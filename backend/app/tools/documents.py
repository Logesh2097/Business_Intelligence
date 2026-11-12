"""search_documents: passages of the caller's own company that are close to a query."""

from __future__ import annotations

from app.core.errors import PermissionDeniedError, UnavailableError
from app.rag.retrieve import MAX_K, Passage, search
from app.tools.context import ToolContext

NOT_SET_UP = "Document search is not set up on this server."


async def search_documents(ctx: ToolContext, query: str, k: int = MAX_K) -> list[Passage]:
    """Find passages in the company's uploaded documents.

    The company is the caller's, taken from `ctx`. There is no argument for it.
    """
    if not ctx.caller.can("documents:read"):
        raise PermissionDeniedError()
    if ctx.qdrant is None or ctx.embedder is None:
        raise UnavailableError(NOT_SET_UP)
    return await search(
        query,
        tenant_id=str(ctx.caller.tenant_id),
        qdrant=ctx.qdrant,
        embedder=ctx.embedder,
        k=k,
        min_score=ctx.settings.rag_min_score,
    )
