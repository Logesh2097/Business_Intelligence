# Architecture

**Status:** Phase 0 is built (the repository skeleton and folder structure, settings, logging, error handling, the PostgreSQL and MongoDB connections, the health routes, the web shell, the Compose file). The folders for agents, workflows, the model, documents, MCP and tools exist and are empty. Everything else below is the design the later phases build. This is the engineer's map. *What the product does* is `../product/overview.md`, the wire contract is `../api.md`, the schema is `data-model.md`.

## 1. The system in one picture

```
 Browser (Vue SPA)
      │  HTTPS: JSON, and Server-Sent Events for the answer as it is written
      ▼
 ┌──────────────────────────── bi-api (FastAPI) ────────────────────────────┐
 │  auth · roles · conversations · reports · documents · admin              │
 │                                                                          │
 │  agents (LangGraph)                                                      │
 │    supervisor → sql analyst │ document agent │ insight agent → report    │
 │         │ model calls                         │ tool calls               │
 └─────────┼─────────────────────────────────────┼──────────────────────────┘
           ▼                                     ▼
       OpenAI API                    bi-mcp (MCP server, same image)
       (chat + embeddings)             list_tables · describe_table · run_query
                                       search_documents · get_metric_definitions
                                       find_similar_questions
                                          │                    │
                                          ▼                    ▼
 ┌───────────────┐   ┌───────────────┐   ┌───────────────────┐   ┌──────────────┐
 │  PostgreSQL   │   │  PostgreSQL   │   │      Qdrant       │   │   MongoDB    │
 │  schema app   │   │  schema biz   │   │ doc_chunks        │   │ conversations│
 │  users, roles │   │  business data│   │ sql_examples      │   │ messages     │
 │  audit, usage │   │  READ ONLY,   │   │                   │   │ reports      │
 │  (bi_app)     │   │  row security │   │                   │   │ documents    │
 │               │   │  (bi_readonly)│   │                   │   │ examples     │
 └───────────────┘   └───────────────┘   └───────────────────┘   └──────────────┘
```

Two things to notice:

1. **The model never touches a database.** It asks for a tool; the tool checks the request and runs it as a database user that can only read, and only the caller's company.
2. **One PostgreSQL server, two schemas, three database users.** `app` is the product's own data (users, logs). `biz` is the business data people ask about. The user that runs AI-written SQL can see `biz` only.

## 2. Stack (locked)

| Layer | Choice | Why this and not more |
|---|---|---|
| Language | Python 3.12 in the container image; the code also runs on 3.10 (this machine) | one language for API, agents and data work |
| API | FastAPI + Uvicorn, Pydantic v2 for every request and response | typed, async, generates the OpenAPI document |
| Agents | LangGraph for the workflow, LangChain (`langchain-openai`) for model calls and structured output | the graph makes each step visible and testable |
| Model | OpenAI through `BI_LLM_MODEL` (the strong model: SQL, analysis) and `BI_LLM_MODEL_FAST` (routing, titles); embeddings `text-embedding-3-small` (1,536 numbers) | the names are settings, so a newer model is a one-line change; confirmed at Phase 3 |
| Business + platform data | PostgreSQL 16, `psycopg` 3 with a connection pool, **no ORM**; numbered `.sql` migrations run by a small runner | the schema is roles, row security and constraints, which are SQL; an ORM would hide the part that matters |
| Conversations | MongoDB 7, the async client of `pymongo` | a message is a nested document (answer, table, chart, trace) that changes shape as the product grows |
| Vectors | Qdrant, `qdrant-client` | document search and example-question search, filtered by company |
| SQL checking | `sqlglot` | parses the model's SQL so the guard works on the syntax tree, not on text |
| Tools | the official `mcp` Python SDK (server), `langchain-mcp-adapters` (client) | see `mcp.md` |
| Sign-in | email + password (`argon2-cffi`), JWT access token (`pyjwt`), rotating refresh token | no outside identity service is needed |
| Frontend | Vue 3 + TypeScript + Vite + Pinia + Vue Router + Vuetify; charts with ECharts (`vue-echarts`) | the same family as the CRM portal |
| Packaging | one Docker image for `bi-api` and `bi-mcp` (two start commands); Docker Compose locally | |
| Cloud | GCP: Cloud Run, Cloud SQL, Secret Manager, Cloud Storage, Artifact Registry; MongoDB Atlas and Qdrant Cloud on GCP | see `deployment.md` |

