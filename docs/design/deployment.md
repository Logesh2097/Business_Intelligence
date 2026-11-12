# Deployment and operations

**Status:** design. Phase 0 wrote `docker-compose.yml`, the backend `Dockerfile` and `scripts/dev.sh`. **The Compose file has not been run on this machine: Docker is not installed here** (§1). The cloud part is built in Phase 10.

## 1. Local development

**What this machine has (checked 2026-11-10):** Python 3.10, Node 22 (through nvm), PostgreSQL 16 binaries, git. **It does not have Docker**, and installing it needs an administrator password.

| To do | Command |
|---|---|
| Install Docker (once, by the owner) | follow Docker's Ubuntu instructions, then add the user to the `docker` group |
| Start the three stores | `docker compose up -d postgres mongo qdrant` |
| Start the API and the web app with reload | `scripts/dev.sh` |
| Start everything in containers | `docker compose up --build` |
| Stop, and wipe the data | `docker compose down`, add `-v` to wipe |

`scripts/dev.sh` runs the API from a virtual environment (`backend/.venv`) on port 8110 and the Vite server on 5290, which proxies `/api` to the API. The stores are expected on the ports of `architecture.md` §4.

**Without Docker** the API and the web app still start, and `/readyz` truthfully reports the stores as down.

**PostgreSQL without Docker:** `scripts/local-postgres.sh up` (or `make pg-up`) starts a project-local PostgreSQL 16 on port 5610 from the binaries installed on the machine, with the same database, users and passwords as the Compose service (it runs the same `deploy/postgres/init-roles.sh`). Its data is in `.pgdata/`, not in git. `down`, `status`, `psql` and `wipe` are its other commands. Use one or the other, not both: they share the port.

**MongoDB and Qdrant** have no such fallback here; they need Docker (or a server installed by the owner).

## 2. The Compose file

| Service | Image | Port | Data |
|---|---|---|---|
| `postgres` | `postgres:16` | 5610 | volume `pgdata`; `bi_app` and `bi_readonly` are created on first start by `deploy/postgres/init-roles.sh` |
| `mongo` | `mongo:7` | 27027 | volume `mongodata` |
| `qdrant` | `qdrant/qdrant` (version pinned) | 6343, 6344 | volume `qdrantdata` |
| `api` | built from `backend/Dockerfile` | 8110 | profile `app` |
| `mcp` | the same image, started as the MCP server | 8111 | profile `app`, from Phase 8 |

**No password is written in the Compose file.** The three database passwords are read from `.env` (`BI_PG_OWNER_PASSWORD`, `BI_PG_APP_PASSWORD`, `BI_PG_READONLY_PASSWORD`), and Compose stops with a message when one is missing. They are used only when the database volume is first created; to change them later, change them in the database or wipe the volume. Every port is published on `127.0.0.1` only, so the development stores (MongoDB and Qdrant run without a password locally) are not reachable from the network.

The stores start with plain `docker compose up -d`; the `app` profile adds the API for a full run in containers. Each service has a health check, and the API waits for the stores to be healthy.

## 3. The image

One multi-stage `Dockerfile`:

1. **web**: Node, `npm ci`, `npm run build` → the static files.
2. **python**: Python 3.12 slim, install the locked dependencies, copy `app/`, copy the built web files to `app/static`.

It runs as a non-root user, has no build tools in the final stage, and starts either `uvicorn app.main:app` (the API, which also serves the web app) or `python -m app.mcp_server`.

## 4. The cloud layout (GCP)

```
                 HTTPS, custom domain
 Internet ────────────────────────────► Cloud Run: bi-api   (public; serves the web app and /api)
                                              │   │
                    internal only             │   └────────► OpenAI API
 Cloud Run: bi-mcp ◄──────────────────────────┤
        │                                     │
        ▼                                     ▼
 Cloud SQL (PostgreSQL 16, private IP)    MongoDB Atlas (on GCP, same region)
        ▲                                 Qdrant Cloud (on GCP, same region)
        │                                 Cloud Storage bucket (uploaded documents)
 Cloud Run Job: bi-migrate
 (runs migrations as bi_owner, before a deploy)
```

