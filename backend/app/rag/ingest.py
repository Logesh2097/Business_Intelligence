"""One document from bytes to searchable chunks, and its record's status.

    extract -> chunk -> embed (title in front of each chunk) -> Qdrant -> ready

Any failure leaves the record `failed` with a sentence the user can read; nothing of a
failed document stays in Qdrant. Runs inside the upload request (rag.md §2).
"""

from __future__ import annotations

import asyncio
import logging

from app.core.errors import AppError
from app.llm.base import LLMError
from app.llm.embeddings import Embedder
from app.rag.chunk import chunk
from app.rag.extract import ExtractionFailed, extract
from app.store import documents
from app.store.documents import DocumentRecord
from app.store.mongo import Mongo
from app.store.qdrant import ChunkPoint, Qdrant

log = logging.getLogger("bi.rag")

EMBEDDING_DOWN = "The document could not be indexed right now. Try 'Re-index' in a few minutes."
EMBEDDING_NOT_SET_UP = "Document indexing is not set up: no embedding model is configured."
TOO_LONG = "This document is too long to index."


def _extract_and_chunk(data: bytes, kind: str) -> tuple[list[tuple[int | None, str]], int | None]:
    pages = extract(data, kind)
    chunks = chunk(pages)
    page_count = len(pages) if kind == "pdf" else None
    return [(c.page, c.text) for c in chunks], page_count


async def ingest(
    record: DocumentRecord, data: bytes, *, mongo: Mongo, qdrant: Qdrant, embedder: Embedder
) -> tuple[str, str | None]:
    """Index the document. Returns its final (status, error). Never raises for the file."""
    tenant_id, document_id = record.tenant_id, record.id

    async def finish(status: str, error: str | None = None, chunks: int = 0,
                     pages: int | None = None) -> tuple[str, str | None]:  # fmt: skip
        await documents.set_status(
            mongo, tenant_id=tenant_id, document_id=document_id, status=status, error=error,
            chunk_count=chunks, page_count=pages,
        )  # fmt: skip
        log.info("ingested", extra={"status": status, "chunks": chunks, "kind": record.kind})
        return status, error

    try:
        pieces, page_count = await asyncio.to_thread(_extract_and_chunk, data, record.kind)
    except ExtractionFailed as failure:
        return await finish("failed", failure.reason)
    except ValueError:
        return await finish("failed", TOO_LONG)

    try:
        # the title goes in front, so a chunk still "knows" which document it is from
        vectors = await embedder.embed([f"{record.title}\n\n{text}" for _, text in pieces])
        await qdrant.replace_document(
            tenant_id=tenant_id,
            document_id=document_id,
            chunks=[
                ChunkPoint(
                    document_id=document_id,
                    chunk_index=index,
                    vector=vector,
                    filename=record.filename,
                    title=record.title,
                    page=page,
                    text=text,
                )
                for index, ((page, text), vector) in enumerate(zip(pieces, vectors, strict=True))
            ],
        )
    except LLMError as failure:
        reason = EMBEDDING_NOT_SET_UP if str(failure) == "not configured" else EMBEDDING_DOWN
        return await finish("failed", reason)
    except AppError as failure:
        return await finish("failed", failure.message)
    return await finish("ready", None, len(pieces), page_count)
