# BI Assistant — working rules

A multi-tenant business intelligence assistant: FastAPI + LangGraph backend, Vue web app, PostgreSQL, MongoDB, Qdrant, OpenAI, tools behind MCP, deployed on GCP. The documentation in `docs/` is the design; **start at `docs/README.md`**, and for a build session at `docs/prompts/00-start-here.md`.

## How work is done here

- **One phase at a time**, in `docs/plan.md` order, using that phase's prompt. Stop at its gate, report, wait.
- **Docs change with the code.** Update `docs/api.md` and the design doc in the same change; update a doc's Status line when you make it true. The owner wants every doc kept in detail.
- **A decision that is the owner's** (`docs/design/architecture.md` §9) is asked, not guessed.
- Commit or push only when asked.

## Rules that protect the product

1. Routes hold no SQL, no Mongo queries and no prompts. All data access is in `backend/app/store/`.
2. Agents reach data only through `backend/app/tools/`. No tool takes a company, user or outlet argument; the caller's context comes from the session.
3. AI-written SQL runs only through the guard, as `bi_readonly`, in a read-only transaction, under row security.
4. Any new `biz` table comes with `tenant_id`, its composite key, its row-security policy and its grants in the same migration.
5. Every MongoDB query and every Qdrant search filters on `tenant_id`, added by our code.
6. Numbers by code, words by the model. The model never computes a figure.
7. Model or user text is never rendered as HTML (`v-html` is a lint error).
8. Never log or return a password, a token, a connection string or a customer's personal detail. Never write one in code, the Compose file or `.env.example`: credentials come from the environment, and `.env.example` holds `change-me…` placeholders only.
9. Every test except `tests/evals` uses the fake model and needs no key.
10. Raise an `AppError` subclass from `core/errors.py`; never build an error response by hand. Only `app/llm/` talks to a model provider.

## Commands

| | |
|---|---|
| `make setup` | Python environment, npm packages, `.env` |
| `make up` / `make down` | the three stores in Docker |
| `make dev` | API on :8110 (reload) and web app on :5290 |
| `make mcp` | the MCP tool server on :8111 (needs `BI_SECRET_KEY` set, shared with the API) |
| `make ci` | lint, types, backend tests, frontend tests, build |
| `make db-migrate` / `make db-seed` | create the schema; load the demo data |
| `make test` | backend unit tests only (mocked or serverless) |
| `make pg-up` / `make pg-down` | a local PostgreSQL without Docker |
| `make test-integration` | live tests against real servers; skipped ones say why |
| `make eval-check` | the golden question set without a model: builds `bi_eval`, checks every expected value against PostgreSQL, then runs all 58 again through a real MCP server process |
| `make eval CONFIRM_REAL_MODEL=1 BI_EVAL_MIN_CALL_INTERVAL_SECONDS=<s>` | the golden question set with the real model (costs model calls); `<s>` is the seconds between model calls, chosen from the account's quota, and has no default; add `ONLY=<id>` for one. `make eval` alone runs nothing and says so |

Node 22 or newer is required (`nvm use 22`; the system `node` on this machine is 12). Python is 3.10 here and 3.12 in the image, so code must run on 3.10.

## Ports

API 8110 · MCP 8111 · web 5290 · PostgreSQL 5610 · MongoDB 27027 · Qdrant 6343/6344. The machine's own PostgreSQL (5432) and the CRM's ports are not ours to use.

## Conventions

- **Python:** `ruff` (lint and format, line length 100), `mypy --strict` on `app/`, `from __future__ import annotations`, async I/O, Pydantic models for every request and response. No ORM: SQL lives in `store/` and in numbered migration files.
- **Tests:** named as sentences (`test_readyz_never_leaks_a_connection_string`). Unit tests need nothing running. Each database test file says at its top whether it is MOCKED, REAL DRIVERS NO SERVER, or LIVE.
- **Database access:** `async with stores.postgres.session() as conn:` (one transaction) or `async with stores.mongo.guard():`, inside a function in `store/`. Routes get `StoresDep` from `api/deps.py` and call store functions.
- **Vue:** `<script setup lang="ts">`, pages fetch through stores, components take props, logic worth testing goes in `src/lib/` with a test beside it.
- **The model:** call it only through `llm.structured(task=…, schema=…)`. Tests script answers with `FakeLLM().on(task, answer)`; an unscripted call fails the test.
- **Sign-in is not built.** `api/deps.py › get_caller` accepts `X-Dev-User` in dev and test only. Do not widen it; replace it in the authentication phase.
- **Settings:** every one is a `BI_*` variable in `core/config.py` and is explained in `.env.example`. A setting that is unsafe in production is added to `Settings.problems()` with a test.
- **Writing in docs:** plain words, short sentences, tables for anything with more than two attributes. Say what is built and what is only designed.
