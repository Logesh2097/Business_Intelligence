"""A question to the passages of the caller's own company (docs/design/rag.md §3)."""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.embeddings import Embedder
from app.store.qdrant import Hit, Qdrant

MAX_K = 8
MAX_PER_DOCUMENT = 3
MAX_QUERY_CHARS = 2000


@dataclass(frozen=True)
class Passage:
    document_id: str
    filename: str
    title: str
    page: int | None
    chunk_index: int
    text: str
    score: float


def select(hits: list[Hit], k: int, min_score: float) -> list[Passage]:
    """Best first; nothing under the threshold; at most three from one document."""
    taken: dict[str, int] = {}
    out: list[Passage] = []
    for hit in sorted(hits, key=lambda h: h.score, reverse=True):
        if hit.score < min_score or taken.get(hit.document_id, 0) >= MAX_PER_DOCUMENT:
            continue
        taken[hit.document_id] = taken.get(hit.document_id, 0) + 1
        out.append(
            Passage(
                document_id=hit.document_id,
                filename=hit.filename,
                title=hit.title,
                page=hit.page,
                chunk_index=hit.chunk_index,
                text=hit.text,
                score=round(hit.score, 4),
            )
        )
        if len(out) >= k:
            break
    return out


async def search(
    query: str,
    *,
    tenant_id: str,
    qdrant: Qdrant,
    embedder: Embedder,
    k: int = MAX_K,
    min_score: float,
) -> list[Passage]:
    """`tenant_id` comes from the caller's session, set by our code. Never from a model."""
    query = query.strip()[:MAX_QUERY_CHARS]
    if not query:
        return []
    k = max(1, min(k, MAX_K))
    vector = (await embedder.embed([query]))[0]
    # ask for more than k, so that the per-document cap still leaves k
    hits = await qdrant.search_chunks(
        tenant_id=tenant_id, vector=vector, limit=k * MAX_PER_DOCUMENT, min_score=min_score
    )
    return select(hits, k, min_score)
