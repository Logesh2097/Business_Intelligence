"""Readiness checks: does each store answer?

A check returns a short class of failure ("timeout", "unreachable", "error"),
never the exception text, which could hold a connection string.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
import psycopg
from pymongo.errors import PyMongoError

from app.store.connections import Stores

log = logging.getLogger("bi.health")

CHECK_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    ms: float
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        body: dict[str, object] = {"ok": self.ok, "ms": self.ms}
        if self.error:
            body["error"] = self.error
        return body


async def check_postgres(stores: Stores) -> None:
    await stores.postgres.ping()


async def check_mongo(stores: Stores) -> None:
    await stores.mongo.ping()


async def check_qdrant(stores: Stores) -> None:
    await stores.qdrant.ping()


Check = Callable[[Stores], Awaitable[None]]

CHECKS: dict[str, Check] = {
    "postgres": check_postgres,
    "mongo": check_mongo,
    "qdrant": check_qdrant,
}


def classify(exc: BaseException) -> str:
    """A short class of failure that is safe to return to a caller."""
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, (OSError, httpx.TransportError, psycopg.OperationalError, PyMongoError)):
        return "unreachable"
    return "error"


async def run_check(name: str, check: Check, stores: Stores) -> CheckResult:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(check(stores), timeout=CHECK_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a "down" answer
        ms = round((time.perf_counter() - started) * 1000, 1)
        log.warning("store check failed", extra={"store": name, "kind": type(exc).__name__})
        return CheckResult(ok=False, ms=ms, error=classify(exc))
    return CheckResult(ok=True, ms=round((time.perf_counter() - started) * 1000, 1))


async def run_all(stores: Stores) -> dict[str, CheckResult]:
    names = list(CHECKS)
    results = await asyncio.gather(*(run_check(n, CHECKS[n], stores) for n in names))
    return dict(zip(names, results, strict=True))
