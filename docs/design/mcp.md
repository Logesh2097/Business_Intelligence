# The MCP server

**Status:** built on 2026-11-12 (the owner's Phase 9) and run for real: the server process, the SDK client, the token and the business tools on PostgreSQL. What is and is not done:

| Part | State |
|---|---|
| The tool registry (`app/tools/registry.py`): 14 tools, one execution path (`call_tool`) | built, unit-tested and live-tested on PostgreSQL |
| Context tokens (`core/context_token.py`) | built; 15 tests |
| The MCP server (`app/mcp_server/`, `make mcp`, port 8111) | built on the `mcp` SDK **version 2** (`MCPServer`; `FastMCP` is its version 1 name). Tested with the SDK's client over HTTP, in a thread and as a real process |
| REST doorway `GET /api/v1/tools`, `POST /api/v1/tools/{name}` | built; what the web app uses |
| The agents through MCP (`BI_TOOLS_TRANSPORT=mcp`) | built: the SQL analyst (every domain agent and the general route) sends each query to `run_query`, and the document agent uses `search_documents`. The golden set passes 58 of 58 through a real MCP server process with the scripted model (`tests/evals/test_golden_mcp.py`, part of `make eval-check`). Not tried with a real model |
| `find_similar_questions` | not built: there is no store of example questions yet |
| `langchain-mcp-adapters` | not used. The SDK's own client is enough for calling one tool; no extra dependency |
| Compose service, Cloud Run service | not added: Docker is not available where this was built |
| An MCP inspector | not run. The SDK client lists the 14 tools with schemas and descriptions in a test |

**The tools as built.** The six of §2 minus `find_similar_questions`, plus nine business tools asked for in the owner's Phase 9 brief:

| Group | Tools | Notes |
|---|---|---|
| business | `sales_summary`, `sales_trend`, `revenue_by_outlet`, `top_products`, `inventory_status`, `customer_insights`, `visits_by_segment`, `product_information`, `business_summary` | fixed SQL written by us (`store/business.py`), values as bind parameters, run as `bi_readonly` under row security. A period is a name from a fixed list; code turns it into dates |
| catalog | `list_tables`, `describe_table`, `get_metric_definitions` | read the vocabulary; no database |
| query | `run_query` | guard, aggregation check, correctness check, then the database; every attempt logged. Optional `only_tables` narrows what may be read (a domain agent's tables; it can never add one) and `asked` is the question for the log. A person needs `sql:view` to call it (the REST doorway). The agent pipeline needs only `chat:ask`: that is how any user's question is answered. The MCP server counts as the pipeline, because context tokens are made by the API for its own agents and never leave it. If the server is ever opened to outside clients (§6), this must change |
| documents | `search_documents` | needs `documents:read` |

Kept internal on purpose: document upload, re-index and delete; the query-log writer; migrations; the seed. No tool writes.

**Execution rules (`call_tool`).** Input validated by a Pydantic model that forbids unknown arguments; permission checked before anything runs; time bounded by the query timeout plus 5 seconds; one log line per call with the tool, the outcome, the time and the row count, never the arguments. All tools are read-only and repeatable, so there is no duplicate-call protection; a tool that changed something would need an idempotency key.

**Security as built.** Two locks on the server: an ASGI wrapper refuses every HTTP request without a valid token (401, also for listing tools), and each tool call rebuilds the caller from the token again. Headers that claim an identity are ignored. The server is stateless: each request carries its own token. It listens on 127.0.0.1 by default.

**One thing to set.** With `BI_TOOLS_TRANSPORT=mcp`, the API and the MCP process must share `BI_SECRET_KEY`. In development an unset key is random per process, so the two would not agree: set it in `.env`.

**Measured (2026-11-12, local PostgreSQL, one machine):** `sales_summary` took 6.7 ms in process and 17.5 ms over MCP, about 11 ms more per call (a new HTTP connection and MCP handshake each time).

The design as first written follows.

## 1. What MCP is, and why it is here

MCP (Model Context Protocol) is a standard way for an AI application to call tools: the server lists its tools with their inputs, a client calls them, and both sides speak the same protocol whatever the tool does.

For this product it gives two things:

1. **One guarded doorway to the data.** Every check (the SQL guard, the company filter, the row limit, the log) lives in the tools. The agents cannot reach data any other way. In the cloud, `bi-mcp` is the **only** process that holds the read-only database password.
2. **Reuse.** The same tools can later be offered to another MCP client (a desktop assistant, another internal agent) without rewriting them.

**Said plainly:** the agents would work just as well calling the functions in process, and until Phase 8 they do. MCP adds a network hop and a token. It is in the design because it is a requested technology and because the boundary in point 1 is a real security gain. It is built last among the AI phases so that it never blocks progress.

## 2. The tools

| Tool | Input | Returns | Used by |
|---|---|---|---|
| `list_tables` | none | the tables the caller may query, each with a one-line purpose | outside clients (the agents have the schema in their prompt) |
| `describe_table` | `table` | columns with type, meaning and allowed values; how it joins | outside clients |
| `get_metric_definitions` | `names?` | the business words with their SQL and conditions (`semantic-layer.md` §3) | SQL analyst |
| `find_similar_questions` | `question`, `k` (at most 5) | example questions with their SQL, the company's own first | SQL analyst |
| `run_query` | `sql` | `columns`, `rows`, `row_count`, `truncated`, `duration_ms`; or `refused` with a code and a reason | SQL analyst, insight agent |
| `search_documents` | `query`, `k` (at most 8) | passages with text, filename, page, score | document agent, insight agent |

**No tool takes a company, a user or an outlet as input.** There is no argument the model could fill in to ask for someone else's data.

**No tool writes business data.** There is no `execute`, no `insert`, no "admin" tool.

Each tool has a description written for a model to read: what it is for, when to use it, what it returns, and what it refuses.

## 3. How the caller's identity reaches the tools

```
browser ──access token──► bi-api ──┐
                                   │ makes a context token (signed, 2 minutes):
                                   │   tenant_id, user_id, role, outlet scope, message id
                                   ▼
                    MCP client (inside the agent run)
                                   │ Authorization: Bearer <context token>
                                   ▼
                               bi-mcp ── verifies the signature and the expiry,
                                         builds the caller's context from the token,
                                         and passes it to the tool function
```

- The context token is signed by `bi-api` with a key derived from `BI_SECRET_KEY` under its own label, so an access token cannot be used as a context token or the other way round.
- It lives two minutes and is made per question. It is never sent to the browser and never placed in a prompt.
- `bi-mcp` refuses a call with no token, a bad signature or an expired token. It trusts nothing else in the request.
- The tool functions take the context as their first parameter. The MCP layer fills it from the verified token; the direct (in-process) path fills it from the request's session. **The tool code is the same in both.**

## 4. Transport and deployment

| | Choice |
|---|---|
| Server | the official `mcp` Python SDK (`FastMCP`), streamable HTTP transport, mounted at `/mcp` |
| Process | `bi-mcp`: the same Docker image as the API, started with a different command, port 8111 |
| Client | `langchain-mcp-adapters` turns the server's tools into LangChain tools for the agents |
| Network | locally: a Compose service. In the cloud: a second Cloud Run service with **internal ingress only**, callable by the API's service account |
| Switch | `BI_TOOLS_TRANSPORT=direct` or `mcp`. `direct` stays for unit tests and as a fallback |

The insight agent's fixed queries also go through `run_query`. There is no back door for "trusted" SQL.

## 5. What Phase 8 must prove

- The golden question set gives the same results with `BI_TOOLS_TRANSPORT=mcp` as with `direct`.
- A call with no token, a forged token, an expired token and another company's token is refused (four tests).
- The tenant-isolation suite passes through MCP.
- The extra time per tool call is measured and written into `plan.md` (expected: a few milliseconds).
- An MCP inspector can list the six tools and read their descriptions.

## 6. Opening it to outside MCP clients (later, not in the first version)

What it would need, so the design leaves room for it:

| Need | Approach |
|---|---|
| A credential a person can paste into a client | a **personal access token** created in the web app, shown once, stored hashed, tied to the user's role and outlets, revocable |
| Public reachability | a separate public route with its own rate limit; the internal one stays internal |
| Audit | every call already lands in `app.query_log`; the token id would be added |
| Scope | read-only, the same six tools |

Nothing in the first version depends on this.
