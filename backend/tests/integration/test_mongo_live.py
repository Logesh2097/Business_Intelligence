"""LIVE: MongoDB connectivity against a real server.

Mocked equivalents: tests/unit/test_mongo_mocked.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.config import Settings
from app.core.errors import UnavailableError
from app.store import health
from app.store.connections import Stores
from app.store.mongo import Mongo
from tests.integration.conftest import make_settings

pytestmark = pytest.mark.usefixtures("need_mongo")


@pytest.fixture
async def scratch(stores: Stores) -> AsyncIterator[str]:
    """A collection for one test, dropped afterwards."""
    name = f"live_test_{uuid.uuid4().hex[:8]}"
    yield name
    await stores.mongo.collection(name).drop()


async def test_the_server_answers_and_is_version_7(stores: Stores) -> None:
    await stores.mongo.ping()
    assert (await stores.mongo.server_version()).split(".")[0] == "7"


async def test_the_readiness_check_passes(stores: Stores) -> None:
    result = await health.run_check("mongo", health.check_mongo, stores)
    assert result.ok is True and result.error is None


async def test_a_document_can_be_written_read_and_removed(stores: Stores, scratch: str) -> None:
    collection = stores.mongo.collection(scratch)
    async with stores.mongo.guard():
        await collection.insert_one({"_id": "a", "tenant_id": "t1", "title": "hello"})
        found = await collection.find_one({"tenant_id": "t1", "_id": "a"})
        other_tenant = await collection.find_one({"tenant_id": "t2", "_id": "a"})
        await collection.delete_one({"tenant_id": "t1", "_id": "a"})
        remaining = await collection.count_documents({})
    assert found == {"_id": "a", "tenant_id": "t1", "title": "hello"}
    assert other_tenant is None
    assert remaining == 0


async def test_the_client_can_be_closed_and_a_new_one_connects(base_settings: Settings) -> None:
    first = Mongo(base_settings)
    await first.ping()
    await first.close()
    second = Mongo(base_settings)
    try:
        await second.ping()
    finally:
        await second.close()


async def test_a_server_that_is_not_there_is_unavailable_within_the_timeout(
    base_settings: Settings,
) -> None:
    mongo = Mongo(
        make_settings(base_settings, mongo_url="mongodb://127.0.0.1:1", mongo_timeout_ms=300)
    )
    try:
        with pytest.raises(UnavailableError):
            async with mongo.guard():
                await mongo.ping()
    finally:
        await mongo.close()
