"""Every error has one shape, carries the request id, and never shows its cause (api.md §1)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core import errors
from app.core.config import Settings
from app.main import create_app


class Body(BaseModel):
    email: str
    age: int


def build() -> FastAPI:
    app = create_app(Settings(_env_file=None, env="test"))

    @app.get("/api/v1/_test/app-error/{name}")
    async def app_error(name: str) -> None:
        raise {
            "not_found": errors.NotFoundError(),
            "denied": errors.PermissionDeniedError(),
            "conflict": errors.ConflictError("That email is already in use."),
            "limited": errors.RateLimitedError(code="daily_limit_reached"),
        }[name]

    @app.get("/api/v1/_test/crash")
    async def crash() -> None:
        raise RuntimeError("password=hunter2 host=db.internal")

    @app.post("/api/v1/_test/body")
    async def body(_: Body) -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(build()) as started:
        yield started


def error_of(response: object) -> dict[str, object]:
    body = response.json()  # type: ignore[attr-defined]
    assert set(body) == {"error"}
    return body["error"]  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    ("name", "status", "code", "message"),
    [
        ("not_found", 404, "not_found", "Not found."),
        ("denied", 403, "permission_denied", "You do not have access to this."),
        ("conflict", 409, "conflict", "That email is already in use."),
        ("limited", 429, "daily_limit_reached", "Too many requests. Please wait and try again."),
    ],
)
def test_an_app_error_becomes_the_error_body(
    client: TestClient, name: str, status: int, code: str, message: str
) -> None:
    response = client.get(f"/api/v1/_test/app-error/{name}")
    assert response.status_code == status
    error = error_of(response)
    assert (error["code"], error["message"]) == (code, message)


def test_the_request_id_in_the_body_is_the_one_in_the_header(client: TestClient) -> None:
    response = client.get("/api/v1/_test/app-error/not_found")
    request_id = response.headers["X-Request-Id"]
    assert len(request_id) == 32
    assert error_of(response)["request_id"] == request_id


def test_an_unknown_path_and_a_wrong_method_use_the_same_shape(client: TestClient) -> None:
    missing = client.get("/api/v1/nope")
    assert missing.status_code == 404
    assert error_of(missing)["code"] == "not_found"

    wrong = client.post("/healthz")
    assert wrong.status_code == 405
    assert error_of(wrong)["message"] == "This method is not allowed here."


def test_an_unexpected_error_is_generic_and_hides_its_cause(client: TestClient) -> None:
    response = client.get("/api/v1/_test/crash")
    assert response.status_code == 500
    error = error_of(response)
    assert error["code"] == "internal"
    assert error["message"] == errors.INTERNAL_MESSAGE
    assert error["request_id"] == response.headers["X-Request-Id"]
    assert "hunter2" not in response.text and "db.internal" not in response.text


def test_the_cause_of_an_unexpected_error_is_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("ERROR", logger="bi.request"):
        client.get("/api/v1/_test/crash")
    assert any(record.message == "unhandled error" and record.exc_info for record in caplog.records)


def test_a_bad_body_lists_the_fields_without_echoing_the_values(client: TestClient) -> None:
    response = client.post("/api/v1/_test/body", json={"age": "secret-value"})
    assert response.status_code == 422
    error = error_of(response)
    assert error["code"] == "validation_failed"
    fields = {item["field"] for item in error["fields"]}  # type: ignore[union-attr]
    assert fields == {"email", "age"}
    assert "secret-value" not in response.text


def test_a_good_request_is_untouched(client: TestClient) -> None:
    assert client.post("/api/v1/_test/body", json={"email": "a@b.c", "age": 3}).json() == {
        "ok": True
    }
