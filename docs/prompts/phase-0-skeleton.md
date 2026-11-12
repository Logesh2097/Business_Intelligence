# Phase 0 — Skeleton

**Goal:** a repository that starts, tests and builds, with nothing of the product in it.
**Read:** `docs/plan.md` Phase 0, `design/architecture.md` §2–4 and §7, `design/deployment.md` §1–3, `design/testing.md` §1.

## Order of work (test after each step)

1. `.gitignore`, `.env.example` (every `BI_*` key with a comment), `Makefile`, `README.md`, `CLAUDE.md`.
2. `backend/pyproject.toml`; `app/core/config.py` (settings, and `validate()` with the refusals of `architecture.md` §7); its tests.
3. `app/core/logging.py` (JSON lines, request id); `app/main.py`; `app/api/routes/health.py` (`/healthz`, `/readyz` with a check per store and a short timeout); tests with the stores down.
4. `frontend/`: Vite + Vue + TypeScript + Vuetify + Pinia + Router; the shell; the Status page; the proxy; one unit test; `npm run build`.
5. `docker-compose.yml` with the three stores, health checks and the role-creating init script; `backend/Dockerfile`.
6. `scripts/dev.sh`. Run the gate. Look at the Status page.

## Acceptance

The gate of this phase in `docs/plan.md`. Every box ticked with its evidence, or explained.

## Do not

Add a table, a login, a model call or any product screen.
