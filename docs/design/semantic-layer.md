# The business vocabulary (semantic layer)

**Status:** `tables.yaml` (14 tables) and `metrics.yaml` (16 metrics) are built, with the loader (`model.py`), the choice of relevant tables (`retrieval.py`), the prompt text (`render.py`) and the period words (`periods.py`). `examples.yaml` is not built yet (it needs vector search). A live test keeps `tables.yaml` equal to the database's column grants, another keeps its `unique` keys equal to the database's constraints, and a third does the same for its `sales` and `optional` entries.

## 1. Why it exists

A database knows columns. People ask about *revenue*, *profit*, *low sales*. If each question lets the model decide what revenue means, two people get two answers. So the meaning of each business word is written **once**, in a file, and given to the model every time. The same definitions are used by the insight agent's fixed queries and by the tests.

Three files:

| File | Holds |
|---|---|
| `tables.yaml` | each table and column the model may use: purpose, type, meaning, allowed values, joins |
| `metrics.yaml` | each business word: its SQL expression, its conditions, its unit, its other names |
| `examples.yaml` | example questions with correct SQL |

## 2. `tables.yaml`

```yaml
- table: orders
  purpose: One row per customer order (a bill).
  date_column: order_date
  columns:
    - {name: id, type: uuid, meaning: Order id}
    - {name: outlet_id, type: uuid, meaning: The outlet that took the order, joins: outlets.id}
    - {name: customer_id, type: uuid, meaning: The customer; empty for a walk-in, joins: customers.id}
    - {name: order_date, type: date, meaning: The local calendar date of the order. Use this for periods}
    - {name: channel, type: text, values: [dine_in, takeaway, delivery, online]}
    - {name: status, type: text, values: [completed, cancelled, refunded], meaning: Only completed orders count as sales}
    - {name: total_amount, type: money, meaning: What the customer paid, after discount, including tax}
    # …
```

Each table also has `keywords`: the words in a question that point at it. They drive the choice of which tables the model is shown (`nl2sql.md` §2a). `joins` name the column and the table it leads to; they are both shown to the model and used to find the tables that bridge two others.

Rules:

- Only columns granted to the read-only database user are listed. A test compares this file with the database's own grants and column comments and fails when they differ.
- `tenant_id` is **not** listed. The model has no reason to know it exists.
- `values` lists every allowed value of a status or type column, so the model never guesses a spelling.

**One agent's part of it.** `Vocabulary.restricted_to(tables)` returns the same vocabulary cut down to a list of tables: those tables, the joins between them, and the metrics that need no other table. A domain agent (`agents.md` §2a) is given that, so its prompt and the guard's allow-list hold its own tables only.

### Unique keys (`unique`)

Each table lists its unique keys, each a list of columns: `unique: [[id], [order_no]]`. No two rows share the same values in all the columns of one key. They are the database's own PRIMARY KEY and UNIQUE constraints without `tenant_id` (row security already limits every query to one company, so inside a query the rest of the key is unique by itself).

| Table | Unique keys |
|---|---|
| `outlets` | `id`; `code` |
| `categories`, `expense_categories` | `id`; `name` |
| `products` | `id`; `sku` |
| `orders` | `id`; `order_no` |
| `customer_visits` | `id`; `order_id` (at most one visit per order; empty for a visit with no purchase) |
| `inventory_levels` | `(outlet_id, product_id)`. It has no `id` |
| `purchase_orders` | `id`; `po_no` |
| `order_items`, `customers`, `suppliers`, `stock_movements`, `purchase_order_items`, `expenses` | `id` |

They are not shown to the model as data; prompt rule 13 states the ones that matter. They are read by the aggregation check (`nl2sql.md` §3b), which uses them to tell a join that repeats rows from one that cannot. A wrong entry here would make that check trust a join it should not, which is why a live test compares them with the database.

### Sales columns and optional columns (`sales`, `optional`)

Read by the correctness check (`nl2sql.md` §3c). Both are validated when the application starts: a column that does not exist, a status that is not an allowed value, or a one-to-one note on a column that is not a unique key stops it.

| Entry | On | Says |
|---|---|---|
| `sales: {columns, status, normal}` | `orders` | which columns are sales amounts; `status` is the column that decides whether an order counts; `normal: completed` is what ordinary sales figures count |
| `sales: {columns, via, of}` | `order_items` | its sales columns, and that a line is governed by the status of the order `via: order_id` points at (`of: orders`) |
| `optional: [{column, note, other_side_note}]` | `orders.customer_id`, `customer_visits.order_id` | the column may be empty; `note` is the sentence added to an answer when an inner join through it leaves rows out; `other_side_note` exists only when the column alone is a unique key (a one-to-one link) |

The allowed status values are the `values` already listed on `orders.status`. A live test compares all of this with PostgreSQL: the optional marks with `NOT NULL`, the one-to-one link with the tenant-scoped UNIQUE constraint and with the data, the status values with the CHECK constraint and the data, and the sales columns with their numeric types. A table missing from an agent's vocabulary takes its rules with it: that agent is simply not judged on them.

## 3. The metrics

