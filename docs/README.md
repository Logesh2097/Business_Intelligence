# Documentation index

Everything about the BI Assistant lives here, reviewed and changed like code. **Each fact has exactly one home**; when two docs want it, one links to the other.

| Folder | Audience | Rule |
|---|---|---|
| `product/` | the owner, business users | what the product does, in plain words; no technical content |
| `design/` | engineers | how a thing is built, or should be |
| `prompts/` | the build session | one prompt per phase |

## Start here

| Doc | What it holds |
|---|---|
| [plan.md](plan.md) | **The build plan**: principles, the eleven phases, what each delivers and the gate it must pass, each ticked with the evidence that proves it |
| [api.md](api.md) | **The API contract**: every route, the conventions all of them follow (errors, paging, streaming events), the roles and permissions, the error codes. Updated with every API change |
| [product/overview.md](product/overview.md) | **What the product does**: who uses it, what they can ask, what an answer looks like, what it will not do |
| [prompts/00-start-here.md](prompts/00-start-here.md) | The master prompt and the preflight for a build session |

## Design

| Doc | What it holds |
|---|---|
| [design/architecture.md](design/architecture.md) | The stack (locked), the repository layout, ports, the request path, configuration, decisions taken and what is still open |
| [design/data-model.md](design/data-model.md) | Tenancy, every PostgreSQL table, the database roles and row-level security, the MongoDB collections, the Qdrant collections, the demo seed and the stories planted in it |
| [design/agents.md](design/agents.md) | **The agent workflow**: the five agents, the shared state, the routes, the limits, what each agent may and may not do, the streaming events |
| [design/nl2sql.md](design/nl2sql.md) | **Question to SQL**: the context the model gets, the SQL guard and its rules, execution, self-correction, how examples are learned |
| [design/semantic-layer.md](design/semantic-layer.md) | The business vocabulary: metric definitions, table and column descriptions, time words, the example questions |
| [design/rag.md](design/rag.md) | Documents: upload, text extraction, chunking, embedding, retrieval, citations, deletion |
| [design/mcp.md](design/mcp.md) | The MCP server: its tools, how the tenant reaches it, how the agents call it, what an outside MCP client would need |
| [design/security.md](design/security.md) | Sign-in, tokens, roles, tenant isolation, SQL safety, prompt injection, privacy, limits, the threat table |
| [design/ui.md](design/ui.md) | The pages, the chat screen in detail, charts, states, the components |
| [design/testing.md](design/testing.md) | The test layers, the fake model, the tenant-isolation suite, the golden question set, what each phase must prove |
| [design/deployment.md](design/deployment.md) | Local development, Docker Compose, the GCP layout, CI/CD, migrations, backups, cost |

## Rules for these docs

1. A doc opens with a **Status** line saying what is built and what is only designed. Update it when that changes.
2. Change the doc in the same change as the code it describes.
3. When the code and a doc disagree, that is a bug in one of them. Fix it; do not leave both.
4. A decision that belongs to the owner is listed in `design/architecture.md` §9, not decided silently in code.
