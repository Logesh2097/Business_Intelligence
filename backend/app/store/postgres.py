"""PostgreSQL connections, sessions and health.

Two pools for two database users with two jobs (docs/design/data-model.md §2):

- `app`       bi_app: the product's own reads and writes. Use `session()`.
- `readonly`  bi_readonly: used only to run AI-written SQL, in a later phase.
              Use `tenant_session(caller)`: READ ONLY, and limited by row security
              to the caller's company and outlets.

The pools open without waiting for the database, so the API starts even when
PostgreSQL is down and /readyz reports it. Connections are made on first use.

How a store function uses it:

    async def count_users(pg: Postgres) -> int:
        async with pg.session() as conn:
            row = await (await conn.execute("SELECT count(*) AS n FROM app.users")).fetchone()
            return row["n"]

A session is one transaction: committed when the block ends, rolled back when it
raises. A database that is down, slow or out of connections becomes an
`UnavailableError` (HTTP 503); the cause is logged, never returned.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import psycopg
from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from app.core.config import Settings
from app.core.context import Caller
from app.core.errors import UnavailableError

log = logging.getLogger("bi.postgres")

Connection = AsyncConnection[DictRow]
Pool = AsyncConnectionPool[Connection]

PING_TIMEOUT_SECONDS = 2.0
SLOW_MESSAGE = "The database took too long to answer. Please try again."


async def _make_read_only(conn: Connection) -> None:
    # Runs once for each new connection of the read-only pool.
    await conn.set_read_only(True)


def _pool(settings: Settings, dsn: str, name: str, *, read_only: bool = False) -> Pool:
    return AsyncConnectionPool(
        dsn,
        name=name,
        connection_class=AsyncConnection[DictRow],
        kwargs={
            "row_factory": dict_row,
            "connect_timeout": settings.pg_connect_timeout_seconds,
        },
        min_size=settings.pg_pool_min,
        max_size=settings.pg_pool_max,
        timeout=settings.pg_pool_timeout_seconds,
        configure=_make_read_only if read_only else None,
        open=False,
    )


class Postgres:
    def __init__(self, settings: Settings) -> None:
        self._statement_timeout_ms = settings.pg_statement_timeout_seconds * 1000
        self._query_timeout_ms = settings.query_timeout_seconds * 1000
        self._database = str(conninfo_to_dict(settings.pg_dsn).get("dbname") or "")
        self.app: Pool = _pool(settings, settings.pg_dsn, "bi-app")
        self.readonly: Pool = _pool(
            settings, settings.pg_readonly_dsn, "bi-readonly", read_only=True
        )

    # -- lifecycle ---------------------------------------------------------

    async def open(self) -> None:
        await self.app.open(wait=False)
        await self.readonly.open(wait=False)

    async def close(self) -> None:
        try:
            await self.app.close()
        finally:
            await self.readonly.close()

    # -- sessions ----------------------------------------------------------

    def session(self) -> AbstractAsyncContextManager[Connection]:
        """One read-write transaction as bi_app."""
        return self._session(self.app, self._statement_timeout_ms)

    def readonly_session(self) -> AbstractAsyncContextManager[Connection]:
        """One READ ONLY transaction as bi_readonly, with the shorter query limit."""
        return self._session(self.readonly, self._query_timeout_ms)

    def tenant_session(
        self, caller: Caller, *, read_only: bool = True
    ) -> AbstractAsyncContextManager[Connection]:
        """One transaction scoped to the caller's company and outlets by row security.

        `read_only=True` (the default) runs as bi_readonly: this is the only way
        AI-written SQL reaches the database. `read_only=False` runs as bi_app, for the
        product's own reads of business data.
        """
        if read_only:
            return self._session(self.readonly, self._query_timeout_ms, caller)
        return self._session(self.app, self._statement_timeout_ms, caller)

    @asynccontextmanager
    async def _session(
        self, pool: Pool, timeout_ms: int, caller: Caller | None = None
    ) -> AsyncIterator[Connection]:
        try:
            async with pool.connection() as conn, conn.transaction():
                # SET cannot take a bind parameter; the value is an int from our settings.
                await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
                if caller is not None:
                    # Both values are rebuilt from UUID objects, so only hex digits, dashes,
                    # commas and '*' can appear inside the quotes.
                    tenant = str(uuid.UUID(str(caller.tenant_id)))
                    outlets = caller.outlet_scope
                    if outlets != "*":
                        outlets = ",".join(str(uuid.UUID(part)) for part in outlets.split(","))
                    await conn.execute(f"SET LOCAL app.tenant_id = '{tenant}'")
                    await conn.execute(f"SET LOCAL app.outlet_ids = '{outlets}'")
                yield conn
        except psycopg.errors.QueryCanceled as exc:
            log.warning("statement cancelled", extra={"pool": pool.name})
            raise UnavailableError(SLOW_MESSAGE) from exc
        except PoolTimeout as exc:
            # no connection in time: the database is down, or every connection is busy
            log.warning("no connection available", extra={"pool": pool.name})
            raise UnavailableError() from exc
        except psycopg.OperationalError as exc:
            log.warning(
                "database connection failed",
                extra={"pool": pool.name, "kind": type(exc).__name__},
            )
            raise UnavailableError() from exc

    # -- health ------------------------------------------------------------

    async def ping(self, pool: Pool | None = None) -> None:
        """Raise unless the database answers (on the app pool unless another is given)."""
        async with (pool or self.app).connection(timeout=PING_TIMEOUT_SECONDS) as conn:
            await conn.execute("SELECT 1")

    async def server_version(self) -> str:
        async with self.app.connection(timeout=PING_TIMEOUT_SECONDS) as conn:
            row = await (await conn.execute("SHOW server_version")).fetchone()
            # "16.4 (Ubuntu 16.4-1.pgdg22.04+1)" -> "16.4"
            return str(row["server_version"]).split()[0] if row else ""

    @property
    def database(self) -> str:
        return self._database

    def pool_stats(self) -> dict[str, dict[str, int]]:
        """Sizes only. Nothing here names a host, a user or a password."""
        stats = {}
        for key, pool in (("app", self.app), ("readonly", self.readonly)):
            raw = pool.get_stats()
            stats[key] = {
                "min": pool.min_size,
                "max": pool.max_size,
                "size": int(raw.get("pool_size", 0)),
                "available": int(raw.get("pool_available", 0)),
                "waiting": int(raw.get("requests_waiting", 0)),
            }
        return stats
