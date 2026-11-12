"""/healthz always answers; /readyz tells the truth about each store (api.md §2)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.core.config import Settings, UnsafeSettingsError
from app.main import create_app
from app.store import health
from app.store.connections import Stores

# ports where nothing listens, so "down" is real and fast
DOWN = {
    "pg_dsn": "postgresql://bi_app:x@127.0.0.1:1/bi",
    "pg_readonly_dsn": "postgresql://bi_readonly:x@127.0.0.1:1/bi",
    "mongo_url": "mongodb://127.0.0.1:1",
    "qdrant_url": "http://127.0.0.1:1",
}


@pytest.fixture
def client() -> Iterator[Callable[..., TestClient]]:
    """Build a started app (its connections opened, and closed after the test)."""
    with ExitStack() as stack:

        def make(**overrides: object) -> TestClient:
            settings = Settings(_env_file=None, env="test", **{**DOWN, **overrides})
            return stack.enter_context(TestClient(create_app(settings)))

        yield make


def test_healthz_answers_without_touching_a_store(client: Callable[..., TestClient]) -> None:
    response = client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0", "env": "test"}
    assert response.headers["X-Request-Id"]


def test_readyz_is_503_and_names_every_store_that_is_down(
    client: Callable[..., TestClient],
) -> None:
    response = client().get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert set(body["checks"]) == {"postgres", "mongo", "qdrant"}
    for check in body["checks"].values():
        assert check["ok"] is False
        assert check["error"] in {"unreachable", "timeout"}


def test_readyz_never_leaks_a_connection_string(client: Callable[..., TestClient]) -> None:
    text = client(pg_dsn="postgresql://bi_app:hunter2@127.0.0.1:1/bi").get("/readyz").text
    assert "hunter2" not in text
    assert "127.0.0.1" not in text


def test_readyz_is_200_when_every_store_answers(
    client: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def up(_: Stores) -> None:
        return None

    monkeypatch.setattr(health, "CHECKS", {"postgres": up, "mongo": up, "qdrant": up})
    response = client().get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert all(check["ok"] for check in response.json()["checks"].values())


def test_one_store_down_is_reported_alone(
    client: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def up(_: Stores) -> None:
        return None

    async def down(_: Stores) -> None:
        raise ConnectionRefusedError

    monkeypatch.setattr(health, "CHECKS", {"postgres": up, "mongo": down, "qdrant": up})
    body = client().get("/readyz").json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"]["ok"] is True
    assert body["checks"]["mongo"] == {
        "ok": False,
        "ms": body["checks"]["mongo"]["ms"],
        "error": "unreachable",
    }


def test_a_store_that_hangs_is_a_timeout(
    client: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hangs(_: Stores) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(health, "CHECKS", {"postgres": hangs})
    body = client().get("/readyz").json()
    assert body["checks"]["postgres"]["error"] == "timeout"


def test_the_app_refuses_to_start_with_unsafe_production_settings() -> None:
    with pytest.raises(UnsafeSettingsError):
        create_app(Settings(_env_file=None, env="prod"))


def test_the_openapi_document_is_off_outside_development(
    client: Callable[..., TestClient],
) -> None:
    assert client().get("/api/v1/openapi.json").status_code == 200
    prod = Settings(
        _env_file=None,
        env="prod",
        secret_key="s" * 40,
        openai_api_key="sk-test",
        llm_model="test-model",
        **DOWN,
    )
    with TestClient(create_app(prod)) as prod_client:
        assert prod_client.get("/api/v1/openapi.json").status_code == 404


def test_the_built_web_app_is_served_and_cannot_escape_its_folder(
    client: Callable[..., TestClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<h1>app</h1>")
    (static / "assets" / "a.js").write_text("js")
    (tmp_path / "secret.txt").write_text("secret")
    monkeypatch.setattr(main, "STATIC_DIR", static)

    web = client()
    assert web.get("/").text == "<h1>app</h1>"
    assert web.get("/chat/123").text == "<h1>app</h1>"  # a page of the single-page app
    assert web.get("/assets/a.js").text == "js"
    assert web.get("/healthz").json()["status"] == "ok"  # routes still win
    assert web.get("/api/v1/nope").status_code == 404  # not a page
    assert "secret" not in web.get("/%2e%2e/secret.txt").text
    assert "secret" not in web.get("/..%2fsecret.txt").text