**Deliberately not used:** Redis, Celery, Kafka, Kubernetes, an ORM, a separate auth service, a reranking model, a second LLM provider, LangSmith (off by default: it would send company data to a third party). Each can be added when a measured need appears; none is needed to build this product.

## 3. Repository layout (target)

```
bi/
├── backend/
│   ├── app/
│   │   ├── main.py            create the app: config → logging → pools → routes
│   │   ├── core/              config.py (BI_* settings), logging.py, errors.py (the one error shape), request_context.py,
│   │   │                      security.py (hashing, tokens), clock.py
│   │   ├── api/               deps.py (settings, stores; later the current user and permissions) and routes/: health system auth chat conversations
│   │   │                      reports documents admin platform meta.   Routes hold no SQL and no prompts
│   │   ├── store/             THE data layer. One file per area. All SQL and all Mongo queries live here.
│   │   │                      postgres.py (pools, sessions), mongo.py (client, guard), qdrant.py, connections.py,
│   │   │                      health.py (readiness), status.py (the status report)
│   │   │   └── migrations/    0001_init.sql, 0002_… applied in order, recorded in app.schema_migrations
│   │   ├── seed/              the demo data generator and the sample documents
│   │   ├── semantic/          the business vocabulary: tables.yaml, metrics.yaml, examples.yaml + loader
│   │   ├── sqlguard/          PURE: text in → checked SQL out, or a reason. No database, no network
│   │   ├── tools/             the six tools as plain async functions (schema, metrics, examples, query, documents)
│   │   ├── mcp_server/        the same six tools exposed over MCP; verifies the caller's context token
│   │   ├── rag/               extract, chunk, embed, ingest, retrieve
│   │   ├── llm/               the model client, and the scripted fake used by every test
│   │   ├── agents/            one file per agent, prompts/ (one text file per prompt)
│   │   └── workflows/         the LangGraph graph: the shared state, the nodes wired together, the routes, the limits
│   ├── tests/
│   │   ├── unit/              no services needed
│   │   ├── integration/       real PostgreSQL, MongoDB, Qdrant; fake model
│   │   └── evals/             the golden question set; real model; run on demand
│   ├── pyproject.toml   Dockerfile
├── frontend/                  the Vue app: src/pages, src/components (reusable pieces), src/stores (Pinia),
│                              src/api (calls to the backend), src/lib (pure logic with tests), src/plugins
├── deploy/                    postgres/init-roles.sh (local database users); Cloud Run and CI files from Phase 10
├── scripts/                   dev.sh, local-postgres.sh (PostgreSQL without Docker); seed and test helpers later
├── docs/                      this folder
├── docker-compose.yml   .env.example   Makefile   CLAUDE.md   README.md
```

