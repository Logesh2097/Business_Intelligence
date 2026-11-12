"""Reading the query log: what was asked and how it ended (schema `app`).

Every query names the company. The log holds every piece of AI-written SQL, run or
refused (store/analytics.py writes it).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from app.store.postgres import Postgres

MAX_ROWS = 200


@dataclass(frozen=True)
class LogEntry:
    id: str
    created_at: dt.datetime
    asked_by: str  # the user's email
    question: str
    status: str  # ok | rejected | error | timeout
    reason: str | None
    row_count: int | None
    duration_ms: int | None
    sql: str | None


async def recent(pg: Postgres, *, tenant_id: uuid.UUID, limit: int) -> list[LogEntry]:
    async with pg.session() as conn:
        rows = await (
            await conn.execute(
                """
                SELECT q.id, q.created_at, u.email, q.question, q.status, q.reason,
                       q.row_count, q.duration_ms, q.sql
                FROM app.query_log q
                JOIN app.users u ON u.id = q.user_id AND u.tenant_id = q.tenant_id
                WHERE q.tenant_id = %s
                ORDER BY q.created_at DESC
                LIMIT %s
                """,
                (tenant_id, max(1, min(limit, MAX_ROWS))),
            )
        ).fetchall()
    return [
        LogEntry(
            id=str(row["id"]),
            created_at=row["created_at"],
            asked_by=str(row["email"]),
            question=str(row["question"]),
            status=str(row["status"]),
            reason=row["reason"],
            row_count=row["row_count"],
            duration_ms=row["duration_ms"],
            sql=row["sql"],
        )
        for row in rows
    ]


async def counts(pg: Postgres, *, tenant_id: uuid.UUID, since: dt.datetime) -> dict[str, int]:
    async with pg.session() as conn:
        rows = await (
            await conn.execute(
                "SELECT status, COUNT(*) AS n FROM app.query_log "
                "WHERE tenant_id = %s AND created_at >= %s GROUP BY status",
                (tenant_id, since),
            )
        ).fetchall()
    return {str(row["status"]): int(row["n"]) for row in rows}
