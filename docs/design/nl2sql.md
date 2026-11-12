# Question to SQL

**Status: built** (the owner's Phase 3), except what needs a model key: a real model has answered the ten check questions once (Google Gemini through `BI_OPENAI_BASE_URL`, 2026-11-11, `plan.md` Phase 3); OpenAI's own models have not been run and the golden set is not written, so accuracy is not yet measured (§8). Everything else here is tested: the guard by 154 cases, the pipeline with a scripted model, and the database's own locks, tenant isolation, timeouts and row limits against a real PostgreSQL. The vocabulary the model is given is in `semantic-layer.md`; the database roles and row security are in `data-model.md` §2.

## 1. The path of one question

```
question
   │
   ▼
1. build the context      schema description · metric definitions · period words · similar examples
   │
   ▼
2. the model writes SQL   structured output: sql, explanation, assumptions
   │
   ▼
3. the guard checks it    parse → one SELECT → allowed tables, columns, functions → add LIMIT
   │        └── refused: the reason goes back to step 2 (at most twice)
   ▼
3b. the aggregation check does a join repeat the rows that are added up? (§3b)
   │        └── refused: the reason goes back to step 2 (the same two retries)
   ▼
3c. the correctness check five more mistakes provable from the SQL; notes for the answer (§3c)
   │        └── refused: the reason goes back to step 2 (the same two retries)
   ▼
4. run it                 read-only user · company and outlets set on the session · 15 s · 1,000 rows
   │        └── database error: the message goes back to step 2 (same two retries)
   ▼
5. log it                 app.query_log: question, SQL, status, rows, time
   │
   ▼
rows → the report agent
```

Steps 3 to 5 (with 3b and 3c) are the `run_query` tool. The model cannot skip them: it has no other way to reach the database.

## 2. The context the model gets

| Block | From | Size |
|---|---|---|
| The rules | the prompt file `sql_analyst.md` | fixed |
| The schema | **only the tables the question needs**, picked by `semantic/retrieval.py` (§2a), from `semantic/tables.yaml`: each table's purpose, its columns with type and meaning, the allowed values of status and type columns, and the joins between the tables shown. **Only columns the read-only user may select** | usually 1 to 5 of the 14 tables |
| The metrics | the definitions from `semantic/metrics.yaml` whose names appear in the question: the exact SQL expression and its condition | usually 1 to 3 of 16 |
| Dates | today's date in the company's time zone, and what "last month", "this quarter", "YTD", "last 7 days" mean as date ranges | computed by code, not by the model |
| Examples | *not yet*: similar past questions with their SQL, found by vector search, arrive when Qdrant is in use | |
| The conversation | *not yet*: follow-up questions arrive with conversation history | |
| The question | | |

**What the model is not given:** `tenant_id` values, the user's outlet ids, any row of data, any private column.

**The rules in the prompt** (the important ones):

- PostgreSQL 16. One `SELECT`. Qualify nothing with a schema; the search path is `biz`.
- **Do not filter by `tenant_id` or by the user's outlets.** The database does that. (A filter the model writes could be wrong; the database's cannot be bypassed.)
- Use the metric definitions exactly. Revenue counts completed orders only.
- Name the columns you need; never `SELECT *`.
- Use `order_date`, `expense_date`, `movement_date` for periods. Use the date ranges given, do not compute "last month" yourself.
- Give result columns readable names (`revenue`, `orders`, `outlet`).
- Round money to two places. Guard divisions with `NULLIF`.
- Ordered results for rankings; a `LIMIT` when the question says "top N".
- If the question cannot be answered from these tables, say so in `explanation` and return no SQL.

## 2a. Choosing the relevant schema (`semantic/retrieval.py`)

The whole schema is not sent. For each question:

1. The question's words (lower-cased, with simple singular forms) are matched against each table's `keywords` and each metric's `names`.
2. A matched metric brings the tables it needs (they are required, not just likely).
3. The tables that sit **between** the chosen ones in the join graph are added, so the model is never shown two tables it cannot join. Example: *revenue by category and outlet* gets `categories`, `products`, `order_items`, `orders`, `outlets`.
4. At most six tables are chosen before bridging. A question that matches nothing gets the four core tables (`orders`, `order_items`, `outlets`, `products`).

