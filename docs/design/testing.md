# Testing and verification

**Status:** design. Phase 0 built the unit layer's first tests (configuration, the health routes). Each later phase adds what §7 lists for it.

The rule: **every part is tested on its own, and a phase is done only when its gate is green and the screens have been looked at.** A green build alone is not done.

## 1. Layers

| Layer | Where | Needs | Runs |
|---|---|---|---|
| **Unit** | `backend/tests/unit` | nothing: no database, no network, no model | `make test` in seconds. The SQL guard, the chunker, the chart rules, tokens and hashing, period words, the number check |
| **Integration** | `backend/tests/integration` | real PostgreSQL, MongoDB and Qdrant (throwaway databases whose names contain `test`); the **fake model** | `make test-integration`. Routes end to end, row security, the seed, the tools, ingestion, the graph |
| **Isolation** | `backend/tests/integration/isolation` | the same | part of the integration run; listed separately because it is the suite that must never be skipped (§4) |
| **Evaluation** | `backend/tests/evals` | the same, plus the **real model** and a key | `make eval`, on demand and before any prompt, model or vocabulary change is merged. Costs money; not in the per-commit run (§5) |
| **Frontend unit** | `frontend/src/**/*.test.ts` | nothing | `npm run test` |
| **End to end** | `frontend/e2e` | the whole stack running with the fake model | `make e2e`, a handful of journeys (§6) |

`make ci` = lint (`ruff`), types (`mypy` on `app/`), unit tests, frontend lint, type check, unit tests and build. Integration and end-to-end run in CI with service containers.

**The question pipeline's tests** follow the same split:

| Kind | Files | Proves |
|---|---|---|
| **PURE** | `tests/unit/sqlguard/test_guard.py` (154 cases), `test_semantic.py` | the guard's rules; which tables a question is given; period words |
| **MOCKED** | `test_sql_analyst_mocked.py`, `test_summarizer.py`, `test_analytics_api_mocked.py`, `test_llm.py` | generation, repair, the three-attempt stop, model failures, the number check, the API's shape and who sees the SQL |
| **LIVE** | `tests/integration/test_schema_live.py`, `test_analytics_live.py` | the database's locks with the guard out of the way; the example questions' figures against values computed separately; tenant and outlet isolation; timeout; row limit; the query log |

**The domain agents' tests:**

| Kind | File | Proves |
|---|---|---|
| **MOCKED** | `tests/unit/test_agents_mocked.py` (69 cases) | each agent's prompt and table list; the structured answer; a query outside the agent's tables refused; unsafe SQL refused by the same guard for every agent; insights with invented figures dropped; model and database failures; the agent's time limit; the API (shape, viewer without SQL, 401, 403, 404, 422, 503) |
| **LIVE** | `tests/integration/test_agents_live.py` (48 cases) | each agent's typical queries against real PostgreSQL, figures equal to values computed separately as the owner; each company and each limited viewer gets only its own rows, for every agent; a query outside the agent's tables is refused and logged; 6 attacks × 3 agents change nothing; the API end to end |

**The supervisor's and the graph's tests:**

