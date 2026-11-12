# Phase 6 — Documents

**Goal:** a company uploads its documents and gets answers from them with sources.
**Read:** `docs/plan.md` Phase 6, `design/rag.md` (all), `design/data-model.md` §6, `design/security.md` §6.

## Order of work (test after each step)

1. The file store; type detection from bytes; the limits.
2. `rag/extract`, `rag/chunk` with unit tests on sample files.
3. Qdrant `doc_chunks`; `rag/ingest`; delete and re-index.
4. `tools/search_documents` with the filter added inside; the isolation test.
5. The document agent; citations checked in code; the routing rule.
6. Routes and the Documents page; sources in the answer card; the demo documents in the seed.
7. The RAG evaluation and the planted-instruction test.

## Acceptance

The gate of this phase in `docs/plan.md`. Every box ticked with its evidence, or explained.

## Do not

Accept CSV or Excel as a document. Take the company filter from an argument. Add a reranker or keyword search before the evaluation asks for one.
