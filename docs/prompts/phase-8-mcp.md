# Phase 8 — MCP

**Goal:** the tools sit behind one guarded doorway.
**Read:** `docs/plan.md` Phase 8, `design/mcp.md` (all). Check the current documentation of the `mcp` SDK and `langchain-mcp-adapters` before writing code; do not rely on memory for their APIs.

## Order of work (test after each step)

1. Context tokens: make, verify, the separate key label; unit tests.
2. `mcp_server/`: the six tools, each calling the same function as the direct path; descriptions written for a model.
3. The client in the agents behind `BI_TOOLS_TRANSPORT`; the `mcp` service in Compose.
4. The four refusal tests; the isolation suite through MCP; the golden set through MCP.
5. Measure the added time per call; list the tools with an MCP inspector.

## Acceptance

The gate of this phase in `docs/plan.md`. Every box ticked with its evidence, or explained.

## Do not

Add a tool that writes. Add a company or user argument. Expose the server to the internet.
