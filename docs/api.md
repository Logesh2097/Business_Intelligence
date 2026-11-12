# The API contract

**Status:** served today: §2 (health, readiness, database status) and `POST /api/v1/analytics/query` (§5). Every response follows the error body and the request id of §1. Every other route is the contract its phase must build; the **Phase** column says which. Update this file in the same change as a route.

Base path: `/api/v1`. JSON in and out, UTF-8. The API also publishes its OpenAPI document at `/api/v1/openapi.json` (off in production).

## 1. Conventions

| Topic | Rule |
|---|---|
| **Sign-in** | `Authorization: Bearer <access token>` on every route except health, login and refresh |
| **The company** | always taken from the signed-in user. No route accepts a company id from a company's user |
| **Names** | `snake_case` fields. Ids are UUID strings (PostgreSQL) or 24-character hex strings (MongoDB) |
| **Times** | ISO 8601 with offset (`2026-11-10T14:03:00+05:30`). Dates are `YYYY-MM-DD` |
| **Money** | a JSON number with two decimals, in the company's currency; the currency is in `/auth/me` |
| **Lists** | `?limit=` (default 50, at most 200) and `?cursor=`. The answer is `{"items": [...], "next_cursor": "…" or null}` |
| **Errors** | the body below, with the HTTP status that fits |
| **Not yours** | an id that belongs to another company or another user is `404` |
| **Request id** | every response carries `X-Request-Id`; it is also in the error body and the logs |

**The error body**

```json
{ "error": { "code": "permission_denied", "message": "You do not have access to this.", "request_id": "b0c1…" } }
```

| Status | Code | When |
|---|---|---|
| 400 | `bad_request` | the body or a parameter is malformed |
| 401 | `not_authenticated`, `token_expired` | no token, a bad one, an expired one |
| 403 | `permission_denied` | the role lacks the route's permission |
| 404 | `not_found` | no such item for this user |
| 409 | `conflict` | a duplicate (an email, a document), or an answer already running |
| 413 | `too_large` | a body or an upload over its limit |
| 422 | `validation_failed` | a field failed a rule; `error.fields` lists them |
| 429 | `rate_limited`, `daily_limit_reached` | too many attempts or the company's daily limit; `Retry-After` is set |
| 500 | `internal` | our fault; one generic sentence |
| 503 | `unavailable` | a store or the model is down |

## 2. Health (Phase 0, served)

| Method | Path | Returns |
|---|---|---|
| GET | `/healthz` | `200 {"status": "ok", "version": "…", "env": "dev"}`. The process is alive. Touches nothing |
| GET | `/readyz` | `200` when every store answers, else `503`. Body: `{"status": "ok" or "degraded", "checks": {"postgres": {"ok": true, "ms": 3}, "mongo": {"ok": false, "error": "…"}, "qdrant": {…}}}` |

Both are outside `/api/v1` and need no sign-in. Error text in `checks` is a short class of failure (`unreachable`, `timeout`, `error`), never a connection string.

### Database status (Phase 0B, served)

| Method | Path | Returns |
|---|---|---|
| GET | `/api/v1/system/databases` | always `200`: it is a report, not a probe. Use `/readyz` for monitoring |

```json
{
  "status": "ok",
  "databases": {
    "postgres": {
      "ok": true, "ms": 3.1, "version": "16.4", "database": "bi",
      "pools": {
        "app":      { "ok": true, "min": 0, "max": 10, "size": 1, "available": 1, "waiting": 0 },
        "readonly": { "ok": true, "min": 0, "max": 10, "size": 1, "available": 1, "waiting": 0 }
      }
    },
    "mongo":  { "ok": true, "ms": 4.0, "version": "7.0.14", "database": "bi", "pool_max": 20 },
    "qdrant": { "ok": false, "ms": 52.3, "error": "unreachable" }
  }
}
```