| Question | Tables shown | Metric |
|---|---|---|
| What was our total revenue last month? | `orders` | revenue |
| Which outlet had the lowest revenue? | `outlets`, `orders` | revenue |
| Show the top 10 selling products. | `products`, `orders`, `order_items` | units_sold |
| How many customers visited this month? | `customers`, `customer_visits` | visiting_customers |

It is plain word matching: no model call, no vector search, the same answer every time, and a test can say exactly why a table was chosen. Its weakness is a question that uses none of the listed words; the fallback set covers the common case, and the keyword lists are data that can be extended. A vector search can replace `relevant()` later without touching its callers.

**The guard does not depend on this choice.** It allows any of the 14 tables, so a model that reaches for a table it was not shown is still checked against the full allow-list, not trusted.

**Agents skip the choice.** A domain agent (`agents.md` §2a) passes a vocabulary already cut down to its own four to seven tables, with `whole_vocabulary=True`: all of them are shown. Choosing among so few by word matching only loses tables: "Which outlet performed best this month?" names no sales word, and the real-model run declined it until this was changed.

## 3. The guard (`backend/app/sqlguard`)

A pure function: `check(sql) → Checked(sql=…) | Refused(code, reason)`. It parses with `sqlglot` (PostgreSQL dialect) and works on the syntax tree. What runs afterwards is the SQL **printed back from the tree**, so nothing the parser did not understand can reach the database.

| # | Rule | Refusal code |
|---|---|---|
| 1 | It parses, and it is exactly one statement | `parse_error`, `multiple_statements` |
| 2 | The statement is a `SELECT` (plain, with CTEs, or a `UNION`/`INTERSECT`/`EXCEPT` of selects). No `INSERT`, `UPDATE`, `DELETE`, `MERGE`, DDL, `COPY`, `CALL`, `DO`, `SET`, `SHOW`, `EXPLAIN`, anywhere, including inside a CTE | `not_select` |
| 3 | No `SELECT … INTO`, no `FOR UPDATE` / `FOR SHARE` | `not_read_only` |
| 4 | Every table is on the allow-list (the 14 `biz` tables of the vocabulary) or is a CTE defined in the same statement. A schema prefix other than `biz` is refused, so `app`, `pg_catalog` and `information_schema` are out | `table_not_allowed` |
| 5 | Every column exists and is one the caller may read. The guard resolves each column to its table with sqlglot's own resolver against a catalog that **does not contain** private columns or `tenant_id`, so `customers.phone` is simply unknown, wherever it appears (select list, `WHERE`, `ORDER BY`, a subquery, a CTE). An ambiguous column in a join is refused too | `column_not_allowed` |
| 6 | No `*` anywhere (it would pull columns nobody asked for and hide what is being read). `COUNT(*)` is fine | `star_not_allowed` |
| 7 | Every function is on the allow-list: aggregates (`sum`, `count`, `avg`, `min`, `max`), window functions, date functions (`date_trunc`, `extract`, `to_char`, `age`, `make_date`), math (`round`, `abs`, `ceil`, `floor`, `greatest`, `least`), text basics (`lower`, `upper`, `concat`, `length`, `trim`), `coalesce`, `nullif`, `cast`, `generate_series` (with a bounded range). Anything else is refused: this is what keeps out `set_config`, `current_setting`, `pg_sleep`, `pg_read_file`, `dblink`, `lo_import` and whatever a future PostgreSQL adds | `function_not_allowed` |
| 8 | The outermost query ends with a `LIMIT` no larger than the cap: added when missing, lowered when larger, `FETCH FIRST` rewritten. The pipeline asks for **the cap plus one** row, so a result that was cut can be told from one that fits exactly | (rewritten, not refused) |
| 9 | The text is at most 8,000 characters, and nesting at most 6 levels deep | `too_large` |

Each refusal carries a sentence the model can act on ("The column customers.phone is not available. Count or group customers instead.").

**Why an allow-list and not a block-list:** a block-list has to know every dangerous thing; an allow-list only has to know what a business question needs.

**The guard is the first lock, not the only one.** Behind it: the read-only database user has no privilege to write, no access to `app`, no `SELECT` on private columns, and cannot call `set_config`; row security limits rows to the company and outlets; the transaction is `READ ONLY`. Each lock is tested on its own, with the guard switched off (`testing.md` §4).

## 3b. The aggregation check (`backend/app/sqlguard/aggregation.py`)

