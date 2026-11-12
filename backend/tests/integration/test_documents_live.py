"""LIVE: the document stores against real servers. Each test is skipped, with the
reason, when its server is not running.

- Qdrant: the chunk store's requests really work, and the company filter really holds.
- MongoDB: the document records, every query naming the company.

Mocked equivalents: tests/unit/test_qdrant_requests.py, tests/unit/test_rag_pipeline.py.
The embedder is the word-based fake: no model is called.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest

from app.core.config import Settings
from app.llm.embeddings import FakeEmbedder
from app.rag.retrieve import search
from app.store import documents
from app.store import qdrant as qdrant_store
from app.store.connections import Stores
from app.store.qdrant import ChunkPoint


def _tenant() -> str:
    return str(uuid.uuid4())  # a company of this test only; nothing else is touched


async def _store(stores: Stores, tenant: str, document: str, texts: list[str]) -> None:
    vectors = await FakeEmbedder().embed(texts)
    await stores.qdrant.replace_document(
        tenant_id=tenant,
        document_id=document,
        chunks=[
            ChunkPoint(document, i, v, "live.txt", "Live test", None, t)
            for i, (t, v) in enumerate(zip(texts, vectors, strict=True))
        ],
    )


@pytest.fixture
def scratch_collection(monkeypatch: pytest.MonkeyPatch, base_settings: Settings) -> Iterator[str]:
    """A collection of this test only. The real `doc_chunks` is never touched: it has the
    real embedding model's vector size, and the fake embedder's is different."""
    name = f"live_test_{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(qdrant_store, "COLLECTION", name)
    yield name
    httpx.delete(f"{base_settings.qdrant_url.rstrip('/')}/collections/{name}", timeout=10)


@pytest.mark.usefixtures("need_qdrant", "scratch_collection")
async def test_chunks_are_stored_found_replaced_and_deleted_per_company(stores: Stores) -> None:
    mine, theirs, document = _tenant(), _tenant(), str(uuid.uuid4())
    embedder = FakeEmbedder()
    try:
        await _store(
            stores, mine, document, ["Our refund window is 14 days.", "Outlets open at 8."]
        )
        await _store(stores, theirs, document, ["Our refund window is 90 days."])

        async def find(tenant: str) -> list[str]:
            found = await search(
                "refund window", tenant_id=tenant, qdrant=stores.qdrant, embedder=embedder,
                min_score=0.1,
            )  # fmt: skip
            return [p.text for p in found]

        assert await find(mine) == ["Our refund window is 14 days."]
        assert await find(theirs) == ["Our refund window is 90 days."]
        assert await find(_tenant()) == []

        await _store(stores, mine, document, ["The refund window is now 30 days."])  # replace
        assert await find(mine) == ["The refund window is now 30 days."]
        assert await find(theirs) == ["Our refund window is 90 days."]  # same document id, kept

        await stores.qdrant.delete_document(tenant_id=mine, document_id=document)
        assert await find(mine) == []
        assert await find(theirs) == ["Our refund window is 90 days."]
    finally:
        for tenant in (mine, theirs):
            await stores.qdrant.delete_document(tenant_id=tenant, document_id=document)


@pytest.mark.usefixtures("need_mongo")
async def test_document_records_are_kept_per_company(stores: Stores) -> None:
    mine, theirs = _tenant(), _tenant()
    made = await documents.create(
        stores.mongo, tenant_id=mine, document_id=documents.new_id(), uploaded_by="live-test",
        filename="live.txt", title="Live test", kind="txt", size=1, sha256=uuid.uuid4().hex,
        storage_path="none",
    )  # fmt: skip
    try:
        assert (await documents.find(stores.mongo, tenant_id=mine, document_id=made.id)) == made
        assert await documents.find(stores.mongo, tenant_id=theirs, document_id=made.id) is None
        assert [d.id for d in await documents.list_all(stores.mongo, tenant_id=mine)] == [made.id]
        assert await documents.list_all(stores.mongo, tenant_id=theirs) == []
        await documents.set_status(
            stores.mongo, tenant_id=mine, document_id=made.id, status="ready", chunk_count=2
        )
        found = await documents.find(stores.mongo, tenant_id=mine, document_id=made.id)
        assert found is not None and (found.status, found.chunk_count) == ("ready", 2)
        assert not await documents.mark_deleted(stores.mongo, tenant_id=theirs, document_id=made.id)
        assert await documents.mark_deleted(stores.mongo, tenant_id=mine, document_id=made.id)
        assert await documents.count(stores.mongo, tenant_id=mine) == 0
    finally:
        async with stores.mongo.guard():
            await stores.mongo.collection(documents.COLLECTION).delete_many({"tenant_id": mine})
