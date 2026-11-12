"""MOCKED: /api/v1/me and the development sign-in list."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_caller
from app.core.config import Settings
from app.main import create_app
from app.store import users
from tests.unit.analytics_fakes import caller

DEV = Settings(_env_file=None, env="test")
PROD = Settings(
    _env_file=None, env="prod", secret_key="s" * 40, openai_api_key="sk-test", llm_model="m",
    pg_dsn="postgresql://bi_app:x@db/bi", pg_readonly_dsn="postgresql://bi_readonly:x@db/bi",
)  # fmt: skip


def test_me_says_who_is_asking_and_what_they_may_do() -> None:
    with TestClient(create_app(DEV)) as client:
        client.app.dependency_overrides[get_caller] = lambda: caller("viewer", limited=True)  # type: ignore[attr-defined]
        body = client.get("/api/v1/me").json()
        assert body == {
            "role": "viewer",
            "company": "Brew & Bite Cafés",
            "currency": "INR",
            "timezone": "Asia/Kolkata",
            "all_outlets": False,
            "outlets": ["Indiranagar"],
            "permissions": ["chat:ask", "documents:read"],
            "scope_note": "Limited to your outlets: Indiranagar.",
            "sign_in": "development header",
        }
        assert "tenant_id" not in body and "user_id" not in body  # no ids leave the server
        client.app.dependency_overrides[get_caller] = lambda: caller("admin")  # type: ignore[attr-defined]
        assert client.get("/api/v1/me").json()["permissions"] == [
            "chat:ask", "sql:view", "documents:read", "documents:manage",
        ]  # fmt: skip


def test_me_needs_a_caller() -> None:
    with TestClient(create_app(DEV)) as client:
        assert client.get("/api/v1/me").status_code == 401


def test_the_demo_user_list_exists_in_development_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def listed(pg: Any, limit: int = 50) -> list[dict[str, str]]:
        return [{"email": "analyst@brewbite.example", "role": "analyst", "company": "Brew & Bite"}]

    monkeypatch.setattr(users, "list_demo_users", listed)
    with TestClient(create_app(DEV)) as client:
        assert client.get("/api/v1/dev/users").json() == [
            {"email": "analyst@brewbite.example", "role": "analyst", "company": "Brew & Bite"}
        ]
    with TestClient(create_app(PROD)) as client:
        assert client.get("/api/v1/dev/users").status_code == 404
        # and the header that the list is for is refused there too
        refused = client.get("/api/v1/me", headers={"X-Dev-User": "analyst@brewbite.example"})
        assert refused.status_code == 401
