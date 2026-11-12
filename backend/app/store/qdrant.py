"""Qdrant: health, and the one collection of document chunks (docs/design/rag.md).

Plain HTTP against Qdrant's REST API; no client library. Every function that reads,
writes or deletes points takes the company as a required argument and adds the
`tenant_id` filter itself (CLAUDE.md rule 5). There is no way to search without it.

NOT YET RUN AGAINST A REAL QDRANT SERVER: none was available where this was written.
The requests are unit-tested for their shape; `tests/integration/test_qdrant_live.py`
runs them for real when a server is up.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import AppError, UnavailableError

log = logging.getLogger("bi.qdrant")

TIMEOUT_SECONDS = 2.0
WRITE_TIMEOUT_SECONDS = 30.0
COLLECTION = "doc_chunks"
UPSERT_BATCH = 128
_POINT_NAMESPACE = uuid.UUID("6f0c1c1e-5b1d-4a57-9a52-6d3f0d1b7c11")

SEARCH_DOWN = "Document search is not available right now. Please try again."


class VectorSizeError(AppError):
    status, code = 409, "embedding_changed"
    message = (
        "The embedding model has changed since the documents were indexed. "
        "The document index must be rebuilt."
    )


@dataclass(frozen=True)
class ChunkPoint:
    """One chunk to store. The company is not part of it: the store adds it."""

    document_id: str
    chunk_index: int
    vector: list[float]
    filename: str
    title: str
    page: int | None
    text: str


@dataclass(frozen=True)
class Hit:
    document_id: str
    chunk_index: int
    filename: str
    title: str
    page: int | None
    text: str
    score: float


def point_id(tenant_id: str, document_id: str, chunk_index: int) -> str:
    """The same chunk always gets the same id, so ingesting again replaces it."""
    return str(uuid.uuid5(_POINT_NAMESPACE, f"{tenant_id}:{document_id}:{chunk_index}"))


def _tenant_filter(tenant_id: str, document_id: str | None = None) -> dict[str, Any]:
    if not tenant_id:
        raise ValueError("a company is required for every Qdrant operation")
    must: list[dict[str, Any]] = [{"key": "tenant_id", "match": {"value": tenant_id}}]
    if document_id is not None:
        must.append({"key": "document_id", "match": {"value": document_id}})
    return {"must": must}


class Qdrant:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._url = settings.qdrant_url.rstrip("/")
        self._headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
        self._transport = transport  # tests pass a stand-in; None is the network
        self._ready_for: int | None = None  # the vector size the collection is known to have

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float = TIMEOUT_SECONDS,
        missing_ok: bool = False,
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as http:
            response = await http.request(
                method, f"{self._url}{path}", headers=self._headers, json=json
            )
        if missing_ok and response.status_code == 404:
            return response
        response.raise_for_status()
        return response

    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        """A data call: a server that is down or refuses becomes a 503, cause logged."""
        try:
            return await self._request(method, path, **kw)
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            log.warning(
                "qdrant call failed", extra={"kind": type(exc).__name__, "http_status": status}
            )
            raise UnavailableError(SEARCH_DOWN) from exc

    # -- health ------------------------------------------------------------------------

    async def ping(self) -> None:
        """Raise unless the server says it is ready."""
        await self._request("GET", "/readyz")

    async def server_version(self) -> str:
        return str((await self._request("GET", "/")).json().get("version", ""))

    # -- the collection ----------------------------------------------------------------

    async def _ensure_collection(self, size: int) -> None:
        if self._ready_for == size:
            return
        found = await self._call("GET", f"/collections/{COLLECTION}", missing_ok=True)
        if found.status_code == 404:
            await self._call(
                "PUT",
                f"/collections/{COLLECTION}",
                json={"vectors": {"size": size, "distance": "Cosine"}},
                timeout=WRITE_TIMEOUT_SECONDS,
            )
            for field, schema in (
                ("tenant_id", {"type": "keyword", "is_tenant": True}),
                ("document_id", {"type": "keyword"}),
            ):
                await self._call(
                    "PUT",
                    f"/collections/{COLLECTION}/index?wait=true",
                    json={"field_name": field, "field_schema": schema},
                    timeout=WRITE_TIMEOUT_SECONDS,
                )
        else:
            vectors = found.json().get("result", {}).get("config", {}).get("params", {})
            existing = (vectors.get("vectors") or {}).get("size")
            if existing != size:
                raise VectorSizeError()
        self._ready_for = size

    # -- chunks ------------------------------------------------------------------------

    async def replace_document(
        self, *, tenant_id: str, document_id: str, chunks: list[ChunkPoint]
    ) -> None:
        """Store these chunks as the whole of one document, replacing what was there."""
        if any(chunk.document_id != document_id for chunk in chunks):
            raise ValueError("every chunk must belong to the document being stored")
        if not chunks:
            await self.delete_document(tenant_id=tenant_id, document_id=document_id)
            return
        await self._ensure_collection(len(chunks[0].vector))
        await self.delete_document(tenant_id=tenant_id, document_id=document_id)
        for start in range(0, len(chunks), UPSERT_BATCH):
            points = [
                {
                    "id": point_id(tenant_id, document_id, chunk.chunk_index),
                    "vector": chunk.vector,
                    "payload": {
                        "tenant_id": tenant_id,  # written here, never taken from the chunk
                        "document_id": document_id,
                        "chunk_index": chunk.chunk_index,
                        "filename": chunk.filename,
                        "title": chunk.title,
                        "page": chunk.page,
                        "text": chunk.text,
                    },
                }
                for chunk in chunks[start : start + UPSERT_BATCH]
            ]
            await self._call(
                "PUT",
                f"/collections/{COLLECTION}/points?wait=true",
                json={"points": points},
                timeout=WRITE_TIMEOUT_SECONDS,
            )

    async def delete_document(self, *, tenant_id: str, document_id: str) -> None:
        await self._call(
            "POST",
            f"/collections/{COLLECTION}/points/delete?wait=true",
            json={"filter": _tenant_filter(tenant_id, document_id)},
            timeout=WRITE_TIMEOUT_SECONDS,
            missing_ok=True,  # no collection yet: nothing to delete
        )

    async def search_chunks(
        self, *, tenant_id: str, vector: list[float], limit: int, min_score: float
    ) -> list[Hit]:
        """The chunks of THIS company nearest to the vector. The filter is not optional."""
        response = await self._call(
            "POST",
            f"/collections/{COLLECTION}/points/query",
            json={
                "query": vector,
                "filter": _tenant_filter(tenant_id),
                "limit": limit,
                "score_threshold": min_score,
                "with_payload": True,
            },
            missing_ok=True,  # no collection yet: no documents
        )
        if response.status_code == 404:
            return []
        result = response.json().get("result") or {}
        points = result.get("points", []) if isinstance(result, dict) else result
        hits = []
        for point in points:
            payload = point.get("payload") or {}
            if payload.get("tenant_id") != tenant_id:
                # cannot happen with the filter above; if it ever does, drop it and say so
                log.error("qdrant returned a point of another company; dropped")
                continue
            hits.append(
                Hit(
                    document_id=str(payload.get("document_id", "")),
                    chunk_index=int(payload.get("chunk_index", 0)),
                    filename=str(payload.get("filename", "")),
                    title=str(payload.get("title", "")),
                    page=payload.get("page"),
                    text=str(payload.get("text", "")),
                    score=float(point.get("score", 0.0)),
                )
            )
        return hits
