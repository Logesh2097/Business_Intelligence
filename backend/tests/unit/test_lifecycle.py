"""MOCKED: start-up and shutdown open and close the store connections."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.store.connections import Stores
from app.store.mongo import Mongo
from app.store.postgres import Postgres
from app.store.qdrant import Qdrant
from tests.unit.fakes import FakeMongoClient, FakePool


def settings() -> Settings:
    return Settings(_env_file=None, env="test")


def fake_stores() -> tuple[Stores, FakePool, FakePool, FakeMongoClient]:
    app_pool, ro_pool, client = FakePool("bi-app"), FakePool("bi-readonly"), FakeMongoClient()
    pg, mongo = Postgres(settings()), Mongo(settings())
    pg.app, pg.readonly = app_pool, ro_pool  # type: ignore[assignment]
    mongo.client = client  # type: ignore[assignment]
    stores = Stores(settings=settings(), postgres=pg, mongo=mongo, qdrant=Qdrant(settings()))
    return stores, app_pool, ro_pool, client


async def test_opening_does_not_wait_for_the_database() -> None:
    stores, app_pool, ro_pool, _ = fake_stores()
    await stores.postgres.open()
    assert app_pool.events == ["open(wait=False)"]
    assert ro_pool.events == ["open(wait=False)"]


async def test_closing_closes_both_pools_and_the_mongo_client() -> None:
    stores, app_pool, ro_pool, client = fake_stores()
    await stores.close()
    assert app_pool.events == ["close"] and ro_pool.events == ["close"]
    assert client.closed is True


async def test_one_store_failing_to_close_does_not_leave_the_others_open() -> None:
    stores, app_pool, ro_pool, client = fake_stores()

    async def broken() -> None:
        raise RuntimeError("could not close")

    app_pool.close = broken  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await stores.close()
    assert ro_pool.events == ["close"]
    assert client.closed is True


def test_the_app_opens_its_stores_at_startup_and_closes_them_at_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    stores, *_ = fake_stores()

    async def fake_open(cls: Any, _: Settings) -> Stores:
        events.append("open")
        return stores

    async def fake_close() -> None:
        events.append("close")

    monkeypatch.setattr(Stores, "open", classmethod(fake_open))
    monkeypatch.setattr(stores, "close", fake_close)

    app = create_app(settings())
    assert events == []  # nothing is opened before the app starts
    with TestClient(app) as client:
        assert events == ["open"]
        assert app.state.stores is stores
        assert client.get("/healthz").status_code == 200
    assert events == ["open", "close"]


def test_the_stores_are_closed_even_when_a_request_crashed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    stores, *_ = fake_stores()

    async def fake_open(cls: Any, _: Settings) -> Stores:
        return stores

    async def fake_close() -> None:
        events.append("close")

    monkeypatch.setattr(Stores, "open", classmethod(fake_open))
    monkeypatch.setattr(stores, "close", fake_close)

    app = create_app(settings())

    @app.get("/api/v1/_test/crash")
    async def crash() -> None:
        raise RuntimeError("boom")

    with TestClient(app) as client:
        assert client.get("/api/v1/_test/crash").status_code == 500
    assert events == ["close"]