- `status` is `ok` only when all three stores answer **and** the read-only PostgreSQL user can connect.
- A store that is down has `ok: false` and `error` (the same three classes as `/readyz`) and no `version`.
- `size` is the connections a pool holds now, `available` the idle ones, `waiting` the requests queued for one.
- **It never holds** a host, a port, a user name, a password, a key or an error message.
- **Who may call it:** development and test only for now (`404` elsewhere). It shows versions and pool sizes, so it opens to admins (`audit:view`) when sign-in exists.

## 3. Sign-in (Phase 2)

| Method | Path | Permission | Body → Returns |
|---|---|---|---|
| POST | `/auth/login` | open, rate-limited | `{email, password}` → `{access_token, expires_in, user}` and sets the refresh cookie |
| POST | `/auth/refresh` | the refresh cookie | → `{access_token, expires_in}` and a new cookie |
| POST | `/auth/logout` | signed in | → `204`; the cookie is cleared |
| GET | `/auth/me` | signed in | → `user` |
| POST | `/auth/change-password` | signed in | `{current_password, new_password}` → `204`; other sessions end |

**`user`:** `{id, email, full_name, role, permissions: [...], tenant: {id, name, currency, timezone} or null, all_outlets, outlets: [{id, name}]}`

### Who am I, and the development user list (served since the owner's Phase 10)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/me` | `{role, company, currency, timezone, all_outlets, outlets, permissions, scope_note, sign_in}`. No ids. `permissions` is what the web app shows or hides; the server checks every call regardless |
| GET | `/api/v1/dev/users` | `[{email, role, company}]` for the development sign-in page. `404` outside development and test, where `X-Dev-User` is refused too |

## 4. Users and companies (Phase 2)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/admin/users` | `users:manage` | the company's users |
| POST | `/admin/users` | `users:manage` | `{email, full_name, role, all_outlets, outlet_ids, password}` |
| PATCH | `/admin/users/{id}` | `users:manage` | role, outlets, name, `is_active`. Not one's own role; not the last admin |
| POST | `/admin/users/{id}/reset-password` | `users:manage` | `{new_password}`; the user's sessions end |
| GET | `/admin/outlets` | signed in | `[{id, name, code, city}]` within the caller's scope |
| GET | `/platform/tenants` | `tenants:manage` | every company, with counts |
| POST | `/platform/tenants` | `tenants:manage` | `{name, slug, currency, timezone, admin: {email, full_name, password}}` |
| PATCH | `/platform/tenants/{id}` | `tenants:manage` | name, `is_active` |

## 5. Asking (Phases 3, 4, 7)

| Method | Path | Permission | Phase |
|---|---|---|---|
| POST | `/analytics/query` | `chat:ask` | **served.** The whole answer as one JSON body (below) |
| POST | `/chat/stream` | `chat:ask` | 4. The answer as an event stream (§6) |
| POST | `/chat/{message_id}/stop` | `chat:ask` | 4. Ends a running answer |
| GET | `/meta/suggestions` | `chat:ask` | 4. Six questions the company's data can answer |
| GET | `/meta/glossary` | `chat:ask` | 9. The business words and their meaning in plain words |

### `POST /api/v1/analytics/query` (served)

**Who may call it.** Sign-in is not built yet. In `dev` and `test` the caller is named by the header `X-Dev-User: <email of a demo user>`; in every other environment the route answers `401`. The company and the outlets always come from that user, never from the request.

**Request**

```json
{ "question": "What was our total revenue last month?" }
```

`question` is trimmed; 3 to 2,000 characters.

**Response `200`**

```json
{
  "question": "What was our total revenue last month?",
  "status": "answered",
  "summary": "Revenue last month was INR 946,217.50.",
  "sql": "SELECT ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue FROM orders AS o WHERE o.status = 'completed' AND o.order_date BETWEEN CAST('2026-10-01' AS DATE) AND CAST('2026-10-31' AS DATE) LIMIT 1001",
  "columns": [{ "name": "revenue", "type": "money" }],
  "data": [{ "revenue": 946217.5 }],
  "row_count": 1,
  "truncated": false,
  "assumptions": [],
  "scope_note": null
}
```

