# BI Assistant

Ask business questions in plain language; get an answer with the table, a chart and recommendations. Several companies share one installation and never see each other's data.

**State of the build:** done so far (see [docs/plan.md](docs/plan.md)): the project setup, the database integration, the schema with row-level security and demo data, and **question to SQL**: `POST /api/v1/analytics/query` turns a question into a checked, read-only, tenant-scoped query and explains the result. Three **domain agents** (Sales, Inventory, Customer) answer on their own through `POST /api/v1/agents/{agent}/query`, each limited to its own tables. A real model has run both (Google Gemini through `BI_OPENAI_BASE_URL`); OpenAI's own models have not been run. Tests use a scripted model. A **supervisor** on a LangGraph graph chooses among them, or the general workflow, behind `POST /api/v1/assistant/query`. Not built: sign-in, the chat screen, documents (RAG), MCP, the insight and document agents.

## Read first

| | |
|---|---|
| [docs/README.md](docs/README.md) | the index of all documentation |
| [docs/product/overview.md](docs/product/overview.md) | what the product does, in plain words |
| [docs/design/architecture.md](docs/design/architecture.md) | the stack, the layout, the settings, the open decisions |
| [docs/plan.md](docs/plan.md) | the phases and their gates |
| [docs/api.md](docs/api.md) | the API contract |

## What you need

| Tool | Version | Note |
|---|---|---|
| Python | 3.10 or newer | 3.12 is used inside the Docker image |
| Node.js | 22 or newer | with nvm: `nvm use 22` |
| Docker with Compose | any recent | runs PostgreSQL, MongoDB and Qdrant locally |

## Set up (once)

```bash
make setup
```

This creates the Python environment in `backend/.venv`, installs the backend and frontend packages, and copies `.env.example` to `.env` if you have none. The values in `.env.example` are placeholders that work on your own machine; change them in `.env` if you wish. `.env` is never committed.

## Run

```bash
make up      # PostgreSQL :5610, MongoDB :27027, Qdrant :6343 (Docker)
make db-migrate   # create the schema (first time, and after a new migration)
make db-seed      # load the demo data for two companies
make dev          # API on :8110 with reload, web app on :5290
```

### Ask a question

Set `BI_OPENAI_API_KEY` and `BI_LLM_MODEL` in `.env`, restart, then:

```bash
curl -s http://localhost:8110/api/v1/analytics/query \
  -H 'Content-Type: application/json' \
  -H 'X-Dev-User: analyst@brewbite.example' \
  -d '{"question": "What was our total revenue last month?"}'
```

`X-Dev-User` names a demo user until sign-in is built (development only): `admin@`, `analyst@` or `viewer@brewbite.example`, `admin@` or `viewer@urbanmart.example`. The viewers are limited to some outlets and never receive the SQL. Without a key the route answers `503` and says so.

| Open | To see |
|---|---|
| <http://localhost:5290> | the web app; its Status page shows the API and each store |
| <http://localhost:8110/healthz> | the API is alive |
| <http://localhost:8110/readyz> | whether PostgreSQL, MongoDB and Qdrant answer (`503` names the ones that do not) |
| <http://localhost:8110/api/v1/system/databases> | each database's status, version and connection pool (development only; no credentials) |
| <http://localhost:8110/api/v1/docs> | the API's own documentation page (development only) |

Without Docker, `make dev` still works: the API and the web app start, and the Status page shows the three stores as down. PostgreSQL alone can run without Docker: `make pg-up` starts a local one on the same port from the PostgreSQL 16 installed on the machine (`make pg-down` stops it).

To run the API in a container as well: `docker compose --profile app up --build`. Stop the stores with `make down`.

## Check a change

```bash
make ci      # lint, type checks, backend tests, frontend tests, web build
```

| Command | Runs |
|---|---|
| `make test` | backend unit tests: mocked or serverless, need nothing running |
| `make test-integration` | backend live tests against real PostgreSQL and MongoDB; a test whose server is not running is skipped and says so |
| `make web-test` | frontend unit tests |
| `make lint` | `ruff` and ESLint |
| `make typecheck` | `mypy --strict` and `vue-tsc` |
| `make help` | the list of targets |

## Layout

```
backend/
  app/
    main.py         creates the API: settings, logging, connections, error handlers, routes
    core/           settings (BI_* variables), JSON logging, the one error shape
    api/routes/     HTTP routes. They hold no SQL and no prompts
    store/          the data layer: PostgreSQL pools and sessions, MongoDB client, health and status
    agents/         the SQL analyst and the summariser, and their prompts
    workflows/      the question pipeline (a LangGraph graph later)
    llm/            the model client (OpenAI) and the scripted fake used by tests
    seed/           the demo data generator
    rag/            document ingestion and retrieval                    (empty for now)
    mcp_server/     the tools exposed over MCP                          (empty for now)
    tools/          the functions agents call to reach data             (empty for now)
    sqlguard/       checks AI-written SQL before it runs
    semantic/       business vocabulary: tables, metrics, period words, schema retrieval
  tests/unit/       tests that need nothing running
  tests/integration/  live tests against real PostgreSQL and MongoDB
  pyproject.toml    Python dependencies and tool settings
  Dockerfile        one image: the API with the built web app inside
frontend/
  src/pages/        one file per page
  src/components/   reusable pieces
  src/stores/       state (Pinia); pages read stores, stores call the API
  src/api/          calls to the backend
  src/lib/          pure logic, each with a test beside it
  src/plugins/      Vuetify and theme
deploy/postgres/    creates the local database users on first start
scripts/dev.sh      starts the API and the web app together
scripts/local-postgres.sh  a local PostgreSQL for machines without Docker
docs/               product, design, plan, API contract, phase prompts
docker-compose.yml  the three stores (and, optionally, the API) for local development
.env.example        every setting, explained, with placeholders only
```

## Settings and secrets

Every setting is a `BI_*` environment variable, listed with an explanation in [.env.example](.env.example). No password, key or token is written in the code or in the Compose file. Outside development the app refuses to start with a missing or placeholder secret (see [docs/design/architecture.md](docs/design/architecture.md) §7).