| Kind | File | Proves |
|---|---|---|
| **PURE** | `tests/unit/sqlguard/test_aggregation.py` (115 cases) | the aggregation check (`nl2sql.md` §3b): 21 fan-out shapes refused (order and purchase totals across lines, two detail tables through outlet or customer, a customer's orders and visits, half the stock key, cross joins, joins in `WHERE`, inside CTEs, subqueries and UNIONs); 5 spellings of one query; 20 sound joins passed and judged; `MIN`, `MAX`, `COUNT(DISTINCT)`, `COUNT(*)`, window functions never refused; CTE, subquery, `OR`, inequality and expression joins passed as uncertain; the query is never changed; missing, absent and wrong key metadata; a bad key stops the loader |
| **PURE** | `tests/unit/sqlguard/test_aggregation_unchanged.py` (184 cases) | the aggregation check gives, word for word, the verdict it gave before Step 4 on 176 recorded queries (its own cases, the golden queries and traps, the 43 real-model queries): refused or passed, the code, the full sentence, the judged count, the uncertain reasons; and the public `relations()` helper |
| **PURE** | `tests/unit/sqlguard/test_correctness.py` (485 cases; 36 added in Step 5 for a LEFT JOIN undone by a later inner join) | the correctness check (`nl2sql.md` §3c): every refused, passed and uncertain form of its five codes in several spellings; the three-valued truth tables; two LEFT JOINs judged one by one, self-joins, chains; status in WHERE, inner ON, outer ON on either side, FILTER, CASE, GROUP BY, EXISTS, IN, with the wrong alias, under OR; other statuses allowed and named; half-open ranges; a count of order lines never refused; the exact note texts and when they are withheld; the query never changed; no correct query we hold refused (103); bad metadata stops start-up |
| **MOCKED** | `tests/unit/test_sql_analyst_mocked.py` (13 more cases, Step 4) | a status condition in a LEFT JOIN's ON never runs and the INNER JOIN repair does; three refusals fail; each of the five codes is refused, logged and repaired; the guard and the aggregation check decide first; notes follow the model's own assumptions and are not repeated |
| **LIVE** | `tests/integration/test_correctness_live.py` (31 cases) | the metadata is the database's (optional columns, the one-to-one link in constraint and in data, status values, numeric sales columns); every refused form really adds up cancelled and refunded lines, every accepted form returns the completed figure exactly; other statuses return their own figures; the other four refusals give their wrong figures and their corrected forms the right ones; each note is true of the rows dropped; in the pipeline: refused before the database, repaired, a viewer's repair inside their outlets, notes in the API response, a viewer still gets no SQL |
| **MOCKED** | `tests/unit/test_sql_analyst_mocked.py` (5 more cases) | a refused sum never runs and goes back to the model with the user's words; three in a row end in `failed`; the security guard still decides first; a sound join runs as the guard left it; the same caller throughout |
| **LIVE** | `tests/integration/test_aggregation_live.py` (26 cases) | the `unique` keys equal the database's constraints; each of 10 refused joins really returns a wrong figure on real data and each of 9 passed joins the right one (against a one-table figure computed as the owner); in the pipeline: refused before the database, repaired, right figure; three refusals give the usual failure; a viewer's repair stays inside their outlets; the query log; an attack is still stopped by the guard |
| **MOCKED** | `tests/unit/test_supervisor_mocked.py` (29 cases) | the four routes; unknown, repeated and missing agents; `general` standing alone; a part that adds a number or is longer than the question; a poor clarification; an invalid decision asked for once more, then undecided; the prompt holds the agents' duties and nothing about the caller |
| **MOCKED** | `tests/unit/test_assistant_graph_mocked.py` (43 cases) | each agent reached alone with the user's words; two and three agents running at the same time; the combined summary and its number check; expenses, purchasing and cross-area questions through `general`; ambiguous and unsupported run nothing; a wrong agent declines, with no second try, and cannot read outside its tables; partial results; every part failing; database errors; the time limit with what finished; every query run as the caller; the graph's shape and no checkpointer; the API, and the two older routes' shapes unchanged |
| **LIVE** | `tests/integration/test_assistant_live.py` (30 cases) | figures through one agent, three agents in parallel and `general` (expenses, purchasing, sales joined to stock, customers joined to sales) equal to the owner's; the same join refused when sent to one agent; each company's own rows through every route; a limited viewer's outlets through `general` and through parallel agents; 6 attacks × 2 routes refused, logged, nothing changed; the query log; the API |

The live tests use their own database, `bi_test`, created, migrated and seeded once per run with "today" pinned to 2026-11-01. In them **only the model is scripted**: the guard, the read-only user, row security, the timeout and the API are real.

**Three kinds of database test, kept apart and labelled at the top of each file:**

| Kind | Where | What it proves | Needs |
|---|---|---|---|
| **MOCKED** | `tests/unit/test_*_mocked.py`, `test_lifecycle.py`, `test_status_api.py`, with the fakes in `tests/unit/fakes.py` | how *our* code behaves when the driver answers or fails: commit, rollback, the error translation, what the status route shows | nothing |
| **REAL DRIVERS, NO SERVER** | `tests/unit/test_no_server.py` | the true failure path of psycopg and PyMongo against an address where nothing listens: fast, bounded, no secret in the answer | nothing |
| **LIVE** | `tests/integration/*_live.py` | a real server: users, transactions, read-only mode, timeouts, pooling | PostgreSQL and MongoDB running |

A live test whose server is not running is **skipped with a message that says so**. With `BI_TEST_REQUIRE_LIVE=1` (set in CI) it **fails** instead, so a missing server can never look like a pass. Live tests that write bring their own scratch table or collection and remove it.

## 2. The fake model

Every test except the evaluations runs with `app/llm/fake.py` in place of OpenAI.

- It is **scripted**: a test says "when asked for SQL, answer this; when asked for the report, answer that". An unscripted call fails the test, so a test cannot silently depend on a model.
- It records every prompt it was given, so a test can assert what was (and was not) sent: no `tenant_id`, no private column, the right date range, the delimited document block.
- Embeddings are faked with a deterministic function (a hash spread over the vector), which is enough to test storage, filters and deletion. Retrieval *quality* is an evaluation, not a unit test.

Why: tests must be fast, free, repeatable and runnable without a key. What the real model does is measured by §5, not asserted by unit tests.

## 3. The SQL guard's tests

The most important unit tests in the project. A table of cases in `tests/unit/sqlguard/`, each a line of SQL with the expected outcome:

| Group | Examples | Expect |
|---|---|---|
| Allowed | every example in `semantic/examples.yaml`; CTEs; window functions; unions | passes; `LIMIT` added where missing |
| Not a select | `DELETE`, `UPDATE … RETURNING`, `DROP`, `INSERT` inside a CTE, `COPY`, `DO`, `SET` | `not_select` |
| Two statements | `SELECT 1; DROP TABLE orders`, with comments and odd spacing | `multiple_statements` |
| Other schemas | `app.users`, `pg_catalog.pg_authid`, `information_schema.tables`, quoted and mixed-case forms | `table_not_allowed` |
| Private columns | `customers.phone` direct, through an alias, inside a subquery, as `c.*` | `column_not_allowed`, `star_not_allowed` |
| Functions | `set_config`, `current_setting`, `pg_sleep`, `pg_read_file`, `dblink`, `lo_import`, `query_to_xml` | `function_not_allowed` |
| Tricks | comments hiding keywords, Unicode look-alikes, `/*!…*/`, dollar-quoted strings, very deep nesting, a 100 kB query | refused, never a crash |
| Property test | random valid selects from a small grammar pass; random mutations that add a forbidden node are refused | (`hypothesis`) |

## 4. The isolation suite

Two demo companies exist in every integration run. The suite proves the "never" list of `security.md` §1.

| Test | Proves |
|---|---|
| As `bi_readonly` with company A set, select from every `biz` table: no row of B | row security, table by table |
| As `bi_readonly` with no company set: zero rows from every table | fails closed |
| As `bi_readonly`: `set_config('app.tenant_id', B)` is refused by the database, with the guard bypassed | the model cannot switch company |
| As `bi_readonly`: any write, any read of `app`, any read of a private column is refused by the database, with the guard bypassed | the second lock stands alone |
| As the owner: insert an order item that names company B's product under company A | the composite key refuses it |
| A viewer limited to two outlets: totals equal the sum of those two outlets, computed independently | outlet scope |
| **The sweep**: every route that takes an id, called as company B's admin with company A's ids (read from the API's own OpenAPI document, so a new route is covered automatically) | `404` everywhere, and A's data unchanged |
| A document search as B for a phrase only A's document contains | nothing |
| Ask, as B, a question whose answer for A is a known figure (story S8) | B's figure, never A's |
| Static: every MongoDB call in `store/` includes `tenant_id` | no unfiltered query can be written unnoticed |

From Phase 8 the suite also runs with the tools behind MCP.

## 5. The golden question set

**Status: built** (the owner's Phase 5.1, Step 3, 2026-11-11): 58 questions, all verified against PostgreSQL, in `backend/tests/evals/`. **Not yet run against a real model** (that is Step 6); the dataset itself is proved by `make eval-check`, which needs no model. How to add a question: `backend/tests/evals/README.md`.

**Why it exists.** Every other test scripts the model. The guard proves a query is safe and the aggregation check removes one kind of wrong total (`nl2sql.md` §3b); neither can tell whether a figure is right. This set can: each question has an answer worked out without the model.

### The database it runs on

The demo database cannot hold fixed answers: `make db-seed` replaces it with data for the day it is run. The evaluation builds its own database, `bi_eval`, with the project's generator and loader.

| Fixed input | Value |
|---|---|
| Reference date, and "today" in every run | 2026-11-11 |
| Generator seed | 42 |
| Period | 120 days (14 July to 10 November 2026) |

| Safeguard | How |
|---|---|
| The data is the approved data | `baseline.yaml` holds the generator's fingerprint, row counts per table and company, key totals, outlets per company and user, and first and last dates. Every run rebuilds `bi_eval`, compares it row for row with the generated rows and with the baseline, and stops on any difference |
| The generator reproduces | checked on 2026-11-11: the same fingerprint in three processes; `bi_eval` and the demo database of that day identical to the generated rows in all 17 tables. A unit test in `make ci` recomputes the fingerprint on the running Python and is never skipped |
| Only `bi_eval` can be dropped | the name is a constant; refused unless dev or test, a local server, not a database the application is configured for, not `bi`, `bi_test`, `postgres` or a template; an existing `bi_eval` must carry the marker this code set |
| The demo database is only read | opened with `default_transaction_read_only = on`; its counts and totals are compared before and after |
| Normal settings are untouched | `bi_eval` is reached through an in-memory copy of the settings; `.env` and `core/config.py` are not changed, and a test confirms it |
| Not reproducible | columns filled by the database clock (`created_at`, `updated_at`). No question may use them |

### One case

```yaml
- id: A2-revenue-by-outlet
  group: A
  status: verified                  # or pending, with a reason and no expected values
  as: analyst@brewbite.example
  question: "What was the revenue of each outlet last month?"
  scope: {company: "Brew & Bite Cafés", outlets: all}
  period: {name: "last month", from: "2026-10-01", to: "2026-10-31"}
  metric: "revenue = SUM(orders.total_amount - orders.tax_amount) on completed orders"
  route: {expect: agents, agents: [sales_agent]}
  compare: rows                     # rows | empty | refusal
  parts:
    - agent: sales_agent
      verify_sql: |                 # owner, read-only, scope written out: the source of truth
        SELECT t.name AS outlet, (SELECT ROUND(SUM(o.total_amount - o.tax_amount), 2) ...
        FROM biz.outlets t WHERE t.tenant_id = :tenant ORDER BY t.name
      pipeline_sql: |               # a correct query as a model would write it
        SELECT t.name AS outlet, ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue ...
      expected:
        columns: [{name: outlet, type: text}, {name: revenue, type: money}]
        rows: [...every outlet with its revenue...]
```

**Where an expected value comes from.** Only from `verify_sql`, run as the database owner in a read-only transaction. It is never taken from a model's answer and never edited to make a run pass. Two things keep it honest: `make eval-check` fails if a stored row is not what the query returns now; and `pipeline_sql`, a second query written a different way, must return the same rows through the guard, the aggregation check and row security as the case's own user.

**Scope of a verification query.** The owner is not limited by row security, so the limits are written into the query and checked by parsing it (`dataset.py › scope_problems`). Every business table, at every level (CTE, subquery, UNION arm), must carry `<alias>.tenant_id = :tenant`, and for a limited user `<alias>.outlet_id = ANY(:outlets)`. A filter counts only as a top-level AND term of the same SELECT's WHERE, or of the ON of the INNER or LEFT join that brings the table in. The check fails closed; 39 ways of looking filtered without being so are unit-tested (under OR, in an outer query, in HAVING, on another alias, in another join's ON, a CTE named like a table, and so on).

### The questions

| Group | Questions | What it covers |
|---|---|---|
| A Sales | 8 | revenue last month, by outlet, by product; top 5 by units; revenue of low-stock products; two outlets compared; completed orders; average order value |
| B Inventory | 5 | low stock; units by outlet; largest shortage; out of stock (an empty result); units received by product |
| C Customers | 5 | visitors last month; visits by outlet; customers with more than one visit; visits in a date range; customers by segment |
| D Purchases | 4 | total; by supplier; by outlet; by month |
| E Expenses | 4 | total; by category; by outlet; two months compared |
| F Cross-domain | 5 | revenue beside shortage; revenue and visits by outlet; sales and purchases by month; revenue per visiting customer; sales and expenses by outlet |
| G Duplicate aggregation | 8 | the seven fan-out joins of `nl2sql.md` §3b, each with its trap, and the safe stock join as a positive control |
| H Missing rows | 4 | orders without visits; stock rows without movements; outlets without expenses; products without sales |
| I Metric definition | 2 | total revenue against product revenue, and the discounts between them |
| J SUM(DISTINCT) | 1 | two real orders of the same amount on one day |
| K Date ranges | 4 | last month; this month; month to date; a named range |
| L Scope and security | 5 | a limited viewer; the other company's viewer; another company's data; customer phone numbers; a delete request |
| M Routing only | 3 | two independent facts; three; an ambiguous question |
| **Total** | **58** | 58 active, 0 pending |

| Expected route | Questions |
|---|---|
| one agent | 29 |
| several agents | 5 |
| general | 21 |
| clarify | 1 |
| unsupported | 2 |

**Traps.** 23 wrong queries a model might write, on 20 of the questions, each stored with the figure it gives on `bi_eval` and what **each** check says about it when asked alone (`aggregation_check`, `correctness_check` with its code, and any note).

| Stopped by | Traps | Which |
|---|---|---|
| the aggregation check | 13 | the fan-out joins |
| the correctness check | 7 | `SUM(DISTINCT)` twice (J1), a LEFT JOIN undone by WHERE (H4), a LEFT JOIN undone by a later inner join (H3, added in Step 5), revenue with no status condition (A1), a LEFT JOIN to orders with the status in ON (I1), a period ended on a timestamp (K1) |
| neither: only this dataset catches them | 3 | inner joins that drop rows (H1, H2, H3). H1 carries its note |

There is one safe positive control, G8, which is not a trap: order lines joined to stock on the whole key must be judged sound. (An earlier version of this page said 20 traps and 14 refusals; the dataset then held 19 and 13. Three traps were added in Step 4 and one in Step 5; no question or expected value changed.)

### How a result is compared (`compare.py`)

The whole result set, not a sample: revenue by outlet passes only if every outlet is there with its own revenue.

| Rule | |
|---|---|
| Row count | exact |
| Rows | all expected columns of every row together; a missing, unexpected or repeated row fails and is named |
| Column names | matched by content, since a model names columns as it likes. Extra columns are allowed and reported |
| Money | to the cent. Counts exact. Numbers to the declared places. Text ignoring case and outer spaces. Months as `YYYY-MM` however written |
| Order | ignored unless the question asks for one. For a ranking the sequence must match and a top-N must have N rows; rows that tie on the ranking value may swap |
| Several agents | each agent's result is compared in full with its own expected rows |

**A failure has one cause**, the first that applies (`runner.py`): `routing` (wrong route or agents), `execution` (a part did not run, or data came back for a question that must be refused), `calculation` (it ran and the rows are not the expected rows), `interpretation` (the rows are right; the text leaves out an expected figure or the viewer's scope note).

### Commands

| Command | Model | What runs |
|---|---|---|
| `make eval-check` | none | baseline; every stored value against PostgreSQL; `pipeline_sql` under row security; every trap; all 58 questions through `workflows.assistant.ask` on a scripted model; the runner's failure causes; isolation, viewer SQL, read-only, demo database untouched. 235 tests |
| `make eval` | none | runs nothing: says what the real-model run would do and how to confirm it |
| `make eval CONFIRM_REAL_MODEL=1 BI_EVAL_MIN_CALL_INTERVAL_SECONDS=<s>` | real | all active questions, with a PASS or FAIL per question and the cause. Skipped without a key. Add `ONLY=<id>` for one. `<s>` is required: see "Pacing" below |
| `make ci` | none | the comparison rules and the runner's verdicts (62 cases), the scope check (53), the database safety refusals and the fingerprint (29) |

### The real model cannot be reached by accident

On 2026-11-11 the real-model run was started by mistake. Since Step 5 three layers prevent it (`backend/tests/evals/gate.py`):

| Layer | What it does |
|---|---|
| The Makefile | `make eval` alone prints what it would do and ends with an error. Only `CONFIRM_REAL_MODEL=1` typed on that command line starts it; a variable left exported in the shell does not count |
| The test | the real-model test is skipped unless that Makefile branch set `BI_EVAL_CONFIRM_REAL_MODEL=1` |
| Every test run | `backend/tests/conftest.py` makes the one line that reaches the provider (`OpenAILLM._invoke`) refuse unless the confirmation is present. This turns rule 9 of `CLAUDE.md` from a convention into something enforced, for `make ci`, `make test-integration`, `make eval-check` and a bare `pytest` alike |

`tests/unit/test_eval_gate.py` (21 cases, in `make ci`) proves it without calling a model: it reads what `make` would run with `make -n`, which executes nothing.

### Documents (the owner's Phase 8)

| File | Kind | What |
|---|---|---|
| `tests/unit/test_rag_pipeline.py` | MOCKED | file detection, extraction from real PDF, DOCX and text bytes, chunking, passage selection, the file store, ingestion, isolation of records and passages |
| `tests/unit/test_qdrant_requests.py` | REAL DRIVERS, NO SERVER | every Qdrant request carries the company filter; a foreign point is dropped; never run against a server |
| `tests/unit/test_document_agent_mocked.py` | MOCKED | the citation and figure checks, the agent, the documents route through the graph, the `/documents` API |
| `tests/unit/test_rag_eval_scripted.py` | MOCKED | `tests/evals/rag.yaml`: 12 answerable and 5 unanswerable questions over three sample documents, with the word-based fake embedder. Measures the pipeline, not a model |
| `tests/integration/test_documents_live.py` | LIVE | Qdrant and MongoDB for real; skipped with the reason when they are not running |

`tests/evals/gate.py` also blocks the real embedding call in every test run.

### Tools and the MCP server (the owner's Phase 9)

| File | Kind | What |
|---|---|---|
| `tests/unit/test_context_token.py` | MOCKED | signing, expiry, forgery, the separate key label |
| `tests/unit/test_tools_registry_mocked.py` | MOCKED | the 14 tools, no company/user/outlet argument, validation, permissions, the time limit, logging without arguments, `run_query` through the real checks, the REST routes |
| `tests/unit/test_mcp_server.py` | REAL PROTOCOL, NO DATABASE | the server on 127.0.0.1, called with the SDK client: discovery, calls, the token refusals, document isolation, concurrent callers |
| `tests/integration/test_tools_live.py` | LIVE | the business tools on PostgreSQL against the owner's own sums; the REST and activity routes; a real `python -m app.mcp_server` process |

### The web app (the owner's Phase 10)

`frontend/src/**/*.test.ts`, 61 tests with vitest: formatting, when a chart is drawn, the API client's error handling, the stores (session, assistant, panels, documents, activity) against a stubbed `fetch`, and `ResultView`. `tests/unit/test_session_api.py` covers `/me` and the development user list. The pages themselves were checked by opening them in a headless browser; that check is not automated.

### Pacing between model calls

**Status: built** (the owner's Phase 6, Step 2, 2026-11-12). **Not yet used with a real model.**

The first real-model run of the golden set (2026-11-12) sent about 22 calls a minute and was stopped by the provider's rate limit after 15 of 58 questions. The harness now spaces calls out.

| | |
|---|---|
| Setting | `BI_EVAL_MIN_CALL_INTERVAL_SECONDS`: the least number of seconds between the starts of two model calls. Read by `tests/evals/runner.py`, not by the application |
| Default | none. Without it the real-model test stops before anything is sent and says how to set it. `0` means no pacing and must be written out |
| How to choose it | from the quota of the account in use, which the code does not know: 60 divided by the calls allowed a minute, plus a margin. A limit of 10 a minute gives 6, so use 7. If the provider also limits tokens a minute or calls a day, the interval must respect those too |
| What is paced | every model call of the run, whatever its task: `route`, `sql`, `sql_repair`, `agent_report`, `summary`, `supervisor_report`. All go through one wrapper. Calls made at the same moment by two agents queue one interval apart |
| What it does not do | it never repeats a call. A failed call stays failed; three provider failures in a row still stop the run; the questions not asked are still listed as not executed |
| The scripted model | not paced: `make eval-check` and the dry run wait for nothing |
| What is recorded | in the results file: `run.pacing` (the interval, calls paced, number of waits, seconds waited), `waited_seconds` on each call, `seconds_waiting` and `seconds_working` on each question. A call's own `seconds` never includes its wait |

**One call is one request** (Step 2.1). The application's model client repeats a failed request up to twice on a connection error or a rate limit (`MAX_RETRIES = 2` in `app/llm/openai.py`, the only place a retry is configured). In an evaluation those repeats would be requests the pacing never sees. So the evaluation builds its client through `evaluation_llm` in `tests/evals/runner.py`, which asks for no retries (`build_llm(settings, max_retries=0)`) and refuses to hand over a client on which any retry is configured. The main model and the fast model are both built that way, and every task uses one of the two. The application itself never passes the option and keeps its two retries. A failed request therefore fails the call at once and reaches the stop rule. The results file records `run.client_max_retries`. For a failed call it also records the error class and the HTTP status number (`provider_errors`, from the application's log line; never the provider's message), so a rate limit (429) can be told from an overload (503). This is not the SQL repair loop: that is the application asking the model again with the reason for a refusal, it is part of what is evaluated, and each of its calls is paced.

Tests: `tests/unit/test_eval_retries.py` (13 cases, in `make ci`). They point the real client at a stand-in provider on 127.0.0.1 that answers 429 and counts requests: the evaluation client sends 1 for a call, the application client 3.

**Results files.** Each run writes one file in `tests/evals/results/` (ignored by git). A file is created with a name no other file has, so an earlier one is never written over. `run.kind` is `real-model` or `scripted`; a scripted file also has `SCRIPTED` in its name and its report starts with "SCRIPTED RUN, not a result of any model".

Tests: `tests/unit/test_eval_pacing.py` (30 cases, in `make ci`, no database, no model).

### Limits

- A scripted run proves the dataset and the runner, not the model. The real-model score is not known until Step 6.
- 58 questions, not the "about 60" first planned; no document or "why" questions, because those features are not built.
- A question with two defensible readings is worded to remove the doubt (for example "at the outlets where they are low"). An ambiguous question cannot be scored fairly.
- A month written as a name ("October 2026") or a comparison returned as one wide row is scored as a calculation failure, though a person would accept it.
- The demo data has no out-of-stock product and no purchase in November; those questions expect an empty result. Stock movements are all of type purchase.
- "This month" and "month to date" are the same dates on the 11th.

## 6. End-to-end journeys

With the whole stack and the fake model:

1. Sign in as an analyst, ask a question, see the steps, the table, the chart and the text; open the query.
2. Sign in as a viewer: the query is not offered; the scope note is shown.
3. Save an answer as a report, open it from Reports, refresh it, export it.
4. As an admin, upload a document, ask about it, see the source.
5. As an admin, create a user limited to one outlet; sign in as them; their total is that outlet's.
6. Sign out; the back button does not show the conversation.

## 7. What each phase must prove

| Phase | Proof |
|---|---|
| 0 | `make ci` green; `/healthz` answers; `/readyz` reports each store truthfully (up and down); the web shell builds and shows the status page |
| 1 | migrations apply to an empty database and are recorded; the seed is deterministic (two runs, the same row counts and checksums); every planted story is present (a test per story); the row-security tests of §4 that need no API |
| 2 | sign-in, refresh rotation and reuse detection, sign-out, token version; the login limit; the permission of every route; the last-admin guard; the sweep |
| 3 | the guard's table (§3); the six database locks each alone; the tools; the query log; the golden set's data groups at 80%, privacy and scope at 100% |
| 4 | the event stream parser; the answer card in each state; journeys 1 and 2; looked at on a desktop and at 360 px, light and dark |
| 5 | conversations and reports routes; a report refreshed under the viewer's scope; export neutralises formula cells; journey 3 |
| 6 | ingestion of each file type; duplicate refused; delete removes the points; the document isolation test; the RAG evaluation of `rag.md` §6; journey 4 |
| 7 | the supervisor's routes on a labelled set (40 questions, 90%); the insight procedure reproduces each planted story from the seed with the fake model; the number check refuses an invented figure; the limits of `agents.md` §8 end a run cleanly |
| 8 | `mcp.md` §5 |
| 9 | the whole isolation suite; the authorization matrix generated and current; the limits; the start-up refusals; `pip-audit` and `npm audit`; the golden set at 90%; a load run (20 users asking at once) inside the time budgets |
| 10 | a deploy to staging from CI; `/readyz` green there; journey 1 against staging with the real model; a restore from a backup, done once and timed |

## 8. Reporting at the end of a phase

The gate checklist from `plan.md` with each box ticked or explained · the commands run and their last lines · screenshots of every new screen (desktop and 360 px, light and dark) · the evaluation score and cost where one was run · anything deferred, with why · questions for the owner. Then the phase stops and waits.
