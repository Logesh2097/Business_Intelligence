# Phase 3 — Question to SQL

**Goal:** a data question posted to the API comes back as a correct table and a sentence, safely.
**Read:** `docs/plan.md` Phase 3, `design/nl2sql.md` (all), `design/semantic-layer.md` (all), `design/security.md` §5–7, `design/testing.md` §2, §3, §5.

## Order of work (test after each step)

1. **The guard's test table first** (`testing.md` §3), then `sqlguard/` until it passes.
2. `semantic/`: the three files and the loader; the period words; the tests that tie them to the database.
3. `llm/`: the client and the scripted fake.
4. `tools/run_query`: guard → read-only session → caps → `app.query_log`. The six lock tests with the guard bypassed.
5. `tools/get_metric_definitions`, `tools/find_similar_questions` (Qdrant `sql_examples`, the shipped examples loaded at start).
6. The SQL analyst with two retries; the first report writer; `POST /chat`.
7. `tests/evals/golden.yaml` and `make eval`. Run it; record the score, the failures and the cost in `plan.md`.

## Acceptance

The gate of this phase in `docs/plan.md`. Every box ticked with its evidence, or explained.

## Do not

Let any SQL reach the database without the guard. Put a company id, an outlet id or a private column in a prompt. Add the supervisor or the stream.
