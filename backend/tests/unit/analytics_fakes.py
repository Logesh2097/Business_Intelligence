"""Stand-ins for the database side of the question pipeline (MOCKED tests only)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.context import Caller
from app.store import analytics
from app.store.analytics import QueryResult

TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OUTLET_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def caller(role: str = "analyst", *, limited: bool = False) -> Caller:
    return Caller(
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        tenant_id=TENANT,
        tenant_name="Brew & Bite Cafés",
        role=role,
        timezone="Asia/Kolkata",
        currency="INR",
        all_outlets=not limited,
        outlet_ids=(OUTLET_A,) if limited else (),
        outlet_names=("Indiranagar",) if limited else (),
    )


def result(rows: list[dict[str, Any]], *, truncated: bool = False) -> QueryResult:
    columns = []
    for name, value in (rows[0] if rows else {}).items():
        kind = "text"
        if isinstance(value, (int, float)):
            kind = "money" if "revenue" in name else "number"
        columns.append({"name": name, "type": kind})
    return QueryResult(
        columns=columns, rows=rows, row_count=len(rows), truncated=truncated, duration_ms=3
    )


class FakeDatabase:
    """Replaces `run_checked_sql` and `log_query`. Answers are consumed in order."""

    def __init__(self, *answers: QueryResult | BaseException) -> None:
        self._answers = list(answers)
        self.executed: list[str] = []
        self.logged: list[dict[str, Any]] = []
        self.callers: list[Caller] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeDatabase:
        async def run(pg: Any, who: Caller, sql: str, *, max_rows: int) -> QueryResult:
            self.executed.append(sql)
            self.callers.append(who)
            answer = self._answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        async def log(pg: Any, who: Caller, **fields: Any) -> None:
            self.logged.append(fields)

        monkeypatch.setattr(analytics, "run_checked_sql", run)
        monkeypatch.setattr(analytics, "log_query", log)
        return self

    @property
    def statuses(self) -> list[str]:
        return [entry["status"] for entry in self.logged]
