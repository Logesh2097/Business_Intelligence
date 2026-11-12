"""Fixtures for the golden set. Everything here works on bi_eval and nothing else.

`make eval-check` needs PostgreSQL running (scripts/local-postgres.sh up) and no model
key. A test whose server is not reachable is SKIPPED with a message, unless
BI_TEST_REQUIRE_LIVE=1 is set, in which case it FAILS.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator, Iterator
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from app.core.config import Settings, load_settings
from app.seed.generate import Data
from app.store.connections import Stores
from tests.evals import database

REQUIRE_LIVE = os.environ.get("BI_TEST_REQUIRE_LIVE") == "1"


@pytest.fixture(scope="session")
def base_settings() -> Settings:
    """The application's normal settings, exactly as it would load them."""
    return load_settings()


def _need_postgres(settings: Settings) -> None:
    info = conninfo_to_dict(settings.pg_dsn)
    host, port = str(info.get("host", "localhost")), int(info.get("port") or 5432)
    try:
        socket.create_connection((host, port), timeout=1).close()
    except OSError:
        message = f"PostgreSQL is not running on {host}:{port}; the golden set did not run"
        if REQUIRE_LIVE:
            pytest.fail(message)
        pytest.skip(message)


@pytest.fixture(scope="session")
def demo_before(base_settings: Settings) -> dict[str, Any]:
    """The demo database's counts and totals before anything else happens. Read-only."""
    _need_postgres(base_settings)
    with database.read_only(base_settings.pg_owner_dsn) as demo:
        return database.snapshot(demo)


@pytest.fixture(scope="session")
def eval_database(base_settings: Settings, demo_before: dict[str, Any]) -> tuple[Settings, Data]:
    """bi_eval, rebuilt from the fixed inputs once per run, with the rows it was built from."""
    return database.recreate(base_settings)


@pytest.fixture(scope="session")
def eval_settings(eval_database: tuple[Settings, Data]) -> Settings:
    return eval_database[0]


@pytest.fixture
async def stores(eval_settings: Settings) -> AsyncIterator[Stores]:
    opened = await Stores.open(eval_settings)
    yield opened
    await opened.close()


@pytest.fixture
def owner(eval_settings: Settings) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """The database owner on bi_eval, on a connection that cannot write."""
    with database.read_only(eval_settings.pg_owner_dsn) as conn:
        yield conn
