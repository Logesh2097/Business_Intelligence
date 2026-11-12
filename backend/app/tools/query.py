"""run_query: one SQL statement through every check, then the database.

The same order as the SQL analyst (agents/sql_analyst.py): the guard, the aggregation
check, the correctness check, then `run_checked_sql` as bi_readonly under the
caller's row security. Every attempt is written to app.query_log. There is no other
way for a tool to run SQL, and no "trusted" path that skips a check.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.semantic.model import load_vocabulary
from app.sqlguard import (
    Catalog,
    Facts,
    Refused,
    check,
    check_aggregation,
    check_correctness,
)
from app.store import analytics
from app.store.analytics import QueryFailedError, QueryResult, QueryTimeoutError
from app.tools.context import ToolContext

LOGGED_AS = "[tool] run_query"


@dataclass
class QueryOutcome:
    status: str  # ok | refused | error | timeout
    sql: str | None = None
    result: QueryResult | None = None
    code: str | None = None  # why it was refused or failed
    reason: str | None = None
    notes: list[str] = field(default_factory=list)


class NoSuchTables(ValueError):
    """`only_tables` named no table that exists."""


async def run_query(
    ctx: ToolContext,
    sql: str,
    *,
    only_tables: frozenset[str] | None = None,
    asked: str | None = None,
) -> QueryOutcome:
    """`only_tables` can only narrow what the query may read (a domain agent's own
    tables); it can never add a table. `asked` is the question, kept in the log."""
    vocabulary = load_vocabulary()
    if only_tables:
        known = {table.table for table in vocabulary.tables}
        wanted = frozenset(name.strip().lower() for name in only_tables) & known
        if not wanted:
            raise NoSuchTables("none of the named tables exists")
        vocabulary = vocabulary.restricted_to(wanted)
    catalog = Catalog(vocabulary.catalog())
    max_rows = ctx.settings.query_max_rows

    async def log(status: str, text: str | None, reason: str | None = None,
                  result: QueryResult | None = None) -> None:  # fmt: skip
        await analytics.log_query(
            ctx.pg, ctx.caller, question=asked or LOGGED_AS, sql=text, status=status,
            reason=reason,
            row_count=result.row_count if result else None,
            duration_ms=result.duration_ms if result else None,
        )  # fmt: skip

    checked = check(sql, catalog, max_rows=max_rows + 1)
    if isinstance(checked, Refused):
        await log("rejected", sql, checked.code)
        return QueryOutcome("refused", sql, code=checked.code, reason=checked.reason)
    sound = check_aggregation(checked.sql, catalog, vocabulary.unique_keys())
    if isinstance(sound, Refused):
        await log("rejected", checked.sql, sound.code)
        return QueryOutcome("refused", checked.sql, code=sound.code, reason=sound.reason)
    reviewed = check_correctness(checked.sql, catalog, Facts(**vocabulary.correctness_facts()))
    if isinstance(reviewed, Refused):
        await log("rejected", checked.sql, reviewed.code)
        return QueryOutcome("refused", checked.sql, code=reviewed.code, reason=reviewed.reason)

    try:
        result = await analytics.run_checked_sql(ctx.pg, ctx.caller, checked.sql, max_rows=max_rows)
    except QueryTimeoutError:
        await log("timeout", checked.sql)
        return QueryOutcome("timeout", checked.sql, code="timeout",
                            reason="The query took too long and was cancelled.")  # fmt: skip
    except QueryFailedError as failure:
        await log("error", checked.sql, failure.sqlstate)
        return QueryOutcome("error", checked.sql, code=failure.sqlstate, reason=failure.reason)
    await log("ok", checked.sql, result=result)
    return QueryOutcome("ok", checked.sql, result=result, notes=list(reviewed.notes))
