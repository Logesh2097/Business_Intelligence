"""Running a checked query for a caller, and the log of every attempt.

`run_checked_sql` is the only function that executes AI-written SQL. It must be given
SQL that came out of the guard; it runs it as bi_readonly, read-only, under the
caller's row security, with the statement timeout and the row cap.
"""

from __future__ import annotations

import datetime as dt
import decimal
import time
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg

from app.core.context import Caller
from app.store.postgres import Postgres

MAX_CELL_CHARS = 500
_MONEY_WORDS = ("revenue", "sales", "amount", "total", "cost", "profit", "price", "spend",
                "expense", "value", "income", "turnover", "aov")  # fmt: skip
_PERCENT_WORDS = ("rate", "percent", "pct", "share", "margin_pct", "ratio")
_NUMERIC_OIDS = {20, 21, 23, 700, 701, 1700}  # int8, int2, int4, float4, float8, numeric
_DATE_OIDS = {1082, 1114, 1184}  # date, timestamp, timestamptz


class QueryTimeoutError(Exception):
    """The statement ran past its limit and was cancelled by the database."""


class QueryFailedError(Exception):
    """PostgreSQL refused or failed the statement. `reason` is safe to give the model."""

    def __init__(self, sqlstate: str, reason: str) -> None:
        self.sqlstate = sqlstate
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class QueryResult:
    columns: list[dict[str, str]]  # [{"name": ..., "type": text|number|money|percent|date}]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    duration_ms: int


def _column_type(name: str, oid: int) -> str:
    lowered = name.lower()
    if oid in _DATE_OIDS:
        return "date"
    if oid in _NUMERIC_OIDS:
        if any(word in lowered for word in _PERCENT_WORDS):
            return "percent"
        if any(word in lowered for word in _MONEY_WORDS):
            return "money"
        return "number"
    return "text"


def _plain(value: Any) -> Any:
    """A value that JSON can carry."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    text = str(value)
    return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS] + "…"


async def run_checked_sql(pg: Postgres, caller: Caller, sql: str, *, max_rows: int) -> QueryResult:
    started = time.perf_counter()
    async with pg.tenant_session(caller) as conn:
        try:
            # no parameters: the text was printed by the guard from its own syntax tree
            cursor = await conn.execute(sql)
            fetched = await cursor.fetchmany(max_rows + 1)
            description = cursor.description or []
        except psycopg.errors.QueryCanceled as exc:
            raise QueryTimeoutError() from exc
        except psycopg.OperationalError:
            raise  # the database itself is in trouble: let the session report it
        except psycopg.Error as exc:
            message = exc.diag.message_primary or type(exc).__name__
            raise QueryFailedError(exc.sqlstate or "", message) from exc

    truncated = len(fetched) > max_rows
    rows = fetched[:max_rows]
    columns = [{"name": c.name, "type": _column_type(c.name, c.type_code)} for c in description]
    return QueryResult(
        columns=columns,
        rows=[{key: _plain(value) for key, value in row.items()} for row in rows],
        row_count=len(rows),
        truncated=truncated,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )


async def log_query(
    pg: Postgres,
    caller: Caller,
    *,
    question: str,
    sql: str | None,
    status: str,
    reason: str | None = None,
    row_count: int | None = None,
    duration_ms: int | None = None,
) -> None:
    """One row for every piece of AI-written SQL, run or refused."""
    async with pg.session() as conn:
        await conn.execute(
            """
            INSERT INTO app.query_log
                (tenant_id, user_id, question, sql, status, reason, row_count, duration_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (caller.tenant_id, caller.user_id, question, sql, status, reason, row_count,
             duration_ms),
        )  # fmt: skip
