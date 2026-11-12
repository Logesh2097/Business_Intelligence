# Phase 1 — Data

**Goal:** the business data exists, belongs to a company, and the database itself refuses to show it to anyone else.
**Read:** `docs/plan.md` Phase 1, `design/data-model.md` (all), `design/security.md` §4–5, `design/testing.md` §4.

## Order of work (test after each step)

1. The migration runner and `0001_init.sql`: schemas, the `app` tables, the `biz` tables with comments, composite keys and indexes.
2. Roles and grants: `bi_owner`, `bi_app`, `bi_readonly`; column grants that leave out the private columns; `set_config` revoked from the public.
3. Forced row security and the two policies; the catalogue test that every `biz` table has it.
4. `store/pg.py`: the pools and `tenant_session(ctx)`.
5. **The row-security tests, before the seed** (two hand-made companies of three rows each).
6. `app/seed`: the generator with `--as-of` and a fixed random seed; the stories S1–S8; a test per story; the determinism test.
7. `scripts/db.sh`; time the typical query.

## Acceptance

The gate of this phase in `docs/plan.md`. Every box ticked with its evidence, or explained.

## Do not

Build login, any route beyond health, or MongoDB and Qdrant collections. Use a materialized view.
