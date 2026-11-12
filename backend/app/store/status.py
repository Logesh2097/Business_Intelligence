"""The database status report: is each store answering, how fast, which version, and
how full are the connection pools.

Nothing here returns a host, a port, a user name, a password or an error message:
only a short class of failure (docs/api.md §2).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.store import health
from app.store.connections import Stores

log = logging.getLogger("bi.status")

Details = dict[str, Any]


async def _probe(name: str, describe: Callable[[], Awaitable[Details]]) -> Details:
    """Run one store's check; a failure of any kind becomes `ok: false` with its class."""
    started = time.perf_counter()
    try:
        details = await asyncio.wait_for(describe(), timeout=health.CHECK_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a "down" answer
        log.warning("status check failed", extra={"store": name, "kind": type(exc).__name__})
        ms = round((time.perf_counter() - started) * 1000, 1)
        return {"ok": False, "ms": ms, "error": health.classify(exc)}
    return {"ok": True, "ms": round((time.perf_counter() - started) * 1000, 1), **details}


async def _postgres(stores: Stores) -> Details:
    pg = stores.postgres

    async def describe() -> Details:
        return {"version": await pg.server_version()}

    async def readonly_ok() -> bool:
        try:
            await asyncio.wait_for(pg.ping(pg.readonly), timeout=health.CHECK_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "status check failed",
                extra={"store": "postgres-readonly", "kind": type(exc).__name__},
            )
            return False
        return True

    report, readonly = await asyncio.gather(_probe("postgres", describe), readonly_ok())
    pools = pg.pool_stats()
    pools["app"] = {"ok": report["ok"], **pools["app"]}
    pools["readonly"] = {"ok": readonly, **pools["readonly"]}
    return {**report, "database": pg.database, "pools": pools}


async def _mongo(stores: Stores) -> Details:
    mongo = stores.mongo

    async def describe() -> Details:
        return {"version": await mongo.server_version()}

    report = await _probe("mongo", describe)
    return {**report, "database": mongo.database, "pool_max": mongo.pool_max}


async def _qdrant(stores: Stores) -> Details:
    async def describe() -> Details:
        return {"version": await stores.qdrant.server_version()}

    return await _probe("qdrant", describe)


async def database_status(stores: Stores) -> Details:
    postgres, mongo, qdrant = await asyncio.gather(
        _postgres(stores), _mongo(stores), _qdrant(stores)
    )
    databases = {"postgres": postgres, "mongo": mongo, "qdrant": qdrant}
    # the read-only pool counts: without it no question can be answered later
    all_ok = all(d["ok"] for d in databases.values()) and postgres["pools"]["readonly"]["ok"]
    return {"status": "ok" if all_ok else "degraded", "databases": databases}