| Field | Meaning |
|---|---|
| `status` | `answered` · `no_data` (the query ran and matched nothing) · `unanswerable` (the data cannot answer this; `summary` says why) · `failed` (no safe query could be built in three attempts) · `timeout` (the query ran past its limit) |
| `summary` | the explanation. For every status it is a sentence fit to show the user |
| `sql` | the query that ran, as printed by the guard. **`null` unless the caller has `sql:view`** (analysts and admins); a viewer never receives it. Also `null` when nothing ran |
| `columns` | name and type: `text`, `number`, `money`, `percent`, `date` |
| `data` | the rows, one object per row, at most `BI_QUERY_MAX_ROWS` |
| `truncated` | `true` when more rows existed than the cap |
| `assumptions` | choices the model made that the user did not state |
| `scope_note` | set for a user limited to some outlets, e.g. `Limited to your outlets: Indiranagar, Koramangala.` |

| Status | When |
|---|---|
| `401 not_authenticated` | no caller, an unknown or inactive user, or any call outside `dev` and `test` |
| `422 validation_failed` | the question is missing, too short or too long |
| `503 unavailable` | the model cannot be reached or no key is configured; or the database is down. The message tells three cases apart: not set up, the model's request limit reached ("busy… wait a minute"), and any other failure. Status and code are the same for all three |

A query that is refused, fails or times out is **not** an HTTP error: the answer is `200` with the matching `status`, and the refused SQL and the database's message are never in the response. They are in `app.query_log`.

### The agents, each on its own (served)

| Method | Path | Permission | |
|---|---|---|---|
| GET | `/agents` | `chat:ask` | **served.** The agents, what each is responsible for, and example questions |
| POST | `/agents/{agent}/query` | `chat:ask` | **served.** One question to one agent: `sales_agent`, `inventory_agent` or `customer_agent` |

**`GET /agents`** answers a list:

```json
[
  {
    "agent": "sales_agent",
    "title": "Sales agent",
    "responsibilities": ["Analyse total sales and revenue", "Identify top-selling products", "…"],
    "example_questions": ["What was our total revenue last month?", "…"]
  }
]
```

**`POST /agents/{agent}/query`.** Request: `{"question": "How much did sales increase compared to last month?"}` (3 to 2,000 characters, as for `/analytics/query`).

```json
{
  "agent": "sales_agent",
  "question": "How much did sales increase compared to last month?",
  "status": "answered",
  "summary": "Sales for the current period reached 293227.5 INR compared to 345118.0 INR in the previous month, resulting in a decrease of 51890.5 INR or 15.0 percent.",
  "insights": ["Sales decreased by 15.0 percent compared to last month."],
  "sql": "SELECT ROUND(SUM(total_amount - tax_amount) FILTER(WHERE …), 2) AS current_period_revenue, … FROM orders LIMIT 1001",
  "columns": [{"name": "current_period_revenue", "type": "money"}, {"name": "change_percent", "type": "number"}],
  "data": [{"current_period_revenue": 293227.5, "previous_period_revenue": 345118.0, "change": -51890.5, "change_percent": -15.0}],
  "row_count": 1,
  "truncated": false,
  "assumptions": ["Since the current month is not finished, it is compared with the same days of the previous month (2026-10-01 to 2026-10-11)."],
  "scope_note": null
}
```

