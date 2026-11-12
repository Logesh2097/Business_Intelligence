"""MOCKED: MongoDB client, guard and health with a fake client. No server needed.

Live equivalents: tests/integration/test_mongo_live.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from pymongo.errors import (
    DuplicateKeyError,
    ExecutionTimeout,
    NetworkTimeout,
    ServerSelectionTimeoutError,
)

from app.core.config import Settings
from app.core.errors import UnavailableError
from app.store import health
from app.store.mongo import SLOW_MESSAGE, Mongo
from tests.unit.fakes import FakeMongoClient


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, env="test", **overrides)


def mongo(client: FakeMongoClient) -> Mongo:
    store = Mongo(settings())
    store.client = client  # type: ignore[assignment]
    return store


async def test_ping_succeeds_when_the_server_answers() -> None:
    client = FakeMongoClient()
    await mongo(client).ping()
    assert client.admin.commands == ["ping"]


async def test_server_version_is_read_from_the_server() -> None:
    assert await mongo(FakeMongoClient(version="7.0.14")).server_version() == "7.0.14"


async def test_ping_raises_when_no_server_answers() -> None:
    client = FakeMongoClient(error=ServerSelectionTimeoutError("localhost:27027: refused"))
    with pytest.raises(ServerSelectionTimeoutError):
        await mongo(client).ping()


async def test_the_health_check_reports_an_unreachable_server() -> None:
    store = mongo(FakeMongoClient(error=ServerSelectionTimeoutError("refused")))

    async def check(_: object) -> None:
        await store.ping()

    result = await health.run_check("mongo", check, object())  # type: ignore[arg-type]
    assert (result.ok, result.error) == (False, "unreachable")


async def test_guard_turns_a_connection_failure_into_unavailable() -> None:
    store = mongo(FakeMongoClient())
    with pytest.raises(UnavailableError) as raised:
        async with store.guard():
            raise ServerSelectionTimeoutError("mongo.internal:27017: timed out")
    assert raised.value.status == 503
    assert "mongo.internal" not in raised.value.message  # the cause is not shown


@pytest.mark.parametrize("error", [ExecutionTimeout("too slow"), NetworkTimeout("too slow")])
async def test_guard_turns_a_timeout_into_a_clear_sentence(error: BaseException) -> None:
    with pytest.raises(UnavailableError) as raised:
        async with mongo(FakeMongoClient()).guard():
            raise error
    assert raised.value.message == SLOW_MESSAGE


async def test_guard_does_not_disguise_other_errors_as_an_outage() -> None:
    with pytest.raises(DuplicateKeyError):
        async with mongo(FakeMongoClient()).guard():
            raise DuplicateKeyError("E11000 duplicate key")


async def test_close_closes_the_client() -> None:
    client = FakeMongoClient()
    await mongo(client).close()
    assert client.closed is True


def test_the_client_is_built_from_the_settings_and_does_not_connect() -> None:
    # Building it must not touch the network: this address answers nothing.
    store = Mongo(settings(mongo_url="mongodb://127.0.0.1:1", mongo_db="reports", mongo_pool_max=5))
    assert store.database == "reports"
    assert store.pool_max == 5
    assert store.collection("messages").full_name == "reports.messages"
    options = store.client.options
    assert options.pool_options.max_pool_size == 5
    assert options.server_selection_timeout == 2.0