**Built for question to SQL (the owner's Phase 3):** `store/migrations/0001_init.sql`, `store/{migrate,users,analytics}.py`, `seed/`, `core/{context,clock}.py`, `sqlguard/{guard,functions}.py`, `semantic/{tables.yaml,metrics.yaml,model,retrieval,render,periods}.py`, `llm/{base,openai,fake,factory}.py`, `agents/{sql_analyst,summarizer,prompting}.py` and `agents/prompts/{sql_analyst,summary}.md`, `workflows/analytics.py`, `api/routes/analytics.py`. Still empty: `rag/`, `mcp_server/`, `tools/` (the tool wrappers arrive with MCP; until then the SQL analyst calls `store/analytics.py` directly).

**Built in Phase 0B (database integration):** `store/{postgres,mongo,qdrant,connections,health,status}.py`, `api/deps.py`, `api/routes/system.py`, `scripts/local-postgres.sh`, `tests/unit/{fakes,test_postgres_mocked,test_mongo_mocked,test_no_server,test_lifecycle,test_status_api}.py`, `tests/integration/{conftest,test_postgres_live,test_mongo_live,test_api_live}.py`.

**Built in Phase 0:** `backend/app/main.py`, `core/{config,logging,errors,request_context}.py`, `api/routes/health.py`, `store/{postgres,mongo,connections,health}.py`, `backend/tests/unit`, `backend/Dockerfile`, `frontend/` (the shell, the Status page, its store, a reusable row component, `src/lib/status.ts`), `docker-compose.yml`, `deploy/postgres/init-roles.sh`, `.env.example`, `Makefile`, `scripts/dev.sh`, `CLAUDE.md`, `README.md`.

**Present but empty** (a package with a description of its job, filled by its phase): `agents/` and `agents/prompts/`, `workflows/`, `llm/`, `rag/`, `mcp_server/`, `tools/`, `sqlguard/`, `semantic/`, `tests/integration/`. `api/deps.py`, `core/security.py`, `core/clock.py`, `store/migrations/`, `seed/`, `tests/evals/` are created by the phase that needs them.

### Layer rules

| Rule | Reason |
|---|---|
| Routes call services or the store; they never hold SQL, Mongo queries or prompts | one place to read to know what touches the data |
| `sqlguard` imports nothing from the app and opens no connection | it is the most security-critical code; it must be testable in milliseconds with a table of cases |
| Agents reach data **only** through `tools/` | one doorway, so one place to enforce access |
| `agents/` says what each agent does; `workflows/` says how they are wired together | the two change for different reasons, and each can be tested without the other |
| Only `llm/` talks to a model provider | one place to swap the model, count tokens and plug in the fake |
| Every error leaves through `core/errors.py` | one shape for the browser to handle; nothing internal in a response |
| Tools take the caller's context (company, user, outlets) from the session, never from a model-supplied argument | the model cannot ask for another company |
| A prompt is a text file in `agents/prompts/`, not a string inside code | prompts are reviewed and versioned like code |

## 4. Ports

Checked free on this machine with `ss -ltn` on 2026-11-10 (the local PostgreSQL on 5432 and the CRM's ports 8101, 5282–5284, 5606 are left alone).

| What | Port |
|---|---|
| `bi-api` | 8110 |
| `bi-mcp` | 8111 |
| web (Vite dev server) | 5290 |
| PostgreSQL | 5610 |
| MongoDB | 27027 |
| Qdrant (HTTP / gRPC) | 6343 / 6344 |

## 5. Request path

| Group | Guard | Who |
|---|---|---|
| `/healthz`, `/readyz` | none | monitors |
| `/api/v1/auth/login`, `/auth/refresh` | rate limit per email and address | anyone |
| `/api/v1/*` | access token → user → company → one permission per route | signed-in users |
| `/api/v1/platform/*` | access token with role `super_admin` | the platform admin |
| `/mcp` on `bi-mcp` | a short-lived context token signed by `bi-api` | the agents (not reachable from the internet) |
| `/` and everything else | none | the web app's files |

**One question, start to finish:**

1. The browser posts the question to `POST /api/v1/chat/stream` with the access token.
2. The API checks the token, loads the user, their company and their outlets, checks the company's daily limit, and saves the question to MongoDB.
3. The graph starts. The **supervisor** reads the question and the last few turns and picks a route.
4. The chosen agent works. Each time it needs data it calls a tool; the tool checks the request, sets the company and outlets on the database session, and reads.
5. The **report agent** writes the answer from the results and picks a chart.
6. Each step is sent to the browser as an event while it happens (`status`, `sql`, `table`, `chart`, `token`, `sources`, `done`).
7. The answer, the queries, the trace and the token counts are saved to MongoDB; each query is also written to `app.query_log`.

The steps in detail: `agents.md`. The tool rules: `nl2sql.md`, `rag.md`, `mcp.md`.

## 5a. Database connections (built)

```
 create_app()
   └─ lifespan start ──► Stores.open(settings)          does not wait for any server
        ├─ Postgres   pool "bi-app"       bi_app       read-write   session()
        │             pool "bi-readonly"  bi_readonly  READ ONLY    readonly_session()
        ├─ Mongo      one AsyncMongoClient (its own pool), connects on first use
        └─ Qdrant     plain HTTP for now
   └─ lifespan end ────► Stores.close()                 closes all, even if one fails
```

| Piece | Design |
|---|---|
| Driver | `psycopg` 3 (async) with `psycopg_pool`; PyMongo's async client. No ORM (§2) |
| Pools | two PostgreSQL pools, one per database user. Sizes and timeouts are `BI_PG_POOL_*` settings. `min = 0` by default: no connection is held until needed, so the API starts when the database is down |
| A session | `async with stores.postgres.session() as conn:` is **one transaction**: committed when the block ends, rolled back when it raises, and the connection always returns to the pool. Rows are dictionaries |
| Timeouts | opening a connection (`BI_PG_CONNECT_TIMEOUT_SECONDS`), waiting for a free one (`BI_PG_POOL_TIMEOUT_SECONDS`), one statement (`BI_PG_STATEMENT_TIMEOUT_SECONDS`, set with `SET LOCAL` at the start of each session; the read-only session uses the shorter `BI_QUERY_TIMEOUT_SECONDS`) |
| The read-only pool | every connection is put in read-only mode when it is created, so a write is refused by PostgreSQL itself |
| Failures | a database that is down, out of connections or too slow raises `UnavailableError` (HTTP 503 in the one error shape). The cause is logged with its class name and never returned. A mistake in a query (unknown table, duplicate key) is **not** turned into 503: it stays what it is |
| MongoDB | store functions wrap their work in `async with mongo.guard():`, which does the same translation. Timeout and pool size are `BI_MONGO_TIMEOUT_MS` and `BI_MONGO_POOL_MAX` |
| Routes | get the stores through `api/deps.py` (`StoresDep`). They never open a connection and never hold a query |
| The company on a session | not yet: `tenant_session(ctx)`, which sets `app.tenant_id` and `app.outlet_ids` for row security, arrives with the schema (`data-model.md` §2) |

## 6. Where each kind of data lives, and why

| Data | Store | Why there |
|---|---|---|
| Companies, users, roles, outlet access, refresh tokens | PostgreSQL `app` | relational, must be consistent, joins to outlets |
| Audit log, query log, daily usage, login attempts | PostgreSQL `app` | queried by admins with filters and dates; limits must hold across several API instances |
| Business data (14 tables) | PostgreSQL `biz` | it is what SQL is for; row security is enforced by the database |
| Conversations, messages (answer, table, chart, trace), saved reports, document records, learned examples | MongoDB | nested, read whole, shape evolves |
| Document chunks and example questions, as vectors | Qdrant | similarity search with a company filter |
| Uploaded files | a local folder in development, a Cloud Storage bucket in the cloud | files are not database rows |

If the product were smaller, MongoDB's part could live in PostgreSQL `jsonb` columns. It is kept because it is part of the requested stack and it has a clear, separate job here; nothing relational is put in it.

## 7. Configuration (`BI_*`)

Read once at start by `core/config.py`; every key is explained in `.env.example`.

| Key | Meaning | Default (development) |
|---|---|---|
| `BI_ENV` | `dev`, `test`, `staging` or `prod` | `dev` |
| `BI_PORT` | API port | `8110` |
| `BI_PG_OWNER_PASSWORD`, `BI_PG_APP_PASSWORD`, `BI_PG_READONLY_PASSWORD` | read by Docker Compose only, to create the local database and its users | placeholders in `.env.example` |
| `BI_PG_DSN` | PostgreSQL as `bi_app` | local, port 5610 |
| `BI_PG_READONLY_DSN` | PostgreSQL as `bi_readonly` (used only by the query tool) | local, port 5610 |
| `BI_PG_OWNER_DSN` | PostgreSQL as `bi_owner` (migrations and seed only; **not given to the running API in the cloud**) | local, port 5610 |
| `BI_PG_POOL_MIN`, `BI_PG_POOL_MAX` | connections each PostgreSQL pool keeps and may open | 0, 10 |
| `BI_PG_CONNECT_TIMEOUT_SECONDS`, `BI_PG_POOL_TIMEOUT_SECONDS`, `BI_PG_STATEMENT_TIMEOUT_SECONDS` | how long to wait to connect, to get a free connection, and for one statement | 3, 5, 30 |
| `BI_MONGO_URL`, `BI_MONGO_DB` | MongoDB | local, port 27027, `bi` |
| `BI_MONGO_TIMEOUT_MS`, `BI_MONGO_POOL_MAX` | how long to wait for MongoDB, and its connection limit | 2000, 20 |
| `BI_QDRANT_URL`, `BI_QDRANT_API_KEY` | Qdrant | local, port 6343, none |
| `BI_OPENAI_API_KEY` | the model key | none; needed from Phase 3 |
| `BI_OPENAI_BASE_URL` | the address of another service that speaks OpenAI's API (for example Google Gemini). The key and model names are then that service's | empty: OpenAI itself. Outside development it must be `https` |
| `BI_LLM_MODEL`, `BI_LLM_MODEL_FAST`, `BI_EMBEDDING_MODEL` | the model that writes SQL, the cheaper one that writes the explanation (empty: the same), and the embedding model | none: without a key and `BI_LLM_MODEL` the question route answers 503. Both are required outside `dev` and `test` |
| `BI_SECRET_KEY` | signs access tokens and context tokens | empty: a random key is made at each start in `dev` and `test`; refused when empty elsewhere |
| `BI_ACCESS_TOKEN_MINUTES`, `BI_REFRESH_TOKEN_DAYS` | token lifetimes | 15, 14 |
| `BI_TOOLS_TRANSPORT` | `direct` (call the tool functions in process) or `mcp` | `direct` until Phase 8 |
| `BI_MCP_URL` | where `bi-mcp` listens | `http://localhost:8111/mcp` |
| `BI_UPLOAD_DIR` / `BI_GCS_BUCKET` | where uploaded files go | `./data/uploads` |
| `BI_SEED_DEMO`, `BI_SEED_PASSWORD` | create the demo companies when the database is empty | `true`, a development value |
| `BI_FAKE_TODAY` | pin "today" (tests and the golden set) | unset |
| `BI_DAILY_QUESTION_LIMIT`, `BI_DAILY_TOKEN_LIMIT` | per-company daily limits | 500, 2,000,000 |
| `BI_QUERY_TIMEOUT_SECONDS`, `BI_QUERY_MAX_ROWS` | limits on AI-written SQL | 15, 1,000 |
| `BI_MCP_HOST`, `BI_MCP_PORT` | where the MCP process listens. Loopback by default | 127.0.0.1, 8111 |
| `BI_DOC_MAX_MB`, `BI_DOC_MAX_PER_TENANT` | the largest uploaded document; how many a company may hold | 20, 200 |
| `BI_RAG_MIN_SCORE` | passages scoring below this are not used to answer. A starting value, not measured | 0.30 |
| `BI_AGENT_TIMEOUT_SECONDS` | the longest one agent answer may take, start to end | 90 |
| `BI_ASSISTANT_TIMEOUT_SECONDS` | the longest one assistant answer may take: the supervisor, every agent it chose and the combined summary | 150 |

**No credential has a default in the code.** Passwords, keys and tokens come from the environment: `.env` locally (not in git), Secret Manager in the cloud. `.env.example` holds only placeholders that begin with `change-me`, and tests keep it that way.

**The settings object never shows a secret.** Eleven fields are left out of its `repr()` and `str()` (`Field(repr=False)` in `core/config.py`): the three PostgreSQL connection strings, `mongo_url`, `qdrant_url`, `qdrant_api_key`, `openai_api_key`, `openai_base_url`, `secret_key`, `mcp_url` and `seed_password`. Every field that holds a password, key or token is in the list, and so is every address, because an address can carry `user:password@`. A traceback, a failed test or a debug line that prints the object therefore cannot print one. The values are read and used exactly as before. A validation error raised by `Settings(...)` itself names the field and the rule and not the value (`hide_input_in_errors`). Reason: on 2026-11-12 a failed test printed the object, with the local connection strings, into a log. Tests: `tests/unit/test_config.py` (a new secret-like field that is not hidden fails a test).

**A malformed setting stops the start** with a message that names the variable and never repeats its value (a rejected connection string may hold a password): a connection string with the wrong scheme, a port out of range, a timeout or pool size that is not a positive number, a pool minimum above its maximum, an unknown `BI_ENV`.

**Refused at start outside `dev` and `test`:** a missing secret key, a placeholder (`change-me…`) as the secret key or inside any connection string, a secret shorter than 32 characters, `BI_SEED_DEMO=true`, `BI_FAKE_TODAY`, a missing model key, `BI_PG_OWNER_DSN` being set for the API process, and a read-only connection that is the same user as the app connection.

## 8. Logging and tracing

- **Logs:** one JSON line per event, with `request_id`, `tenant_id`, `user_id`, the route and the time taken. Never a password, a token, a customer's personal detail, or the full text of a document.
- **The trace of an answer:** each agent step (name, time, tokens, a one-line summary, the SQL it ran) is stored with the message in MongoDB. It is what the admin's "how was this answered" view shows and what a bug report quotes.
- **Model spend:** tokens in and out are counted per message and summed per company per day in `app.llm_usage_daily`.

## 9. Decisions taken and what is still open

| For | Status |
|---|---|
| One database for all companies or one each | **Decided.** One shared database; every row carries `tenant_id`; row security enforces it (`data-model.md` §2) |
| Sign-in | **Decided by the owner.** Email and password only |
| Business areas | **Decided by the owner.** Sales, customers, inventory, purchasing, expenses |
| Data | **Decided by the owner.** Generated sample data for two demo companies; real data is a later step |
| Where the web app is served from | **Decided.** The built Vue files are served by `bi-api` itself, so the browser talks to one origin (no CORS in production, a strict cookie) |
| Summary tables for speed | **Decided: not in the first version.** Twelve months of orders with indexes answer in milliseconds. If a measured query is slow, add a summary *table* (not a materialized view: row security does not apply to those) |
| Schema retrieval | **Changed at the owner's request (2026-11-11).** Only the tables a question needs are sent, chosen by word matching and the join graph (`nl2sql.md` §2a). Vector search is kept for example questions and documents |
| **Docker on this machine** | **Open, the owner's.** Docker is not installed here and installing it needs an administrator password. Until it is, the Compose file cannot be run on this machine (`deployment.md` §1) |
| The OpenAI key and model names | **Open, the owner's.** Needed at the start of Phase 3 |
| Currency and time zone of the demo companies | **Taken by default:** INR and `Asia/Kolkata`. One line each to change, per company |
| GCP project and region | **Open, the owner's.** The docs assume `asia-south1` (Mumbai) |
| May company data be sent to OpenAI | **Open, the owner's, before real data.** The design sends the schema, the question and query results (never customer names or contacts). A company that forbids this needs a different model host |
| Scheduled reports, forecasting, live POS connection | **Not in the first version** (`../product/overview.md`) |
