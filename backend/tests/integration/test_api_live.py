"""LIVE: the health, readiness and status routes with real servers behind them."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(base_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(base_settings)) as started:
        yield started


@pytest.mark.usefixtures("need_postgres")
def test_readyz_reports_postgres_as_up(client: TestClient) -> None:
    check = client.get("/readyz").json()["checks"]["postgres"]
    assert check["ok"] is True and "error" not in check


@pytest.mark.usefixtures("need_mongo")
def test_readyz_reports_mongo_as_up(client: TestClient) -> None:
    check = client.get("/readyz").json()["checks"]["mongo"]
    assert check["ok"] is True and "error" not in check


@pytest.mark.usefixtures("need_postgres")
def test_the_status_route_describes_postgres_without_its_credentials(
    client: TestClient, base_settings: Settings
) -> None:
    response = client.get("/api/v1/system/databases")
    assert response.status_code == 200
    postgres = response.json()["databases"]["postgres"]
    assert postgres["ok"] is True
    assert postgres["version"].startswith("16.")
    assert postgres["pools"]["app"]["ok"] is True
    assert postgres["pools"]["readonly"]["ok"] is True
    assert postgres["pools"]["app"]["size"] >= 1

    for dsn in (base_settings.pg_dsn, base_settings.pg_readonly_dsn):
        info = conninfo_to_dict(dsn)
        assert str(info["password"]) not in response.text
        assert str(info["user"]) not in response.text


@pytest.mark.usefixtures("need_postgres")
def test_startup_and_shutdown_open_and_close_real_connections(base_settings: Settings) -> None:
    app = create_app(base_settings)
    with TestClient(app) as started:
        assert started.get("/readyz").json()["checks"]["postgres"]["ok"] is True
        pool = app.state.stores.postgres.app
        assert pool.closed is False
    assert pool.closed is True