| Metric | Other names | Definition | Unit |
|---|---|---|---|
| `revenue` | sales, turnover, income | `SUM(orders.total_amount - orders.tax_amount)` where `status = 'completed'` | money |
| `gross_sales` | billed amount | `SUM(orders.total_amount)` where `status = 'completed'` | money |
| `orders` | bills, transactions, order count | `COUNT(*)` of `orders` where `status = 'completed'` | number |
| `average_order_value` | AOV, average bill, ticket size | `revenue / orders` | money |
| `units_sold` | quantity sold | `SUM(order_items.quantity)` on completed orders | number |
| `product_revenue` | sales by product | `SUM(order_items.line_total)` on completed orders | money |
| `cost_of_goods` | COGS | `SUM(order_items.quantity * order_items.unit_cost)` on completed orders | money |
| `gross_profit` | margin (as money) | `product_revenue - cost_of_goods` | money |
| `gross_margin` | margin % | `gross_profit / product_revenue` | percent |
| `expenses` | costs, spend, overheads | `SUM(expenses.amount)` | money |
| `net_profit` | profit, bottom line | `gross_profit - expenses` for the same period and outlets | money |
| `discount_rate` | | `SUM(discount_amount) / SUM(subtotal)` on completed orders | percent |
| `cancellation_rate` | | orders with `status` in (`cancelled`, `refunded`) over all orders | percent |
| `new_customers` | | customers whose `joined_on` falls in the period | number |
| `active_customers` | | distinct `customer_id` with a completed order in the period | number |
| `lapsed_customers` | churned, inactive | customers with a completed order before the last 90 days and none within them | number |
| `stock_value` | inventory value | `SUM(inventory_levels.quantity_on_hand * products.cost_price)` | money |
| `below_reorder` | needs reordering, low stock | rows of `inventory_levels` where `quantity_on_hand <= reorder_level` | list |
| `wastage` | | `SUM(-quantity * unit_cost)` of `stock_movements` where `movement_type = 'wastage'` | money |
| `purchases` | procurement spend | `SUM(purchase_orders.total_amount)` where `status = 'received'` | money |

**Words with a chosen meaning** (stated in the answer whenever they are used):

| Word | Means |
|---|---|
| *sales* | revenue, unless the question says units or quantity |
| *profit* | net profit when expenses are in scope (a company or outlet over a period); gross profit for a product or category, because expenses are not recorded per product |
| *low sales* | active products in the bottom 10% by units sold in the period, or under 5 units per month |
| *top*, *best* | by revenue, unless another measure is named |
| *top selling*, *best selling*, *bestsellers*, *most sold* | by units sold on completed orders: the word names the measure. By revenue only if the question says so. (Before 2026-11-12 the Sales agent's prompt said only the line above, which contradicted the metric note "Top selling products are ranked by units sold"; the real model followed the prompt and ranked by revenue) |
| *walk-in* | an order with no customer |

A definition in this table is the owner's to change: it is a business decision, not a technical one.

## 4. Period words

Computed by code from today's date in the company's time zone, and given to the model as plain date ranges.

| Words | Range |
|---|---|
| today, yesterday | that date |
| this week, last week | Monday to Sunday |
| this month, month to date | the 1st to today |
| last month | the whole previous calendar month |
| this quarter, last quarter | calendar quarters |
| this year, year to date | 1 January to today |
| last N days | the N days ending yesterday |
| the month before last | the whole calendar month before last month |
| last two months | the two full months before this one |
| the same days of last month | the 1st of last month to the same day number as today (or last month's last day, if it is shorter) |
| no period given | see `agents.md` §3 for the defaults |

A comparison ("vs last month") uses the **same number of days** when the current period is not finished: 1–10 November against 1–10 October, and the answer says so.

## 5. `examples.yaml`

About 40 question-and-SQL pairs, covering each metric and each common shape: a total, a ranking, a trend by week and month, a comparison of two periods in one query, a share, a breakdown by two dimensions, a stock list, a customer count, a join across sales and expenses.

```yaml
- question: Which outlet had the highest revenue last month?
  sql: |
    SELECT o.name AS outlet,
           ROUND(SUM(ord.total_amount - ord.tax_amount), 2) AS revenue
    FROM orders ord
    JOIN outlets o ON o.id = ord.outlet_id
    WHERE ord.status = 'completed'
      AND ord.order_date BETWEEN :last_month_start AND :last_month_end
    GROUP BY o.name
    ORDER BY revenue DESC
    LIMIT 1
```

Rules:

- Every example passes the guard and runs against the seed. A test does both for each one.
- Dates are written as named placeholders so an example does not go stale. Code replaces them with real dates before an example is shown to the model or run.
- No example mentions a real company's products or people.

## 6. Keeping it honest

| Check | Where |
|---|---|
| `tables.yaml` equals the database's grants and comments | integration test |
| every `unique` key in `tables.yaml` is a PRIMARY KEY or UNIQUE constraint of the database, and none the model can read is missing | integration test (`test_aggregation_live.py`) |
| a `unique` key names only columns of its table, is not empty and repeats no column | the loader, at start-up (`model.py › validate`) |
| every metric's SQL runs and returns the figure computed independently by the seed generator | integration test |
| every example passes the guard and runs | integration test |
| a change to any of the three files runs the golden question set | `testing.md` §5 |