| Field | Meaning |
|---|---|
| `agent` | which agent answered |
| `status` | `answered`, `no_data` (the query ran, no rows), `unanswerable` (not this agent's area, or not in the data; `summary` says which), `failed` (no safe query after three attempts), `timeout` (the query, or the whole answer, ran past its limit) |
| `summary` | one to three sentences. Every figure in it is a value of `data`, checked by code |
| `insights` | up to four observations, each checked the same way; one with a figure that is not in `data` is dropped. Often empty for a one-row result |
| `sql` | the query that ran; `null` unless the caller has `sql:view` |
| the rest | as for `/analytics/query` |

| Status | When |
|---|---|
| `200` | always when the agent ran, whatever `status` says |
| `401` | no caller |
| `403` | the caller's role cannot ask questions |
| `404` | no agent of that name |
| `422` | the question is missing, too short or too long |
| `503` | the model is down, rate-limited or not set up; or the database is |

### The assistant: the supervisor chooses (served)

| Method | Path | Permission | |
|---|---|---|---|
| POST | `/assistant/query` | `chat:ask` | **served.** One question; a supervisor sends it to one agent, several agents, or the general analytics workflow |

Request: `{"question": "…"}`, as for the other two routes.

```json
{
  "question": "What was our revenue last month and which products are running low on stock?",
  "status": "answered",
  "route": "agents",
  "reason": "The question contains two independent parts concerning revenue and stock levels.",
  "summary": "Your revenue last month was 902538.0 INR. At the Indiranagar outlet, Cappuccino has a stock of 10.0, …",
  "clarification": null,
  "results": [
    {
      "agent": "sales_agent",
      "question": "What was our revenue last month",
      "status": "answered",
      "summary": "Last month's revenue reached 902538.0 INR.",
      "insights": [],
      "sql": "SELECT ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue FROM orders AS o WHERE … LIMIT 1001",
      "columns": [{"name": "revenue", "type": "money"}],
      "data": [{"revenue": 902538.0}],
      "row_count": 1,
      "truncated": false,
      "assumptions": []
    },
    {"agent": "inventory_agent", "question": "which products are running low on stock", "…": "…"}
  ],
  "scope_note": null
}
```

| Field | Meaning |
|---|---|
| `question` | the user's words, unchanged |
| `route` | `agents` (one to three agents), `general` (the analytics workflow of `/analytics/query`, for purchasing, expenses and questions that join areas), `clarify`, `unsupported`, or `undecided` (the supervisor gave no usable decision twice) |
| `reason` | the supervisor's one sentence on why |
| `status` | `answered` · `partial` (some parts answered, some not) · `no_data` · `unanswerable` (the part declined; its own words are in `summary`) · `needs_clarification` · `unsupported` · `failed` · `timeout` |
| `summary` | one part: that part's summary, unchanged. Several: one combined text whose every figure is a value of some part's `data`, or, if that check fails, each part's own summary under its agent's name |
| `clarification` | for `needs_clarification`, the one question to put to the user; otherwise `null` |
| `results` | one entry per part, in the order the supervisor chose. `agent` is an agent's name or `general`; `question` is what that part was asked (the whole question for a single agent and for `general`). The other fields are those of `/agents/{agent}/query`; `insights` is empty for `general` |
| `sql` (in a result) | `null` unless the caller has `sql:view` |

| HTTP status | When |
|---|---|
| `200` | always when the supervisor answered, whatever `status` says: a part that failed is a `failed` result, not an error |
| `401`, `403`, `422` | as for the other routes |
| `503` | the supervisor's own model call could not be made (down, rate-limited, not set up) |

An agent that declines is returned as it is. There is no second attempt with another agent.

### The later routes

**Request:** `{"question": "What was our total revenue last month?", "conversation_id": "…" or null}`. A null conversation starts a new one.

**The answer (`POST /chat`, and the `done` event):**

```json
{
  "conversation_id": "…",
  "message_id": "…",
  "status": "ok",
  "route": "data",
  "answer": {
    "text": "Revenue in October was ₹18,42,300, up 6.1% on September.",
    "tables": [{ "title": "Revenue by outlet", "columns": [{"name": "outlet", "type": "text"}, {"name": "revenue", "type": "money"}], "rows": [["Indiranagar", 512300.0]], "truncated": false }],
    "chart": { "type": "bar", "title": "Revenue by outlet", "x": "outlet", "y": ["revenue"], "series": null, "unit": "money" },
    "recommendations": [{ "text": "…", "based_on": "…" }],
    "queries": [{ "sql": "SELECT …", "row_count": 5, "duration_ms": 41 }],
    "sources": [{ "document_id": "…", "filename": "ops-notes-october.pdf", "page": 2, "quote": "…" }],
    "period": { "from": "2026-10-01", "to": "2026-10-31", "label": "October 2026" },
    "scope_note": null
  }
}
```

- `status` is `ok`, `clarify` (the `text` is a question back to the user; `answer.choices` may list quick replies) or `error`.
- `queries` is present only for callers with `sql:view`.
- Column `type` is one of `text`, `number`, `money`, `percent`, `date`.
- `chart.type` is one of `kpi`, `line`, `bar`, `stacked_bar`, `pie`, `table`.

## 6. The answer stream (Phase 4)

`POST /chat/stream` answers `200` with `Content-Type: text/event-stream`. Each event is `event: <name>` and `data: <one JSON object>`.

| Event | Data | Notes |
|---|---|---|
| `start` | `{conversation_id, message_id}` | first, always |
| `status` | `{step, label}` | `step` is one of `routing`, `writing_query`, `running_query`, `repairing_query`, `searching_documents`, `analysing`, `writing_answer`; `label` is the sentence to show |
| `sql` | `{sql, row_count, duration_ms}` | only for callers with `sql:view` |
| `table` | one table object | may come more than once |
| `chart` | the chart object | at most once |
| `token` | `{text}` | a piece of the answer text, in order |
| `sources` | `[source, …]` | at most once |
| `done` | the full answer object of §5 | last on success |
| `error` | the error object of §1 | last on failure; the question is kept |

A comment line (`: ping`) is sent every 15 seconds so that proxies keep the connection open. A dropped connection stops the run; the client may ask again.

## 7. Conversations and feedback (Phase 5)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/conversations` | `chat:ask` | the caller's own, newest first |
| GET | `/conversations/{id}` | `chat:ask` | the conversation and its messages |
| PATCH | `/conversations/{id}` | `chat:ask` | `{title}` |
| DELETE | `/conversations/{id}` | `chat:ask` | removes it and its messages |
| POST | `/messages/{id}/feedback` | `chat:ask` | `{rating: "up" or "down", comment}`. An `up` on a single-query answer creates a pending example |
| GET | `/messages/{id}/export.csv` | `reports:export` | re-runs the stored query under the caller's scope and streams the full result |

## 8. Reports (Phase 5)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/reports` | `reports:read` | own and shared; `?filter=mine` or `shared` |
| POST | `/reports` | `reports:write` | `{message_id, title, description}`: saves the question, the query, the chart and a snapshot |
| GET | `/reports/{id}` | `reports:read` | the snapshot only if the caller's scope covers the author's; otherwise `snapshot: null` and `needs_refresh: true` |
| POST | `/reports/{id}/refresh` | `reports:read` | re-runs the stored query (through the guard again) under the **caller's** scope; the owner's refresh updates the snapshot |
| PATCH | `/reports/{id}` | `reports:write`, owner | title, description, `shared` |
| DELETE | `/reports/{id}` | `reports:write`, owner | |
| GET | `/reports/{id}/export.csv` | `reports:export` | |

## 9. Documents (served since the owner's Phase 8; verified with mocked stores only)

| Method | Path | Who | Notes |
|---|---|---|---|
| POST | `/api/v1/documents?filename=<name>&title=<optional>` | admin | the file is the request body (any content type). `201` with the document. PDF, DOCX, TXT, MD; at most `BI_DOC_MAX_MB`. Indexing runs inside the request; `status` is `ready` or `failed` (then `error` says why) |
| GET | `/api/v1/documents` | any role | `{documents: [...], can_manage, max_mb, max_documents}`, newest first |
| GET | `/api/v1/documents/{id}` | any role | one document |
| POST | `/api/v1/documents/{id}/reindex` | admin | extract, chunk and embed again from the stored file |
| DELETE | `/api/v1/documents/{id}` | admin | `204`. Removes the passages, the file and the listing |

A document: `id`, `filename`, `title`, `kind` (`pdf`, `docx`, `txt`, `md`), `size`, `status` (`processing`, `ready`, `failed`), `error`, `chunk_count`, `page_count`, `created_at`, `updated_at`.

| Error | When |
|---|---|
| `400 unsupported_file` | not a PDF, DOCX or text file; a spreadsheet or CSV; empty |
| `413 too_large` | over the size limit |
| `409 duplicate_document` | the same bytes were already uploaded by this company |
| `409 document_limit` | the company holds `BI_DOC_MAX_PER_TENANT` documents |
| `409 file_missing` | re-index of a document whose stored file is gone |
| `404 not_found` | no such document in the caller's company (another company's id looks the same) |
| `403 permission_denied` | not an admin, for the three changing calls |
| `503 unavailable` | MongoDB or Qdrant is down |

