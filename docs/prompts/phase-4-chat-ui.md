# Phase 4 — The chat screen

**Goal:** a person asks in the browser and watches the answer arrive.
**Read:** `docs/plan.md` Phase 4, `docs/api.md` §5–6, `design/ui.md` §3–5, `design/agents.md` §9.

## Order of work (test after each step)

1. `POST /chat/stream`: the events in order, the ping, cancel on disconnect and on Stop; tests on the stream, by role.
2. `api/sse.ts` with its parser tests (split and joined chunks).
3. `lib/format.ts`, `lib/chart.ts` with tests for each chart type.
4. The answer card in each state; the chat page; suggested questions.
5. Sanitised Markdown; the test with HTML and an image link in a result.
6. Journeys 1 and 2. Look at it on both widths and themes.

## Acceptance

The gate of this phase in `docs/plan.md`. Every box ticked with its evidence, or explained.

## Do not

Render model text as HTML. Send the query to a viewer and hide it in the browser. Let the model write chart code.
