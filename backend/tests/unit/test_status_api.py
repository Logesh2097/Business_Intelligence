"""MOCKED: GET /api/v1/system/databases (docs/api.md §2)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from app.core.config import Settings
from app.main import create_app
from app.store.connections import Stores
from tests.unit.fakes import FakeConnection, FakeMongoClient, FakePool

URL = "/api/v1/system/databases"
SECRETS = {
    "pg_dsn": "postgresql://bi_app:pw-app-secret@db.internal:5432/bi",
    "pg_readonly_dsn": "postgresql://bi_readonly:pw-ro-secret@db.internal:5432/bi",
    "mongo_url": "mongodb://mongo_user:pw-mongo-secret@mongo.internal:27017",
    "qdrant_url": "http://qdrant.internal:6333",
    "qdrant_api_key": "qdrant-key-secret",
}


def version_pool(name: str) -> FakePool:
    conn = FakeConnection({"SHOW server_version": {"server_version": "16.4 (Debian)"}})
    return FakePool(name, connection=conn)


@pytest.fixture
def started() -> Iterator[tuple[TestClient, Stores]]:
    with TestClient(create_app(Settings(_env_file=None, env="test", **SECRETS))) as client:
        stores: Stores = client.app.state.stores  # type: ignore[attr-defined]

        async def qdrant_version() -> str:
            return "1.20.0"

        stores.postgres.app = version_pool("bi-app")  # type: ignore[assignment]
        stores.postgres.readonly = version_pool("bi-readonly")  # type: ignore[assignment]
        stores.mongo.client = FakeMongoClient(version="7.0.14")  # type: ignore[assignment]
        stores.qdrant.server_version = qdrant_version  # type: ignore[method-assign]
        yield client, stores


def test_everything_up(started: tuple[TestClient, Stores]) -> None:
    client, _ = started
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    pg = body["databases"]["postgres"]
    assert (pg["ok"], pg["version"], pg["database"]) == (True, "16.4", "bi")
    assert pg["pools"]["app"] == {
        "ok": True, "min": 0, "max": 10, "size": 2, "available": 1, "waiting": 0,
    }  # fmt: skip
    assert pg["pools"]["readonly"]["ok"] is True
    mongo = body["databases"]["mongo"]
    assert (mongo["ok"], mongo["version"], mongo["database"], mongo["pool_max"]) == (
        True, "7.0.14", "bi", 20,
    )  # fmt: skip
    assert body["databases"]["qdrant"] == {
        "ok": True,
        "ms": body["databases"]["qdrant"]["ms"],
        "version": "1.20.0",
    }


def test_it_is_a_report_so_it_answers_200_even_when_stores_are_down(
    started: tuple[TestClient, Stores],
) -> None:
    client, stores = started
    stores.postgres.app = FakePool(error=psycopg.OperationalError("refused"))  # type: ignore[assignment]
    stores.mongo.client = FakeMongoClient(  # type: ignore[assignment]
        error=ServerSelectionTimeoutError("mongo.internal:27017 refused")
    )
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["databases"]["postgres"]["ok"] is False
    assert body["databases"]["postgres"]["error"] == "unreachable"
    assert "version" not in body["databases"]["postgres"]
    assert body["databases"]["mongo"] == {
        "ok": False,
        "ms": body["databases"]["mongo"]["ms"],
        "error": "unreachable",
        "database": "bi",
        "pool_max": 20,
    }
    assert body["databases"]["qdrant"]["ok"] is True


def test_the_readonly_user_being_down_is_degraded(started: tuple[TestClient, Stores]) -> None:
    client, stores = started
    stores.postgres.readonly = FakePool(  # type: ignore[assignment]
        error=psycopg.OperationalError("password authentication failed")
    )
    body = client.get(URL).json()
    assert body["status"] == "degraded"
    assert body["databases"]["postgres"]["ok"] is True
    assert body["databases"]["postgres"]["pools"]["readonly"]["ok"] is False


@pytest.mark.parametrize("down", [False, True])
def test_it_never_shows_a_credential_a_host_or_a_user(
    started: tuple[TestClient, Stores], down: bool
) -> None:
    client, stores = started
    if down:
        stores.postgres.app = FakePool(  # type: ignore[assignment]
            error=psycopg.OperationalError(
                "connection to db.internal failed for user bi_app: pw-app-secret"
            )
        )
    text = client.get(URL).text
    for secret in (
        "pw-app-secret", "pw-ro-secret", "pw-mongo-secret", "qdrant-key-secret",
        "db.internal", "mongo.internal", "qdrant.internal",
        "bi_app", "bi_readonly", "mongo_user", "5432", "27017",
    ):  # fmt: skip
        assert secret not in text, secret


def test_a_store_that_hangs_is_a_timeout_not_a_hung_request(
    started: tuple[TestClient, Stores], monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from app.store import health

    client, stores = started

    async def hangs() -> str:
        await asyncio.sleep(60)
        return ""

    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.05)
    stores.qdrant.server_version = hangs  # type: ignore[method-assign]
    body = client.get(URL).json()
    assert body["databases"]["qdrant"]["error"] == "timeout"


def test_it_is_hidden_outside_development_until_sign_in_exists() -> None:
    prod: dict[str, Any] = {
        "env": "prod",
        "secret_key": "s" * 40,
        "openai_api_key": "sk-test",
        "llm_model": "test-model",
        "pg_dsn": "postgresql://bi_app:real-app@db/bi",
        "pg_readonly_dsn": "postgresql://bi_readonly:real-ro@db/bi",
    }
    with TestClient(create_app(Settings(_env_file=None, **prod))) as client:
        response = client.get(URL)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        # the health routes stay open for monitors
        assert client.get("/healthz").status_code == 200
