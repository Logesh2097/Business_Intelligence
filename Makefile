# BI Assistant. `make help` lists the targets.
# Node 22 or newer is needed for the web targets (nvm use 22).

PY := backend/.venv/bin

.PHONY: help setup dev mcp up down pg-up pg-down db-migrate db-seed test test-integration eval-check eval lint typecheck web-test web-build ci

help:            ## List the targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

setup:           ## Create the Python environment and install both sides
	python3 -m venv backend/.venv
	$(PY)/pip install -q --upgrade pip
	$(PY)/pip install -q -e "backend[dev]"
	cd frontend && npm ci
	@test -f .env || cp .env.example .env

dev:             ## Start the API (:8110) and the web app (:5290)
	scripts/dev.sh

mcp:             ## Start the MCP tool server (:8111). Set BI_SECRET_KEY in .env first
	cd backend && .venv/bin/python -m app.mcp_server

up:              ## Start PostgreSQL, MongoDB and Qdrant (needs Docker)
	docker compose up -d

down:            ## Stop them (data is kept; add -v by hand to wipe)
	docker compose down

pg-up:           ## Start a local PostgreSQL without Docker (uses the installed PostgreSQL 16)
	scripts/local-postgres.sh up

pg-down:         ## Stop it
	scripts/local-postgres.sh down

db-migrate:      ## Create or update the database schema (as bi_owner)
	cd backend && .venv/bin/python -m app.store.migrate

db-seed:         ## Load the demo data for two companies (replaces existing rows; dev only)
	cd backend && .venv/bin/python -m app.seed

test:            ## Backend unit tests: mocked or serverless, need nothing running
	cd backend && .venv/bin/pytest -q tests/unit

test-integration: ## Backend LIVE tests against real PostgreSQL and MongoDB (skips what is not running; uses its own bi_test database)
	cd backend && .venv/bin/pytest -q -rs tests/integration

eval-check:      ## Golden question set, NO model: builds bi_eval, checks every expected value against PostgreSQL
	cd backend && BI_EVAL_CONFIRM_REAL_MODEL= BI_EVAL_REAL_MODEL= .venv/bin/pytest -q -rs tests/evals/test_golden_dataset.py tests/evals/test_golden_mcp.py

# The real-model run costs model calls, so it never starts by accident: without
# CONFIRM_REAL_MODEL=1 typed on this very command line (a variable left exported in the
# shell does not count) this target only says what it would do, and stops.
# BI_EVAL_MIN_CALL_INTERVAL_SECONDS=<seconds> (on the command line or in the shell) spaces the
# model calls out; the test refuses to start without it (tests/evals/runner.py).
eval:            ## Golden question set with the REAL model. Needs: make eval CONFIRM_REAL_MODEL=1 [ONLY=<case id>]
ifeq ($(origin CONFIRM_REAL_MODEL)-$(CONFIRM_REAL_MODEL),command line-1)
	cd backend && BI_EVAL_CONFIRM_REAL_MODEL=1 BI_EVAL_ONLY="$(ONLY)" .venv/bin/pytest -q -rs tests/evals/test_golden_eval.py
else
	@echo "make eval asks the REAL model every golden question (58 questions, roughly 170 model calls)."
	@echo "Nothing was run. To run it on purpose:"
	@echo "    make eval CONFIRM_REAL_MODEL=1 BI_EVAL_MIN_CALL_INTERVAL_SECONDS=<s>                  every question"
	@echo "    make eval CONFIRM_REAL_MODEL=1 BI_EVAL_MIN_CALL_INTERVAL_SECONDS=<s> ONLY=<case id>   one question"
	@echo "<s> is the seconds kept between two model calls. It has no default: choose it from your"
	@echo "account's quota (60 / calls allowed a minute, plus a margin). Without it nothing is sent."
	@echo "To check the dataset without any model: make eval-check"
	@exit 1
endif

lint:            ## Lint and format check, both sides
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd frontend && npm run lint

typecheck:       ## Type check, both sides
	cd backend && .venv/bin/mypy app
	cd frontend && npm run typecheck

web-test:        ## Frontend unit tests
	cd frontend && npm run test

web-build:       ## Build the web app
	cd frontend && npm run build

ci: lint typecheck test web-test web-build   ## Everything a change must pass
