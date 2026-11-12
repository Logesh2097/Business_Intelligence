# The golden question set

58 business questions with answers that were verified in PostgreSQL, independently of any model. It measures what the model does; every other test in the project uses a scripted model.

Design: `docs/design/testing.md` §5. Why it exists: `docs/design/nl2sql.md` §3b ("What it does not catch").

## Commands

| Command | Model | What it does |
|---|---|---|
| `make eval-check` | none | Builds `bi_eval`, checks it against the baseline, checks every stored value against PostgreSQL, checks every trap, runs all 58 questions through the application on a scripted model |
| `make eval` | none | Runs nothing. Says what the real-model run would do and how to confirm it, and ends with an error |
| `make eval CONFIRM_REAL_MODEL=1 BI_EVAL_MIN_CALL_INTERVAL_SECONDS=7` | the real one | Asks the real model all 58 questions and reports PASS or FAIL with the cause. Costs model calls. Skipped without a key. The interval is required (see "Pacing") |
| the same with `ONLY=A1-revenue-last-month` | the real one | One question (several: comma-separated) |

Both need PostgreSQL running (`scripts/local-postgres.sh up`). Neither runs in `make ci` or `make test-integration`. The comparison rules, the scope check and the database safety refusals are unit tests and do run in `make ci`.

## Files

| File | Holds |
|---|---|
| `golden.yaml` | the questions, their verification queries and their expected rows |
| `baseline.yaml` | the approved content of `bi_eval`: fixed inputs, fingerprint, counts, totals |
| `database.py` | creates `bi_eval`, checks it against the baseline; the only code that can drop a database |
| `dataset.py` | loads `golden.yaml`; refuses a dataset that contradicts itself or a query that is not scoped |
| `compare.py` | compares a result with the expected rows, the whole result set |
| `runner.py` | runs a question through the application; names the cause of a failure |
| `test_golden_dataset.py` | `make eval-check` |
| `test_golden_eval.py` | `make eval CONFIRM_REAL_MODEL=1` |
| `gate.py` | the one switch that lets a test reach a real model, and the guard installed for every test run |

## A real model cannot be reached by accident

| Layer | What it does |
|---|---|
| The Makefile | `make eval` starts the real-model test only when `CONFIRM_REAL_MODEL=1` is typed on that command line. A variable left exported in the shell does not count |
| The test | `test_golden_eval.py` is skipped unless `BI_EVAL_CONFIRM_REAL_MODEL=1`, which only that Makefile branch sets |
| Every test run | `tests/conftest.py` replaces the one line of the application that reaches the provider with one that refuses unless the confirmation is present. So `make ci`, `make test`, `make test-integration`, `make eval-check` and a bare `pytest` cannot reach a model, whatever key is configured |

`make eval-check` also clears the confirmation on its own command line. The gate is tested in `tests/unit/test_eval_gate.py` by reading what `make` would run (`make -n`), which executes nothing.

## Pacing

A provider allows only so many calls a minute. `BI_EVAL_MIN_CALL_INTERVAL_SECONDS` is the least number of seconds between the starts of two model calls in a real-model run.

- **It has no default.** The code does not know your account's quota. Without the variable the real-model test stops before anything is sent.
- **Choosing it:** 60 divided by the calls your account allows a minute, plus a margin (10 a minute: use 7). `0` switches pacing off and must be written out.
- Every model call is paced, whatever its task. Nothing is ever retried by the harness. Three provider failures in a row still stop the run.
- The scripted model is never paced.
- One call is one provider request: the evaluation's model client is built with its own retries off (`evaluation_llm` in `runner.py`). The application keeps its two retries.
- The results file records the interval and the time spent waiting (`run.pacing`).

`python -m tests.evals.runner dry-run --min-call-interval 0.05` shows the pacing on the scripted model. Results files are never written over; a scripted one has `SCRIPTED` in its name and `"kind": "scripted"` inside.

## The database

`bi_eval` is built by the project's own generator and loader from three fixed inputs: reference date 2026-11-11, seed 42, 120 days. "Today" is 2026-11-11 on every run. The demo database is not used: `make db-seed` replaces it with data for the day it is run.

- Every run rebuilds `bi_eval` and stops if it differs from `baseline.yaml`.
- Only a database named `bi_eval`, carrying the marker this code set, on a local server, in dev or test, can be dropped. Anything else stops with a message.
- The application's settings, `.env` and the demo database are never written.
- Columns the database fills from its clock (`created_at`, `updated_at`) are not reproducible. No question may use them; the loader refuses one that does.

## One case

| Field | Meaning |
|---|---|
| `id`, `group`, `tags` | `A1-…`; the first letter is the group |
| `status` | `verified`, or `pending` with a `pending_reason` and no expected values. A pending case is never run and never counted as a pass |
| `as` | the user who asks. Their company and outlets are looked up, never typed |
| `question` | exactly what is asked |
| `scope` | the company and the outlets the answer covers |
| `period` | the name of the period and its first and last day |
| `metric` | the business definition used |
| `route` | the route and agents expected; `accept` lists other routes that are also right, each with a reason |
| `compare` | `rows` (the result must be exactly these rows), `empty` (no rows), `refusal` (no query may run; `expect_status` lists the right statuses) |
| `parts` | one per agent, or one for `general`: `verify_sql`, `pipeline_sql`, `expected` |
| `interpretation` | what the answer means, in words |
| `answer_must_contain` | figures the written answer must state |
| `traps` | wrong queries a model might write: the figure each gives, the right figure, and whether the aggregation check refuses it |

`verify_sql` runs as the database owner, read-only. The owner is not limited by row security, so every business table in it must carry `<alias>.tenant_id = :tenant`, and for a limited user `<alias>.outlet_id = ANY(:outlets)` (`<alias>.id` for `outlets`). A filter counts only as a top-level AND term of the same SELECT's WHERE, or of the ON of the INNER or LEFT join that brings the table in. Tables are written `biz.<name>`.

`pipeline_sql` is a correct query as a model would write it: no schema, no company. It must pass the guard and the aggregation check and, under row security as the case's user, return the same rows as `verify_sql`.

## How a result is compared

The whole result set, not a sample.

| Rule | |
|---|---|
| Row count | exact |
| Rows | every expected column of every row; a missing, unexpected or repeated row fails and is named |
| Column names | the model's to choose: columns are matched by content. Extra columns are allowed and reported |
| `money` | equal to the cent |
| `count` | exact; never rounded into place |
| `number` | to the places the column declares (default 2) |
| `text` | case and outer spaces ignored |
| `month` | the same month, written as `2026-10`, a date or a timestamp |
| Order | ignored unless `order: enforced`. Then the sequence must match; with `rank_by`, rows that tie on it may swap |

A failure has one cause, the first that applies: `routing`, `execution`, `calculation`, `interpretation`.

## Adding or changing a question

1. Write the case with `verify_sql` as simple as it can be (one table where possible) and `rows: []`.
2. Run `make eval-check`. The failure for your case prints the rows the query returns now.
3. Read them. If they are what the business definition says, copy them into `rows`. If not, the query is wrong: fix the query.
4. Write `pipeline_sql` a different way. `make eval-check` must pass.
5. Change `dataset.total` and the counts in `test_golden_dataset.py`.

Rules that are not to be bent:

- An expected value is the output of its verification query. It is never taken from a model's answer.
- A value is never edited to make a run, a baseline check or a model's answer pass.
- If `baseline.yaml` no longer matches (the generator changed, or Python gives other rows), find out why. Regenerating the baseline is a deliberate act that the owner approves; after it, every expected value fails until it is re-verified.