**Status: built** (the owner's Phase 5.1, Step 2, 2026-11-11). It answers a different question from the guard. The guard asks "is this safe to run?". This asks "would its totals be right?", for one kind of mistake only: a `SUM`, `AVG` or `COUNT(column)` over rows that a join has repeated.

```sql
SELECT SUM(o.total_amount) FROM orders o JOIN order_items i ON i.order_id = o.id
-- every order appears once per line, so its total is added once per line
```

**How it decides.** It reads the tables' unique keys (`tables.yaml`, `unique`; `semantic-layer.md` §2) and the equalities that join the tables of one `SELECT` (in `ON` and in `WHERE`).

| Term | Meaning |
|---|---|
| At most one row | Table B gives at most one row for a row of table A when every column of one of B's unique keys is equal to a column of A, of a table already settled this way, or to a constant |
| Grain | The tables from which every other table in the `SELECT` is reached like that. Their rows are never repeated |
| Sound | An aggregate that reads at least one grain table |
| Refused | An aggregate that reads no grain table: its rows may be repeated. Code `unsafe_aggregation` |

| Query | Verdict | Why |
|---|---|---|
| `SUM(orders.total_amount)` joined to `order_items` | refused | `order_items.order_id` is not a key: several lines per order |
| `SUM(order_items.line_total)` joined to `orders`, `products`, `outlets` | passed | each is reached on its `id` |
| `orders` joined to `customer_visits` on `outlet_id` or `customer_id`, then `SUM` | refused | neither side is matched on a key |
| `orders` joined to `customer_visits` on `order_id` | passed | `customer_visits.order_id` is unique: one to one |
| `expenses` joined to `orders` on `outlet_id`, then `SUM` | refused | no grain |
| `customers` joined to both `orders` and `customer_visits`, then `SUM` or `COUNT(col)` | refused | no grain |
| `order_items` joined to `inventory_levels` on `outlet_id` **and** `product_id` | passed | the whole key of `inventory_levels` |
| the same join on `product_id` alone | refused | half a key: one row per outlet |
| `SUM(inventory_levels.quantity_on_hand * products.cost_price)` | passed | the stock row is the grain |
| `MIN`, `MAX`, `COUNT(DISTINCT …)`, `SUM(DISTINCT …)`, `COUNT(*)`, window functions, a `SELECT` with one table | never judged | repetition does not change them, or there is nothing to decide |

**What it does when refusing.** Nothing but say so. It returns the code and a sentence naming the aggregate, the table whose rows repeat, the tables that repeat them, and four ways to fix it (add up the line table; one subquery per figure; `EXISTS` or `IN` for a filter; join on the whole key). `sql_analyst.py` logs the attempt as `rejected` with reason `unsafe_aggregation` and sends the sentence back to the model through the same repair loop as a guard refusal (§5). After three refusals the user gets the usual "could not build a query" message, and no figure.

**Rules it keeps.**

- It runs only on SQL the guard has already passed, and reads the guard's own output. It cannot let through anything the guard refused.
- It never rewrites SQL. The query that runs is byte for byte what the guard returned.
- It knows nothing about the caller. Company and outlet scope are untouched.
- It is pure, like the guard: no connection, no import from the rest of the app.

**When it cannot be sure, it passes the query** and records why (`Passed.uncertain`):

| Not judged | Why |
|---|---|
| A subquery, CTE or set-returning function in `FROM` | its rows have no declared key. (This is also the form the prompt asks for, one subquery per figure.) Each subquery's own `SELECT` is still judged |
| Tables tied by `OR`, an inequality, a cast or any other expression | not a plain column equality |
| A `NATURAL` join | the columns are not written |
| A table with no `unique` entry | nothing to reason from |
| SQL it cannot analyse | passed, never crashed on |

**What it does not catch.** Passing this check does not mean a query is right.

| Mistake | Example from the demo data |
|---|---|
| Rows dropped by an inner join | revenue with `orders` inner-joined to `customer_visits` on `order_id`: 168,822.00 instead of 293,227.50, because orders with no recorded visit vanish |
| A valid join that answers a different question | sales of products that are low on stock at *some* outlet, when the user meant at the outlet that sold them |
| A wrong period, filter, metric or grouping | "this month" read as the last 30 days |
| `COUNT(*)` over a fan-out | counting order lines when orders were asked for |
| A fan-out hidden inside a subquery joined back | covered only where the subquery's own `SELECT` shows it |
| A column that is unique in fact but not declared | it is refused (the model is told to use the key); none is known today |

Since Phase 5.1 Step 4 the correctness check (§3c) refuses three of these (`SUM(DISTINCT)`, a LEFT JOIN undone by WHERE, and `COUNT(*)` where no table keeps its rows). The rest are what the golden question set is for (`testing.md` §5): it holds each as a stored trap with the wrong figure it gives and what each check says about it.

**Evidence (2026-11-11).** All 43 queries the real model had run successfully in earlier phases were replayed through the check: 41 passed, 2 were refused, and those 2 are exactly the two that had returned wrong figures (483,402 and 9,557,136). No correct query was refused. Tests: `testing.md` §3.

## 3c. The correctness check (`backend/app/sqlguard/correctness.py`)

**Status: built** (the owner's Phase 5.1, Step 4, 2026-11-12). It runs third, after the guard and the aggregation check, and only on SQL both have passed. It refuses five mistakes that can be proved from the SQL alone, and adds plain notes to the answer's `assumptions`. It never rewrites SQL and never changes a figure. The guard is not edited. The aggregation check gives the same verdict as before on every query (§3b; proved by `test_aggregation_unchanged.py`); the one change to its file is that its grain calculation is now a public function, `relations()`, so this check can use it instead of copying it.

**When it is unsure it passes the query** and records why (`Reviewed.uncertain`). A refusal is issued only when the aliases and joins were resolved and the mistake is clear.

| Code | Refused | Measured on the demo data (Brew & Bite, October 2026) |
|---|---|---|
| `sum_distinct` | `SUM(DISTINCT x)` or `AVG(DISTINCT x)` | 260,016.00 instead of 902,538.00: 1,664 orders hold only 343 distinct amounts |
| `left_join_undone` | a LEFT (or RIGHT) JOIN whose unmatched rows the WHERE, or a later inner join, can never keep | "products with no sale" returns 0 rows instead of 2; "every outlet" returns 0 outlets instead of 5 |
| `missing_status_filter` | a `SUM` or `AVG` of sales amounts that no order-status condition actually limits | 962,184.00 instead of 902,538.00 for orders; 986,830.00 instead of 925,700.00 for order lines |
| `timestamp_period` | a period that ends on a timestamp with `BETWEEN` or `<=` a date | 1,601 orders instead of 1,664: the whole of the 31st is lost |
| `count_without_grain` | `COUNT(*)` returned from a join in which no table keeps its rows | counts pairs of unrelated rows |

### How SQL is read

SQLGlot only, on the guard's own output, with columns qualified against the catalog. No text matching. Each SELECT is judged on its own: the outer query, every CTE, every subquery, each arm of a UNION. A table is known by its alias inside its own SELECT; a self-join is two aliases; an alias reused in an inner query is a different table there. A column of an outer query is an unknown value.

### `sum_distinct`

`DISTINCT` removes equal *values*, not repeated *rows*: two different orders for the same amount are counted once.

| | |
|---|---|
| Refused | `SUM` or `AVG` with `DISTINCT`, reading at least one column, in a SELECT that is not grouped by a whole unique key of that column's table |
| Passed | `COUNT(DISTINCT …)`, `SELECT DISTINCT`, `DISTINCT ON`, `MIN`/`MAX(DISTINCT)`; `SUM(DISTINCT o.total_amount) … GROUP BY o.id` (one order per group, so only a join's repeats are removed) |
| Uncertain | the column comes from a CTE or subquery |

### `left_join_undone`

For each table R brought in by LEFT JOIN, by its own alias, the **whole** WHERE is evaluated for the case "R had no matching row", in SQL's three-valued logic. Every sub-expression gets the set of values it could take (true, false, null):

| Expression | Value when R has no row |
|---|---|
| a column of R; a cast, arithmetic, `LOWER`, `UPPER`, `TRIM`, `ROUND`, `ABS`, `DATE_TRUNC`, `EXTRACT` over one | null |
| `=`, `<>`, `<`, `<=`, `>`, `>=`, `BETWEEN`, `IN (list)`, `LIKE`, `ILIKE`, `SIMILAR TO` with a null operand | null |
| `IN (subquery)` with a null operand | null or false |
| `X IS NULL` / `X IS NOT NULL` where X is null | true / false |
| `AND`, `OR`, `NOT` | the standard three-valued tables |
| anything else: another table's column, `COALESCE`, `CASE`, `NULLIF`, `IS [NOT] DISTINCT FROM`, a subquery, an unlisted function | unknown (true, false or null) |

It is refused only when *true* is not among the possible values: the unmatched row can never be kept, so the LEFT JOIN has no effect. A column of R appearing somewhere is never, by itself, a reason.

| WHERE | Verdict |
|---|---|
| `r.x = 1`; `r.x IS NOT NULL`; `r.x NOT IN (1, 2)`; `r.x BETWEEN 1 AND 5 AND t.name = 'A'` | refused |
| `r.id IS NULL AND r.d = DATE '…'` (can return nothing) | refused |
| `NOT (r.x = 1 OR t.name = 'A')` (null OR anything is never false, so NOT of it is never true) | refused |
| `r.id IS NULL` (the anti-join) | passed |
| `r.x = 1 OR r.id IS NULL`; `r.x = 1 OR t.name = 'A'`; `NOT (r.x = 1 AND t.name = 'A')` | passed |
| `COALESCE(r.x, 0) = 0`; `CASE …`; `r.x IS DISTINCT FROM 1` | passed |
| the same condition written in the join's `ON`; a condition in `HAVING` | passed |
| a FULL, NATURAL, USING or LATERAL join beside a WHERE | uncertain |

The reason sent back offers both fixes and does not choose: move the condition into `ON` if unmatched rows must be kept, or write `JOIN` if they need not be.

**A later inner join (Phase 5.1, Step 5).** The same proof is applied to the `ON` of every INNER JOIN written after the LEFT JOIN, when that `ON` uses the left-joined table.

| Joins | Verdict |
|---|---|
| `outlets t LEFT JOIN orders r … JOIN customers c ON c.id = r.customer_id` (the later join needs a row of `r`) | refused |
| `… LEFT JOIN expenses e … JOIN expense_categories ec ON ec.id = e.category_id` | refused |
| the later join written as LEFT JOIN too; or its `ON` can still be true (`… OR r.id IS NULL`, `COALESCE`) | passed |
| `outlets t LEFT JOIN orders r … INNER JOIN expenses e ON e.outlet_id = t.id` (the later join is on the *preserved* table) | passed |

The last row is deliberate. That LEFT JOIN still works: an outlet with expenses and no orders is kept. What is lost is outlets with no expenses, and whether that is wrong depends on the question ("every outlet" or "outlets that spent"). A LEFT JOIN and an INNER JOIN in one query is never refused for that alone; it stays with the golden set (H3).

### `missing_status_filter`

The rule is that a sales figure must carry a **decision** about order status that **actually limits the rows added up**. It does not impose "completed".

*Judged:* each `SUM` or `AVG` (not a window function) over a sales column: `orders.total_amount`, `subtotal`, `tax_amount`, `discount_amount`; `order_items.line_total`, `quantity`, `discount_amount`, `unit_price`, `unit_cost` (`tables.yaml`, `sales`).

*The governing orders alias:* the orders alias itself; or, for order lines, the base `orders` alias linked to *that same* lines alias by `o.id = i.order_id` as a top-level AND term of a join's `ON` or of `WHERE`.

*A restriction:* a top-level AND term pinning that alias's status to literals: `= 'x'`, `IN (…)`, `<> 'x'`, `NOT IN (…)`, `NOT (… = 'x')`, an `OR` whose every branch does so, the same on `LOWER`/`UPPER`/`TRIM` of it.

| Effective, accepted | Why |
|---|---|
| in the `WHERE` of the same SELECT | WHERE removes the row |
| in the `ON` of an INNER JOIN | an inner join's ON filters like WHERE |
| in the `ON` of an outer join, when the **summed** table is the side filled with NULLs (`orders o LEFT JOIN order_items i ON … AND o.status = …`; `outlets t LEFT JOIN orders o ON … AND o.status = …`) | a failing row becomes NULL and SUM ignores it |
| `SUM(…) FILTER (WHERE o.status = …)`; `SUM(CASE WHEN o.status = … THEN x END)`, also `ELSE 0` | the aggregate is itself conditional |
| `o.status` is a GROUP BY key | the figure is shown per status |
| for order lines: `EXISTS (… WHERE o2.id = i.order_id AND <restriction>)`, `i.order_id IN (SELECT o2.id … WHERE <restriction>)`, and their NOT forms, as top-level WHERE terms | the lines' own orders are consulted |

| Not effective, refused | Why |
|---|---|
| order lines with no orders linked at all | every order's lines are added up |
| the restriction in the `ON` of an outer join whose **preserved** side is the summed table (`order_items i LEFT JOIN orders o ON … AND o.status = 'completed'`) | a LEFT JOIN keeps every line; cancelled and refunded lines are still summed (986,830.00) |
| an `OR` with a branch that lets other rows through | |
| status only selected or ordered by; `IS NOT NULL`; `status = status`; `LIKE '%'` | nothing is pinned |
| the restriction on another alias (a self-join), in an EXISTS that is not correlated to these lines on the order id, in an outer query, or in another CTE | it governs other rows |

| Uncertain, passed | |
|---|---|
| lines joined to a subquery or CTE of orders instead of the base table | |
| status compared with something that is not a literal | |
| a `CASE` in another form; EXISTS or IN under `OR` or nested deeper | |
| an outer `SUM` over a column of a CTE that returns raw order rows | not judged |

Other statuses stay possible: "value of refunded orders", "revenue excluding cancelled", "all orders whatever their status" and "revenue by status" all pass, and a note says which statuses were counted (N3 below).

**Two rules meeting.** `order_items i LEFT JOIN orders o … WHERE o.status = 'completed'` is effective for this rule, but that WHERE also makes the LEFT JOIN an inner join, so `left_join_undone` refuses it first and asks which is meant. No figure is lost either way.

### `timestamp_period`

Columns `ordered_at`, `visited_at`, `moved_at` (timestamptz, on tables that also have a date column for periods).

| | |
|---|---|
| Refused (the end loses the last day) | `ts BETWEEN a AND b`, `ts <= b`, `ts = b` where b is a date, or a timestamp at exactly 00:00:00 |
| Passed (reliable) | the half-open range `ts >= start AND ts < next_period_start`; `ts >= start`; `ts < end`; the date column; `CAST(ts AS DATE) BETWEEN …`; `DATE_TRUNC('month', ts) = …`; `EXTRACT(HOUR FROM ts)`, `DATE_PART(…)` |
| Uncertain (passed, never called complete) | an inclusive end with a time of day (`<= '2026-10-31 23:59:59'`: fractions of the last second may be missed); an end that is not a literal |

### `count_without_grain`

Uses `aggregation.relations()`. *Grain* is the table whose rows the join does not repeat.

| | |
|---|---|
| Refused | the analysis completes, the SELECT has two or more tables, **no** table is at the grain, and `COUNT(*)` or `COUNT(constant)` is returned in the SELECT list (`orders JOIN customer_visits ON outlet_id`; a customer's orders and visits; a cross join) |
| Passed | a grain exists (`orders JOIN order_items` counts order lines: a real count); `COUNT(*)` only in HAVING or ORDER BY; a window count; one table |
| Uncertain | the grain cannot be decided (a subquery or CTE in FROM, joins on OR or an inequality) |

**Known false positive.** A user may want the number of joined rows on purpose, for example how many outlet and product combinations exist (`outlets CROSS JOIN products`). That query is refused. The reason sent back says so and names the way through: count each table separately, or count over a subquery that returns the combinations.

### Notes (never a refusal)

Notes are appended to the answer's existing `assumptions` list, after the model's own. No response field was added; the SQL, the rows and the figures are untouched; the notes hold no numbers.

| Note | When | Text |
|---|---|---|
| N1 | an INNER join through a column that may be empty, while that table's rows are added up or counted (`orders JOIN customers ON c.id = o.customer_id`) | "Orders with no recorded customer are not included." |
| N2 | an INNER join to an optional **one-to-one** record, while the other table's rows are added up or counted (`orders JOIN customer_visits ON cv.order_id = o.id`) | "Orders with no recorded customer visit are not included." |
| N3 | the status decision is not exactly `completed` | "Counts refunded orders only." · "Counts orders with status: cancelled, refunded." · "Includes orders of every status: completed, cancelled, refunded." · "Results are shown separately for each order status." |

- N1 and N2 are given only for columns marked `optional` in `tables.yaml`, joined by a plain equality to the other table's unique key. A LEFT JOIN leaves nothing out and gets no note; an ordinary join gets none.
- N2 is given only when the optional column is also, alone, a unique key in the metadata. A live test checks that against PostgreSQL: the column is nullable, a UNIQUE constraint exists on exactly `(tenant_id, column)`, and no value repeats within a company. A one-to-many link is never described as one-to-one.
- N3 is built from the actual predicate and the status values the database allows (`<> 'cancelled'` becomes "completed, refunded"). A value the database does not allow is never named; that condition gets no note.
- At most two join notes and one status note per SELECT.

### What it does not catch

| Still wrong, still passed | Example |
|---|---|
| An inner join that drops rows where a match is merely absent, not optional | stock joined to movements: 5 products listed instead of 21; revenue joined to expenses: 0 outlets instead of 5 |
| The inner join to visits used for total revenue | 544,004.50 instead of 902,538.00. It now carries note N2; it is not refused, because the same SQL is right for "orders that had a visit" |
| `COUNT` of the wrong record where a grain exists | order lines (3,420) when orders (1,664) were asked for |
| A count of orders with no status condition | only SUM and AVG of sales columns are judged |
| A model listing all three statuses for plain "revenue" | noted (N3), not refused |
| A wrong period, a wrong metric, a join that answers another question | |
| Anything classed as uncertain, and anything inside a joined subquery | by design |

`missing_status_filter` and `timestamp_period` are preventive: their effect is measured on data, but no model has yet been seen making those mistakes. No check here proves a query right.

**Evidence (2026-11-12).** The 60 golden queries and the 43 queries a real model ran in earlier phases: none refused. The 23 golden traps: 13 stopped by the aggregation check, 7 by this check, 3 by neither (`testing.md` §5). Tests: `testing.md` §3.

## 4. Running it

```
connection from the read-only pool (user bi_readonly, search_path = biz)
BEGIN READ ONLY
SET LOCAL statement_timeout = <BI_QUERY_TIMEOUT_SECONDS>
SET LOCAL app.tenant_id  = <from the session>
SET LOCAL app.outlet_ids = <'*' or the user's outlets>
<checked SQL>          fetch at most BI_QUERY_MAX_ROWS + 1 rows (the extra one tells us it was cut)
ROLLBACK
```

- **The result** is `columns` (name and a simple type: `text`, `number`, `money`, `date`, `percent`), `rows`, `row_count`, `truncated`, `duration_ms`. Values are converted to JSON-safe forms (decimals to numbers, dates to ISO text). A text cell longer than 500 characters is cut.
- **Money columns** are recognised by the metric they come from or by name (`revenue`, `amount`, `total`, `cost`, `profit`, `price`), so the screen can format them in the company's currency.
- **Errors** from PostgreSQL are turned into a short reason for the model (the message, not the internal detail) and a generic sentence for the user.
- **A timeout** is its own status. The user is told to narrow the period; the model is not asked to retry the same thing.

**As built** (`store/analytics.py`, `store/postgres.py`): `run_checked_sql` is the only function that executes model-written SQL. It opens `tenant_session(caller)` on the read-only pool, which issues `SET LOCAL statement_timeout`, `SET LOCAL app.tenant_id` and `SET LOCAL app.outlet_ids` (the ids are rebuilt from UUID objects before they are placed in the statement), runs the text with **no parameters**, and fetches at most the cap plus one row. A cancelled statement becomes `QueryTimeoutError`; any other PostgreSQL error becomes `QueryFailedError` carrying the SQLSTATE and the primary message for the model. A connection failure is left to the session, which reports 503.

## 4a. The explanation (`agents/summarizer.py`)

After the query runs, the question and the result (the first 30 rows, the column types, the row count, the currency) go to the model, which returns one to three sentences.

| Rule | How it is enforced |
|---|---|
| Only numbers from the result | **checked by code after the model answers**: every figure in the text must equal a value of the result (allowing for rounding and digit grouping), the row count, a rank up to the row count, or a number the question itself contained. Dates are ignored |
| No computed numbers | a difference, a sum or a percentage the model worked out is not a value of the result, so it fails the same check |
| A failed check | the model is asked once more with the reason; if it fails again, a plain sentence built by code from the first row replaces it |
| No data | the model is **not asked**. The answer is "No data matched this question." built by code, so nothing can be invented |
| The model fails | the data is still returned, with the sentence built by code |
| A user limited to some outlets | the scope ("Limited to your outlets: …") is appended by code, not left to the model |

## 5. Self-correction

| What came back | What happens |
|---|---|
| Refused by the guard | the code and the reason go back to the model with the original question; attempt 2, then 3 |
| Refused by the aggregation check (§3b) | the same; logged as `rejected`, reason `unsafe_aggregation` |
| Refused by the correctness check (§3c) | the same; logged as `rejected`, with one of its five codes |
| PostgreSQL error (unknown column, type mismatch, grouping error) | the same |
| Timeout | no retry; the user is told |
| Zero rows | no retry; an empty result is a valid answer. (One exception: if the SQL has a text equality on a name, the analyst may retry once with a case-insensitive match and says so) |

Every attempt, run or refused, is a row in `app.query_log`.

## 6. Learning from use

1. A user gives an answer a thumbs-up. If the answer came from a single query, the question and its SQL are saved in MongoDB `examples` as **pending**, for that company only.
2. An admin reviews pending examples (a list with the question, the SQL and a "run it" button) and **approves** or rejects each one.
3. An approved example is embedded and stored in Qdrant `sql_examples` with the company's `tenant_id`. From then on it is offered to the model for similar questions **from that company only**.
4. A thumbs-down stores the comment with the message and shows it in the admin's review list. Nothing is learned from it automatically.

Why approval and not automatic learning: an example is trusted input to every later prompt. A wrong one that was liked by mistake would quietly teach the wrong SQL.

The examples shipped with the product (`semantic/examples.yaml`, about 40) are stored with `tenant_id = global` and are offered to every company. They never contain a company's data.

## 7. Known hard cases, and what the design does

| Case | Handling |
|---|---|
| "Last month" at a month boundary and time zones | the date ranges are computed by code in the company's time zone and given to the model; `order_date` is already a local date |
| "Sales" could mean revenue or units | the metric file says: *sales* means revenue unless the question says units or quantity; the answer states which |
| "Low sales" has no threshold | the metric file defines it: the bottom 10% of active products by units in the period, or under 5 units a month; the answer states the rule used |
| Product or outlet named loosely ("the Indiranagar store") | the prompt asks for a case-insensitive `ILIKE` match on names; the answer shows the matched name |
| A question about a column that does not exist ("staff performance") | the model returns no SQL and an explanation; the user is told what data exists |
| A question about customers by name | refused by rule 5 with a helpful reason; the answer explains that personal details are not available |
| A very wide result | `LIMIT 1000`; the answer says the table was cut and suggests grouping |
| Comparing two periods | one query with conditional sums, as in the shipped examples, so both figures come from the same run |

**A join that repeats rows (found with a real model, 2026-11-11).** Twice the model wrote a query that was safe, ran, and returned a wrong total: it added up each order's total once per order line (483,402 for 207,240), and joined orders to visits through the outlet so that each order counted once per visit (9,557,136 for 88,492). Nothing in the pipeline can see this: the guard checks safety, not meaning, and the number check only confirms that the text repeats the result. Rule 13 of the prompt now forbids adding up a column across such a join and asks for one subquery per figure; with it the same questions returned the right totals. A rule in a prompt is a request, not a lock. A check in code (refuse a query that sums a column of one table across a join to another table with several rows per row, using the joins in `tables.yaml`) is the proper fix and is the owner's to approve; the golden set is what would catch the next one. **Since Phase 5.1 this is a check in code as well as a prompt rule (§3b):** both queries are now refused before they run.

## 8. What is not verified, and what is not built

| Item | State |
|---|---|
| **A real model writing SQL** | `llm/openai.py` is written and its error handling is unit-tested with the provider's call replaced, and it has answered real questions through Google Gemini's compatible address (2026-11-11: 8 of 8 supported questions correct). It has **not been run on OpenAI's own models**: the account had no credits. Whether a real model's SQL is correct, and how often, is unknown until `make eval` runs the golden question set (`testing.md` §5) |
| The golden question set | not written yet. The five example questions are covered by live tests with the SQL a model is expected to write |
| Example questions in the prompt (few-shot) and learning from feedback (§6) | not built: they need Qdrant and the feedback route |
| Conversation history for follow-up questions | not built |
| Sign-in | not built. In development the caller is named by the `X-Dev-User` header; every other environment refuses the route (`security.md` §2) |
| A cap on the bounds of `generate_series` | not built; the statement timeout stops a runaway series |
