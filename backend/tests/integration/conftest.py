"""LIVE tests: they talk to real PostgreSQL and MongoDB servers.

Start the servers first (`make up`, or `scripts/local-postgres.sh up` for PostgreSQL
without Docker), then run `make test-integration`.

A test whose server is not reachable is SKIPPED with a message that says so, unless
BI_TEST_REQUIRE_LIVE=1 is set (as in CI), in which case it FAILS: a missing server
must never look like a pass.

The addresses come from the same BI_* settings as the app (.env in development).
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator, Iterator
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from app.core.config import Settings, load_settings
from app.seed.load import seed
from app.store.connections import Stores
from app.store.migrate import migrate

REQUIRE_LIVE = os.environ.get("BI_TEST_REQUIRE_LIVE") == "1"
TEST_SCHEMA = "bi_live_test"


def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _need(name: str, host: str, port: int) -> None:
    if _listening(host, port):
        return
    message = f"{name} is not running on {host}:{port}; this live test did not run"
    if REQUIRE_LIVE:
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="session")
def base_settings() -> Settings:
    return load_settings(env="test")


@pytest.fixture
def need_postgres(base_settings: Settings) -> None:
    info = conninfo_to_dict(base_settings.pg_dsn)
    _need("PostgreSQL", str(info.get("host", "localhost")), int(info.get("port") or 5432))


@pytest.fixture
def need_mongo(base_settings: Settings) -> None:
    parts = urlsplit(base_settings.mongo_url)
    _need("MongoDB", parts.hostname or "localhost", parts.port or 27017)


@pytest.fixture
def need_qdrant(base_settings: Settings) -> None:
    parts = urlsplit(base_settings.qdrant_url)
    _need("Qdrant", parts.hostname or "localhost", parts.port or 6333)


@pytest.fixture
async def stores(base_settings: Settings) -> AsyncIterator[Stores]:
    opened = await Stores.open(base_settings)
    yield opened
    await opened.close()


def make_settings(base: Settings, **overrides: Any) -> Settings:
    return base.model_copy(update=overrides)


@pytest.fixture
async def scratch_table(need_postgres: None, base_settings: Settings) -> AsyncIterator[str]:
    """A table bi_app may write and bi_readonly may read, made by the owner for one test.

    No table of the product exists yet (the schema is still a proposal), so the tests
    that need to write bring their own and remove it afterwards.
    """
    if not base_settings.pg_owner_dsn:
        pytest.skip("BI_PG_OWNER_DSN is not set; cannot create the scratch table")
    table = f"{TEST_SCHEMA}.items"
    async with await psycopg.AsyncConnection.connect(
        base_settings.pg_owner_dsn, autocommit=True
    ) as owner:
        await owner.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
        await owner.execute(f"CREATE SCHEMA {TEST_SCHEMA}")
        await owner.execute(f"CREATE TABLE {table} (id int PRIMARY KEY, name text NOT NULL)")
        await owner.execute(f"GRANT USAGE ON SCHEMA {TEST_SCHEMA} TO bi_app, bi_readonly")
        await owner.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO bi_app")
        await owner.execute(f"GRANT SELECT ON {table} TO bi_readonly")
        try:
            yield table
        finally:
            await owner.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")


# -- the seeded test database (schema + demo data), for the question pipeline --------

TEST_DATABASE = "bi_test"
AS_OF = date(2026, 11, 1)  # the demo data ends on 2026-10-31; "today" is pinned to this
SEED_DAYS = 62  # September and October 2026


@pytest.fixture(scope="session")
def seeded_settings(base_settings: Settings) -> Settings:
    """Settings pointing at `bi_test`: created, migrated and seeded once per test run."""
    info = conninfo_to_dict(base_settings.pg_dsn)
    host, port = str(info.get("host", "localhost")), int(info.get("port") or 5432)
    if not _listening(host, port):
        message = f"PostgreSQL is not running on {host}:{port}; this live test did not run"
        if REQUIRE_LIVE:
            pytest.fail(message)
        pytest.skip(message)
    if not base_settings.pg_owner_dsn:
        pytest.skip("BI_PG_OWNER_DSN is not set; cannot create the test database")

    with psycopg.connect(base_settings.pg_owner_dsn, autocommit=True) as owner:
        owner.execute(f"DROP DATABASE IF EXISTS {TEST_DATABASE} WITH (FORCE)")
        owner.execute(f"CREATE DATABASE {TEST_DATABASE}")
        owner.execute(f"REVOKE ALL ON DATABASE {TEST_DATABASE} FROM PUBLIC")
        owner.execute(f"GRANT CONNECT ON DATABASE {TEST_DATABASE} TO bi_app, bi_readonly")

    def on_test_db(dsn: str) -> str:
        return make_conninfo(dsn, dbname=TEST_DATABASE)

    settings = base_settings.model_copy(
        update={
            "pg_dsn": on_test_db(base_settings.pg_dsn),
            "pg_readonly_dsn": on_test_db(base_settings.pg_readonly_dsn),
            "pg_owner_dsn": on_test_db(base_settings.pg_owner_dsn),
            "fake_today": AS_OF,
            "openai_api_key": "",  # live tests never call a real model
        }
    )
    assert migrate(settings.pg_owner_dsn) == ["0001_init"]
    seed(settings.pg_owner_dsn, AS_OF, SEED_DAYS)
    return settings


@pytest.fixture
async def seeded(seeded_settings: Settings) -> AsyncIterator[Stores]:
    opened = await Stores.open(seeded_settings)
    yield opened
    await opened.close()


@pytest.fixture
def owner(seeded_settings: Settings) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """bi_owner on the test database: sees every company, for computing expected values."""
    with psycopg.connect(seeded_settings.pg_owner_dsn, row_factory=dict_row) as conn:
        yield conn
