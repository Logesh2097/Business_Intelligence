"""Stand-ins for the database drivers, so unit tests need no server.

Everything in tests/unit that uses these is a MOCKED test: it proves how our code
behaves when the driver answers or fails, not that a real server works. The real
thing is tested in tests/integration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class FakeCursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, Any] | None:
        return self._row


class FakeConnection:
    def __init__(self, rows: dict[str, dict[str, Any]] | None = None) -> None:
        self.statements: list[str] = []
        self.events: list[str] = []
        self._rows = rows or {}
        self.fail_on: dict[str, BaseException] = {}

    async def execute(self, sql: str, params: object = None) -> FakeCursor:
        self.statements.append(sql)
        for fragment, error in self.fail_on.items():
            if fragment in sql:
                raise error
        return FakeCursor(self._rows.get(sql))

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.events.append("begin")
        try:
            yield
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")


class FakePool:
    """Hands out one FakeConnection, or raises `error` instead of connecting."""

    def __init__(
        self,
        name: str = "fake",
        *,
        connection: FakeConnection | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.name = name
        self.min_size, self.max_size = 0, 10
        self.conn = connection or FakeConnection()
        self.error = error
        self.checked_out = 0
        self.returned = 0
        self.events: list[str] = []

    @asynccontextmanager
    async def connection(self, timeout: float | None = None) -> AsyncIterator[FakeConnection]:
        if self.error is not None:
            raise self.error
        self.checked_out += 1
        try:
            yield self.conn
        finally:
            self.returned += 1

    async def open(self, wait: bool = False) -> None:
        self.events.append(f"open(wait={wait})")

    async def close(self) -> None:
        self.events.append("close")

    def get_stats(self) -> dict[str, int]:
        return {"pool_size": 2, "pool_available": 1, "requests_waiting": 0}


class FakeAdmin:
    def __init__(self, error: BaseException | None) -> None:
        self._error = error
        self.commands: list[str] = []

    async def command(self, name: str) -> dict[str, int]:
        self.commands.append(name)
        if self._error is not None:
            raise self._error
        return {"ok": 1}


class FakeMongoClient:
    def __init__(self, *, error: BaseException | None = None, version: str = "7.0.14") -> None:
        self.admin = FakeAdmin(error)
        self._error = error
        self._version = version
        self.closed = False

    async def server_info(self) -> dict[str, str]:
        if self._error is not None:
            raise self._error
        return {"version": self._version}

    async def close(self) -> None:
        self.closed = True
