"""REAL DRIVERS, NO SERVER: the requests the Qdrant store sends, read from a stand-in
transport. They have NOT been run against a real Qdrant server.

What is proved: every search and every delete carries the company filter, built from
the function's own argument; the company in a stored point is written by the store;
and a point of another company is dropped if a server ever returned one.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import UnavailableError
from app.store.qdrant import COLLECTION, ChunkPoint, Qdrant, VectorSizeError, point_id

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER = "99999999-9999-9999-9999-999999999999"
SETTINGS = Settings(_env_file=None, env="test", qdrant_api_key="dummy-qdrant-key")


class Server:
    """Records every request; answers as a Qdrant with or without the collection."""

    def __init__(
        self, *, exists: bool = True, size: int = 3, points: list[dict[str, Any]] | None = None
    ) -> None:  # noqa: E501
        self.exists, self.size, self.points = exists, size, points or []
        self.requests: list[tuple[str, str, Any]] = []
        self.fail_with: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        assert request.headers["api-key"] == "dummy-qdrant-key"
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"status": {"error": "secret detail"}})
        path = request.url.path
        if path == f"/collections/{COLLECTION}" and request.method == "GET":
            if not self.exists:
                return httpx.Response(404, json={})
            config = {"params": {"vectors": {"size": self.size, "distance": "Cosine"}}}
            return httpx.Response(200, json={"result": {"config": config}})
        if path == f"/collections/{COLLECTION}" and request.method == "PUT":
            self.exists = True
            return httpx.Response(200, json={"result": True})
        if path.endswith("/points/query"):
            if not self.exists:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"result": {"points": self.points}})
        if path.endswith("/points/delete") and not self.exists:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"result": {"status": "ok"}})

    def bodies(self, suffix: str) -> list[Any]:
        return [body for _, path, body in self.requests if path.endswith(suffix)]


def store(server: Server) -> Qdrant:
    return Qdrant(SETTINGS, transport=httpx.MockTransport(server))


def chunk(index: int = 0, document: str = "doc-1") -> ChunkPoint:
    return ChunkPoint(document, index, [0.1, 0.2, 0.3], "f.pdf", "Title", 2, f"text {index}")


def point(tenant: str, text: str = "t", score: float = 0.8) -> dict[str, Any]:
    payload = {"tenant_id": tenant, "document_id": "doc-1", "chunk_index": 0, "filename": "f.pdf",
               "title": "Title", "page": 2, "text": text}  # fmt: skip
    return {"id": "x", "score": score, "payload": payload}


async def test_a_search_always_carries_the_company_filter() -> None:
    server = Server(points=[point(TENANT, "mine")])
    hits = await store(server).search_chunks(
        tenant_id=TENANT, vector=[1.0, 0.0, 0.0], limit=5, min_score=0.3
    )
    (body,) = server.bodies("/points/query")
    assert body["filter"] == {"must": [{"key": "tenant_id", "match": {"value": TENANT}}]}
    assert (body["limit"], body["score_threshold"], body["with_payload"]) == (5, 0.3, True)
    assert [(h.text, h.page, h.score) for h in hits] == [("mine", 2, 0.8)]


async def test_a_point_of_another_company_is_dropped_even_if_the_server_returns_it() -> None:
    server = Server(points=[point(OTHER, "theirs"), point(TENANT, "mine")])
    hits = await store(server).search_chunks(tenant_id=TENANT, vector=[1.0], limit=5, min_score=0)
    assert [h.text for h in hits] == ["mine"]


@pytest.mark.parametrize("tenant", ["", None])
async def test_no_operation_runs_without_a_company(tenant: Any) -> None:
    server = Server()
    qdrant = store(server)
    with pytest.raises(ValueError):
        await qdrant.search_chunks(tenant_id=tenant, vector=[1.0], limit=5, min_score=0)
    with pytest.raises(ValueError):
        await qdrant.delete_document(tenant_id=tenant, document_id="doc-1")
    with pytest.raises(ValueError):
        await qdrant.replace_document(tenant_id=tenant, document_id="doc-1", chunks=[chunk()])
    assert not server.bodies("/points/query") and not server.bodies("/points")


async def test_storing_a_document_deletes_its_old_points_then_writes_the_new_ones() -> None:
    server = Server()
    await store(server).replace_document(
        tenant_id=TENANT, document_id="doc-1", chunks=[chunk(0), chunk(1)]
    )
    (deleted,) = server.bodies("/points/delete")
    assert deleted["filter"]["must"] == [
        {"key": "tenant_id", "match": {"value": TENANT}},
        {"key": "document_id", "match": {"value": "doc-1"}},
    ]
    (written,) = server.bodies("/points")
    assert [p["id"] for p in written["points"]] == [
        point_id(TENANT, "doc-1", 0), point_id(TENANT, "doc-1", 1),
    ]  # fmt: skip
    assert all(p["payload"]["tenant_id"] == TENANT for p in written["points"])
    assert written["points"][0]["payload"]["text"] == "text 0"
    order = [path.rsplit("/", 1)[-1] for _, path, _ in server.requests]
    assert order.index("delete") < order.index("points")


def test_a_point_id_is_stable_and_differs_between_companies() -> None:
    assert point_id(TENANT, "d", 1) == point_id(TENANT, "d", 1)
    assert point_id(TENANT, "d", 1) != point_id(OTHER, "d", 1)


async def test_a_chunk_of_another_document_cannot_be_stored_under_this_one() -> None:
    with pytest.raises(ValueError):
        await store(Server()).replace_document(
            tenant_id=TENANT, document_id="doc-1", chunks=[chunk(0, document="doc-2")]
        )


async def test_the_collection_is_created_once_with_the_company_as_its_tenant_key() -> None:
    server = Server(exists=False)
    qdrant = store(server)
    await qdrant.replace_document(tenant_id=TENANT, document_id="doc-1", chunks=[chunk()])
    await qdrant.replace_document(tenant_id=TENANT, document_id="doc-2", chunks=[chunk(0, "doc-2")])
    created = [b for m, p, b in server.requests if m == "PUT" and p == f"/collections/{COLLECTION}"]
    assert created == [{"vectors": {"size": 3, "distance": "Cosine"}}]
    indexes = server.bodies("/index")
    assert {
        "field_name": "tenant_id",
        "field_schema": {"type": "keyword", "is_tenant": True},
    } in indexes  # noqa: E501
    assert len(indexes) == 2


async def test_a_different_vector_size_is_refused_with_a_reason() -> None:
    with pytest.raises(VectorSizeError, match="embedding model has changed"):
        await store(Server(size=1536)).replace_document(
            tenant_id=TENANT, document_id="doc-1", chunks=[chunk()]
        )


async def test_before_any_document_exists_a_search_is_empty_and_a_delete_is_quiet() -> None:
    qdrant = store(Server(exists=False))
    assert await qdrant.search_chunks(tenant_id=TENANT, vector=[1.0], limit=5, min_score=0) == []
    await qdrant.delete_document(tenant_id=TENANT, document_id="doc-1")


async def test_a_server_error_is_a_503_and_its_detail_is_not_passed_on() -> None:
    server = Server()
    server.fail_with = 500
    with pytest.raises(UnavailableError) as raised:
        await store(server).search_chunks(tenant_id=TENANT, vector=[1.0], limit=5, min_score=0)
    assert "secret detail" not in raised.value.message
    assert "not available" in raised.value.message
