"""MOCKED: PostgreSQL sessions and health with a fake pool. No server needed.

Live equivalents: tests/integration/test_postgres_live.py.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from psycopg_pool import PoolTimeout

from app.core.config import Settings
from app.core.errors import UnavailableError
from app.store import health
from app.store.postgres import SLOW_MESSAGE, Postgres
from tests.unit.fakes import FakeConnection, FakePool


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, env="test", **overrides)


def postgres(app: FakePool | None = None, readonly: FakePool | None = None) -> Postgres:
    pg = Postgres(settings())
    pg.app = app or FakePool("bi-app")  # type: ignore[assignment]
    pg.readonly = readonly or FakePool("bi-readonly")  # type: ignore[assignment]
    return pg


# -- a successful connection -------------------------------------------------


async def test_ping_succeeds_when_the_database_answers() -> None:
    pool = FakePool()
    await postgres(app=pool).ping()
    assert pool.conn.statements == ["SELECT 1"]
    assert (pool.checked_out, pool.returned) == (1, 1)


async def test_server_version_is_the_number_only() -> None:
    conn = FakeConnection({"SHOW server_version": {"server_version": "16.4 (Ubuntu 16.4-1)"}})
    assert await postgres(app=FakePool(connection=conn)).server_version() == "16.4"


# -- session management ------------------------------------------------------


async def test_a_session_is_one_transaction_that_commits() -> None:
    pool = FakePool()
    async with postgres(app=pool).session() as conn:
        await conn.execute("SELECT 42")
    assert pool.conn.events == ["begin", "commit"]
    assert (pool.checked_out, pool.returned) == (1, 1)  # the connection went back


async def test_a_session_rolls_back_and_returns_the_connection_when_the_code_raises() -> None:
    pool = FakePool()
    with pytest.raises(ValueError, match="our own bug"):
        async with postgres(app=pool).session():
            raise ValueError("our own bug")
    assert pool.conn.events == ["begin", "rollback"]
    assert (pool.checked_out, pool.returned) == (1, 1)


async def test_a_session_sets_the_statement_timeout_before_anything_else() -> None:
    pool = FakePool()
    pg = Postgres(settings(pg_statement_timeout_seconds=7))
    pg.app = pool  # type: ignore[assignment]
    async with pg.session() as conn:
        await conn.execute("SELECT 42")
    assert pool.conn.statements == ["SET LOCAL statement_timeout = 7000", "SELECT 42"]


async def test_the_readonly_session_uses_the_readonly_pool_and_the_query_limit() -> None:
    app, readonly = FakePool("bi-app"), FakePool("bi-readonly")
    pg = Postgres(settings(query_timeout_seconds=4))
    pg.app, pg.readonly = app, readonly  # type: ignore[assignment]
    async with pg.readonly_session() as conn:
        await conn.execute("SELECT 42")
    assert readonly.conn.statements[0] == "SET LOCAL statement_timeout = 4000"
    assert app.checked_out == 0


# -- connection failures and timeouts ----------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        PoolTimeout("couldn't get a connection after 5.00 sec"),
        psycopg.OperationalError("connection refused"),
    ],
)
async def test_no_connection_becomes_unavailable(error: BaseException) -> None:
    with pytest.raises(UnavailableError) as raised:
        async with postgres(app=FakePool(error=error)).session():
            pytest.fail("the block must not run")
    assert raised.value.status == 503
    assert "refused" not in raised.value.message  # the cause is not shown


async def test_a_cancelled_statement_becomes_unavailable_with_a_clear_sentence() -> None:
    conn = FakeConnection()
    conn.fail_on["pg_sleep"] = psycopg.errors.QueryCanceled("canceling statement")
    pool = FakePool(connection=conn)
    with pytest.raises(UnavailableError) as raised:
        async with postgres(app=pool).session() as session:
            await session.execute("SELECT pg_sleep(60)")
    assert raised.value.message == SLOW_MESSAGE
    assert pool.conn.events == ["begin", "rollback"]


async def test_a_query_mistake_is_not_disguised_as_an_outage() -> None:
    conn = FakeConnection()
    conn.fail_on["nope"] = psycopg.errors.UndefinedTable("relation nope does not exist")
    with pytest.raises(psycopg.errors.UndefinedTable):
        async with postgres(app=FakePool(connection=conn)).session() as session:
            await session.execute("SELECT * FROM nope")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (psycopg.OperationalError("refused"), "unreachable"),
        (PoolTimeout("no connection"), "unreachable"),
        (RuntimeError("anything else"), "error"),
    ],
)
async def test_the_health_check_reports_the_class_of_failure(
    error: BaseException, expected: str
) -> None:
    pg = postgres(app=FakePool(error=error))

    async def check(_: object) -> None:
        await pg.ping()

    result = await health.run_check("postgres", check, object())  # type: ignore[arg-type]
    assert (result.ok, result.error) == (False, expected)


# -- pool configuration ------------------------------------------------------


def test_the_pools_take_their_sizes_and_timeouts_from_the_settings() -> None:
    pg = Postgres(settings(pg_pool_min=1, pg_pool_max=7, pg_pool_timeout_seconds=2.5))
    for pool in (pg.app, pg.readonly):
        assert (pool.min_size, pool.max_size, pool.timeout) == (1, 7, 2.5)
    assert pg.app.name == "bi-app" and pg.readonly.name == "bi-readonly"


def test_pool_stats_hold_sizes_only() -> None:
    stats = postgres().pool_stats()
    assert stats == {
        "app": {"min": 0, "max": 10, "size": 2, "available": 1, "waiting": 0},
        "readonly": {"min": 0, "max": 10, "size": 2, "available": 1, "waiting": 0},
    }


def test_the_database_name_comes_from_the_dsn_without_the_rest() -> None:
    pg = Postgres(settings(pg_dsn="postgresql://bi_app:s3cret@db.internal:5432/sales"))
    assert pg.database == "sales"