| Piece | Service | Notes |
|---|---|---|
| API and web app | Cloud Run `bi-api` | at least 1 instance in production (no cold start for the first question), request timeout 300 s for the answer stream, concurrency about 40 |
| Tools | Cloud Run `bi-mcp` | ingress internal; only `bi-api`'s service account may call it; the only service given the read-only database password |
| PostgreSQL | Cloud SQL, PostgreSQL 16 | private IP through a Serverless VPC connector; automated daily backups and point-in-time recovery |
| MongoDB | MongoDB Atlas | a managed cluster in the same GCP region; its own backups |
| Qdrant | Qdrant Cloud | a managed cluster in the same region; an API key |
| Files | Cloud Storage | one private bucket, objects under `<company>/<document>/…`; the API reads and writes with its service account |
| Secrets | Secret Manager | the secret key, the three database passwords, the Mongo URL, the Qdrant key, the OpenAI key; each service is granted only the ones it needs |
| Images | Artifact Registry | tagged with the git commit |
| Logs and alerts | Cloud Logging and Monitoring | the JSON logs; alerts on error rate, `/readyz`, slow answers and daily spend |

**Who holds which secret:**

| Secret | `bi-api` | `bi-mcp` | `bi-migrate` |
|---|---|---|---|
| `bi_app` password | ✓ | | |
| `bi_readonly` password | | ✓ | |
| `bi_owner` password | | | ✓ |
| Mongo URL | ✓ | | |
| Qdrant key | ✓ (ingestion) | ✓ (search) | |
| OpenAI key | ✓ | ✓ (embedding a search query) | |
| Secret key | ✓ | ✓ (to verify context tokens) | |

**Environments:** `staging` and `prod`, two GCP projects with the same layout. Staging uses the demo seed; production never does.

**Region:** assumed `asia-south1` (Mumbai); the owner confirms it (`architecture.md` §9).

## 5. CI/CD (GitHub Actions)

| When | What runs |
|---|---|
| Every push and pull request | `make ci`; the integration and isolation suites against service containers; the frontend tests and build; `pip-audit`, `npm audit` |
| A change to prompts, model names or the vocabulary files | a reminder to attach the golden-set result; the run itself is started by hand because it costs money |
| A merge to `main` | build the image, push it, run `bi-migrate` on staging, deploy `bi-mcp` and `bi-api` to staging, wait for `/readyz`, run the smoke journey |
| A version tag | the same to production, after a manual approval |

GitHub authenticates to GCP with Workload Identity Federation: there is no long-lived key in the repository's secrets.

**Rolling back:** Cloud Run keeps earlier revisions; traffic is moved back with one command. Migrations are written so the previous revision still works with the new schema (add first, remove in a later release).

## 6. Migrations

- Numbered files in `backend/app/store/migrations/`: `0001_init.sql`, `0002_…`. Applied in order inside a transaction each, recorded in `app.schema_migrations`.
- Locally and in tests they run at start. In the cloud they run only in the `bi-migrate` job, as `bi_owner`.
- Until the first production deploy there is one schema file, edited in place and re-created with a reset. After it, every change is a new file and none is edited.
- Any new `biz` table must come with its `tenant_id`, its composite key, its policy and its grants. A test fails when a `biz` table has no forced row security.

## 7. Backups and restore

| Store | Backup | Restore proven by |
|---|---|---|
| Cloud SQL | daily, 14 days kept, point-in-time recovery | a restore to a new instance, done once in Phase 10 and timed |
| Atlas | the provider's continuous backup | the same |
| Qdrant | daily snapshots; **and it can be rebuilt** from the stored files and the examples in MongoDB | a rebuild script, run once in Phase 10 |
| Cloud Storage | object versioning, 30 days | |

## 8. Operations

| Task | How |
|---|---|
| Create a company | the platform admin's page, or `POST /api/v1/platform/tenants` |
| Deactivate a company | the same page; sign-in stops at once |
| Delete a company for good | a script that removes its rows from PostgreSQL (by `tenant_id`, in dependency order), its MongoDB documents, its Qdrant points and its files, and prints the counts. Run by hand, never from the web |
| Rotate the secret key | set the new one; everyone signs in again |
| Rotate a database password | change it in the database and in Secret Manager; deploy a new revision |
| Change the model | set `BI_LLM_MODEL` on staging, run the golden set, then production |
| Look into a bad answer | the admin's Activity page shows the question, the SQL and the outcome; the message's trace in MongoDB shows each step; the request id ties them to the logs |

## 9. What it costs to run (orders of magnitude, to be measured)

| Item | Driver |
|---|---|
| The model | by far the largest and the only one that grows with use: a data question is two strong-model calls and one fast one; a "why" question about three strong calls. The daily limits per company cap it |
| Cloud Run | small: one always-on instance and bursts |
| Cloud SQL, Atlas, Qdrant Cloud | the smallest production tiers are enough for the first companies |

Real figures are recorded here after the Phase 9 load run and the first month on staging. They are not guessed in advance.
