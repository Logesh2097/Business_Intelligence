"""LIVE: PostgreSQL connectivity, pooling and sessions against a real server.

Mocked equivalents: tests/unit/test_postgres_mocked.py.
"""

from __future__ import annotations

import asyncio

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from app.core.config import Settings
from app.core.errors import UnavailableError
from app.store import health
from app.store.connections import Stores
from app.store.postgres import SLOW_MESSAGE, Postgres
from tests.integration.conftest import make_settings

pytestmark = pytest.mark.usefixtures("need_postgres")


# -- a successful connection -------------------------------------------------


async def test_the_database_answers_and_is_version_16(stores: Stores) -> None:
    await stores.postgres.ping()
    assert (await stores.postgres.server_version()).split(".")[0] == "16"


async def test_the_two_pools_connect_as_two_different_users(stores: Stores) -> None:
    async with stores.postgres.session() as conn:
        app_user = (await (await conn.execute("SELECT current_user AS u")).fetchone())["u"]
    async with stores.postgres.readonly_session() as conn:
        ro_user = (await (await conn.execute("SELECT current_user AS u")).fetchone())["u"]
    assert (app_user, ro_user) == ("bi_app", "bi_readonly")


async def test_the_readiness_check_passes(stores: Stores) -> None:
    result = await health.run_check("postgres", health.check_postgres, stores)
    assert result.ok is True and result.error is None


# -- session management ------------------------------------------------------


async def test_a_session_commits_what_it_wrote(stores: Stores, scratch_table: str) -> None:
    async with stores.postgres.session() as conn:
        await conn.execute(f"INSERT INTO {scratch_table} VALUES (1, 'kept')")
    async with stores.postgres.session() as conn:
        row = await (await conn.execute(f"SELECT name FROM {scratch_table}")).fetchone()
    assert row == {"name": "kept"}  # rows come back as dictionaries


async def test_a_session_rolls_back_when_the_code_raises(
    stores: Stores, scratch_table: str
) -> None:
    with pytest.raises(RuntimeError, match="changed my mind"):
        async with stores.postgres.session() as conn:
            await conn.execute(f"INSERT INTO {scratch_table} VALUES (1, 'lost')")
            raise RuntimeError("changed my mind")
    async with stores.postgres.session() as conn:
        count = await (await conn.execute(f"SELECT count(*) AS n FROM {scratch_table}")).fetchone()
    assert count == {"n": 0}


async def test_a_failed_statement_rolls_back_and_the_pool_still_works(
    stores: Stores, scratch_table: str
) -> None:
    with pytest.raises(psycopg.errors.UniqueViolation):
        async with stores.postgres.session() as conn:
            await conn.execute(f"INSERT INTO {scratch_table} VALUES (1, 'a')")
            await conn.execute(f"INSERT INTO {scratch_table} VALUES (1, 'again')")
    async with stores.postgres.session() as conn:  # the connection came back clean
        count = await (await conn.execute(f"SELECT count(*) AS n FROM {scratch_table}")).fetchone()
    assert count == {"n": 0}


async def test_the_readonly_session_can_read_but_never_write(
    stores: Stores, scratch_table: str
) -> None:
    async with stores.postgres.session() as conn:
        await conn.execute(f"INSERT INTO {scratch_table} VALUES (1, 'seen')")
    async with stores.postgres.readonly_session() as conn:
        row = await (await conn.execute(f"SELECT name FROM {scratch_table}")).fetchone()
        mode = await (await conn.execute("SHOW transaction_read_only")).fetchone()
    assert row == {"name": "seen"} and mode == {"transaction_read_only": "on"}

    with pytest.raises(psycopg.Error) as raised:
        async with stores.postgres.readonly_session() as conn:
            await conn.execute(f"DELETE FROM {scratch_table}")
    assert not isinstance(raised.value, psycopg.OperationalError)  # refused, not an outage


async def test_sessions_get_their_statement_timeouts_from_the_settings(
    base_settings: Settings,
) -> None:
    pg = Postgres(
        make_settings(base_settings, pg_statement_timeout_seconds=7, query_timeout_seconds=3)
    )
    await pg.open()
    try:
        async with pg.session() as conn:
            app = await (await conn.execute("SHOW statement_timeout")).fetchone()
        async with pg.readonly_session() as conn:
            readonly = await (await conn.execute("SHOW statement_timeout")).fetchone()
    finally:
        await pg.close()
    assert (app["statement_timeout"], readonly["statement_timeout"]) == ("7s", "3s")


# -- pooling -----------------------------------------------------------------


async def test_sequential_sessions_reuse_one_connection(stores: Stores) -> None:
    pids = set()
    for _ in range(5):
        async with stores.postgres.session() as conn:
            pids.add((await (await conn.execute("SELECT pg_backend_pid() AS p")).fetchone())["p"])
    assert len(pids) == 1
    assert stores.postgres.pool_stats()["app"]["size"] == 1


async def test_concurrent_sessions_never_exceed_the_pool_maximum(
    base_settings: Settings,
) -> None:
    pg = Postgres(make_settings(base_settings, pg_pool_max=3))
    await pg.open()

    async def work() -> int:
        async with pg.session() as conn:
            await conn.execute("SELECT pg_sleep(0.1)")
            return int((await (await conn.execute("SELECT pg_backend_pid() AS p")).fetchone())["p"])

    try:
        pids = await asyncio.gather(*(work() for _ in range(12)))
        assert len(pids) == 12 and len(set(pids)) <= 3
        assert pg.pool_stats()["app"]["size"] <= 3
    finally:
        await pg.close()


# -- failures and timeouts ---------------------------------------------------


async def test_a_statement_over_its_limit_is_cancelled_and_reported_as_slow(
    base_settings: Settings,
) -> None:
    pg = Postgres(make_settings(base_settings, pg_statement_timeout_seconds=1))
    await pg.open()
    try:
        with pytest.raises(UnavailableError) as raised:
            async with pg.session() as conn:
                await conn.execute("SELECT pg_sleep(10)")
        assert raised.value.message == SLOW_MESSAGE
        await pg.ping()  # the pool is still healthy afterwards
    finally:
        await pg.close()


async def test_an_exhausted_pool_gives_unavailable_instead_of_waiting_forever(
    base_settings: Settings,
) -> None:
    pg = Postgres(make_settings(base_settings, pg_pool_max=1, pg_pool_timeout_seconds=0.3))
    await pg.open()
    try:
        async with pg.session():  # holds the only connection
            with pytest.raises(UnavailableError):
                async with pg.session():
                    pytest.fail("there is no second connection to give")
    finally:
        await pg.close()


async def test_a_wrong_password_is_unavailable_and_the_cause_stays_private(
    base_settings: Settings,
) -> None:
    wrong = make_conninfo(base_settings.pg_dsn, password="not-the-password")  # noqa: S106
    pg = Postgres(make_settings(base_settings, pg_dsn=wrong, pg_pool_timeout_seconds=1))
    await pg.open()
    try:
        with pytest.raises(UnavailableError) as raised:
            async with pg.session():
                pytest.fail("the block must not run")
        assert "password" not in raised.value.message
    finally:
        await pg.close()


async def test_a_closed_pool_is_unavailable(base_settings: Settings) -> None:
    pg = Postgres(base_settings)
    await pg.open()
    await pg.ping()
    await pg.close()
    with pytest.raises(UnavailableError):
        async with pg.session():
            pytest.fail("the block must not run")
