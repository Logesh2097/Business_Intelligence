# Phase 5 — History and reports

**Goal:** conversations are kept, and a good answer becomes a report that can be refreshed and shared.
**Read:** `docs/plan.md` Phase 5, `design/data-model.md` §5, `docs/api.md` §7–8, `design/security.md` §3.

## Order of work (test after each step)

1. `store/mongo.py`: the client, the indexes, every function with the company first; the static test for `tenant_id`.
2. Conversations and messages saved by the chat routes; follow-ups from the last six turns.
3. Routes for conversations, feedback and message export.
4. Reports: save, list, open (the snapshot rule), refresh under the caller's scope, share, export with neutralised cells, delete.
5. Web: the conversation list, Reports, the report page, feedback buttons.
6. Journey 3.

## Acceptance

The gate of this phase in `docs/plan.md`. Every box ticked with its evidence, or explained.

## Do not

Show a stored snapshot to a user whose scope is narrower than the author's. Learn anything automatically from feedback.
