"""REAL DRIVERS, NO SERVER: the true behaviour when nothing is listening.

Not mocked: these use psycopg and PyMongo against an address where no server runs,
so they prove the failure path end to end and need nothing installed.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import UnavailableError
from app.store.connections import Stores
from app.store.status import database_status

NOWHERE: dict[str, Any] = {
    "pg_dsn": "postgresql://bi_app:pw-app@127.0.0.1:1/bi",
    "pg_readonly_dsn": "postgresql://bi_readonly:pw-ro@127.0.0.1:1/bi",
    "mongo_url": "mongodb://127.0.0.1:1",
    "qdrant_url": "http://127.0.0.1:1",
    "pg_pool_timeout_seconds": 0.5,
    "pg_connect_timeout_seconds": 1,
    "mongo_timeout_ms": 300,
}


@pytest.fixture
async def stores() -> Any:
    opened = await Stores.open(Settings(_env_file=None, env="test", **NOWHERE))
    yield opened
    await opened.close()


async def test_the_app_opens_its_stores_even_when_every_server_is_down(stores: Stores) -> None:
    assert stores.postgres.app.closed is False  # opened without waiting for the database


async def test_a_postgres_session_fails_fast_with_unavailable(stores: Stores) -> None:
    started = time.perf_counter()
    with pytest.raises(UnavailableError):
        async with stores.postgres.session():
            pytest.fail("the block must not run")
    assert time.perf_counter() - started < 3  # bounded by BI_PG_POOL_TIMEOUT_SECONDS


async def test_a_mongo_operation_fails_fast_with_unavailable(stores: Stores) -> None:
    started = time.perf_counter()
    with pytest.raises(UnavailableError):
        async with stores.mongo.guard():
            await stores.mongo.collection("anything").find_one({})
    assert time.perf_counter() - started < 3  # bounded by BI_MONGO_TIMEOUT_MS


async def test_the_status_report_marks_every_store_down_and_shows_no_secret(
    stores: Stores,
) -> None:
    report = await database_status(stores)
    assert report["status"] == "degraded"
    for name in ("postgres", "mongo", "qdrant"):
        assert report["databases"][name]["ok"] is False
        assert report["databases"][name]["error"] in {"unreachable", "timeout"}
    assert report["databases"]["postgres"]["pools"]["readonly"]["ok"] is False
    text = str(report)
    for secret in ("pw-app", "pw-ro", "bi_app", "bi_readonly", "127.0.0.1"):
        assert secret not in text


async def test_a_session_after_shutdown_is_unavailable_not_a_crash() -> None:
    stores = await Stores.open(Settings(_env_file=None, env="test", **NOWHERE))
    await stores.close()
    with pytest.raises(UnavailableError):
        async with stores.postgres.session():
            pytest.fail("the block must not run")