**The assistant and documents.** `POST /api/v1/assistant/query` gained, additively: `route` may be `documents`; a result's `agent` may be `document_agent`; every result has `sources` (empty except for documents), each `{number, document_id, filename, title, page, quote, score}` where `number` is the `[n]` in the summary. A documents result has no SQL, columns or rows.

## 9b. Tools and activity (served since the owner's Phase 9)

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/api/v1/tools` | any role | `{tools: [{name, title, description, group, input_schema}], transport}`: the tools this caller may use |
| POST | `/api/v1/tools/{name}` | any role; `run_query` needs `sql:view` | body `{"arguments": {...}}`. Returns a tool result |
| GET | `/api/v1/activity?limit=50` | `sql:view` | `{entries: [...], last_24_hours: {status: count}}`: the company's recent AI-written queries from `app.query_log`, newest first |

A tool result: `tool`, `title`, `status` (`ok`, `refused`, `error`, `timeout`), `period` (`{name, start, end}` or null), `columns`, `rows`, `row_count`, `truncated`, `notes`, `scope_note`, `code`, `reason`. Only `run_query` uses the last two and the non-`ok` statuses.

| Error | When |
|---|---|
| `400 invalid_input` | an argument is missing, unknown or out of range. The message names the field, never the value |
| `403 permission_denied` | the role may not use this tool |
| `404 not_found` | no such tool, or no such table for `describe_table` |
| `503 unavailable` | the database is down, or the tool ran past its time limit |

An activity entry: `id`, `created_at`, `asked_by`, `question`, `status` (`ok`, `rejected`, `error`, `timeout`), `reason`, `row_count`, `duration_ms`, `sql`.

### As first designed (kept for reference)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/documents` | `documents:manage` | with status and chunk count |
| POST | `/documents` | `documents:manage` | multipart: `file`, `title`. PDF, DOCX, TXT, MD; at most 20 MB. `409` for a duplicate |
| GET | `/documents/{id}` | `documents:manage` | |
| GET | `/documents/{id}/file` | `documents:manage` | the original file |
| POST | `/documents/{id}/reindex` | `documents:manage` | |
| DELETE | `/documents/{id}` | `documents:manage` | removes the file and its passages |

Every signed-in user's questions can be answered from the company's documents; managing them is the admin's.

## 10. Activity and examples (Phase 9)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/admin/query-log` | `audit:view` | `?from=&to=&user_id=&status=` |
| GET | `/admin/audit-log` | `audit:view` | |
| GET | `/admin/usage` | `audit:view` | questions and tokens per day against the limits |
| GET | `/admin/examples` | `examples:manage` | `?status=pending` |
| POST | `/admin/examples/{id}/approve` | `examples:manage` | checks the SQL through the guard, embeds it, makes it available to this company |
| POST | `/admin/examples/{id}/reject` | `examples:manage` | |
| POST | `/admin/examples/{id}/run` | `examples:manage` | runs it under the admin's scope and returns a table |

## 11. What a response never holds

A password or its hash · a refresh token in a body · another company's or another user's item · `tenant_id` values of anyone else · a private column (customer or supplier contact) · the SQL, for a caller without `sql:view` · a connection string, a stack trace or a prompt.
