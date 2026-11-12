"""The SQL analyst: one question in, one checked and executed query out.

    schema + metrics + dates -> model writes SQL -> guard -> aggregation -> correctness -> run
                                      ^              |         check          check        |
                                      └─ the reason ─┴───────────┴──────────────┴──────────┘
                                                                              (at most twice)

The model can only propose text. The guard decides whether it is safe to run. The
aggregation check refuses a query that adds up rows a join has repeated. The
correctness check refuses five more mistakes it can prove from the SQL, and adds notes
to the assumptions. The database decides what may be read, and every attempt is logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, Field

from app.agents.prompting import block, load_prompt
from app.core.context import Caller
from app.llm.base import LLM, LLMInvalidResponseError
from app.semantic.model import Vocabulary
from app.semantic.periods import periods_text
from app.semantic.render import metrics_text, schema_text
from app.semantic.retrieval import everything, relevant
from app.sqlguard import Catalog, Facts, Refused, check, check_aggregation, check_correctness
from app.store import analytics
from app.store.analytics import QueryFailedError, QueryResult, QueryTimeoutError
from app.store.postgres import Postgres
from app.tools.client import use_tool
from app.tools.context import ToolContext

log = logging.getLogger("bi.sql_analyst")

MAX_ATTEMPTS = 3  # the first try and two repairs

# what the user is told when there is no result to explain
FAILURE_MESSAGES = {
    "failed": "I could not build a safe query for this question. "
    "Try naming the figure and the period you want.",
    "timeout": "That question needs more data than can be read in one go. Try a shorter period.",
    "unanswerable": "This cannot be answered from the business data available.",
}


class SqlDraft(BaseModel):
    """What the model must return."""

    sql: str | None = Field(default=None, description="One SELECT statement, or null")
    explanation: str = Field(default="", description="One sentence")
    assumptions: list[str] = Field(default_factory=list)


@dataclass
class SqlOutcome:
    status: str  # answered | unanswerable | failed | timeout
    sql: str | None = None
    result: QueryResult | None = None
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)
    attempts: int = 0
    tables_shown: tuple[str, ...] = ()


def build_prompt(
    question: str,
    vocabulary: Vocabulary,
    today: date,
    guidance: str = "",
    whole_vocabulary: bool = False,
) -> tuple[str, str, tuple[str, ...]]:
    """The system and user messages, and the names of the tables that were shown.

    `guidance` is an agent's own prompt (its area and its rules), added after the
    shared SQL rules. It can narrow what is asked for, never what is allowed.
    `whole_vocabulary` shows every table given instead of choosing by the question's
    words: right for an agent, whose vocabulary is already only its own tables.
    """
    chosen = everything(vocabulary) if whole_vocabulary else relevant(question, vocabulary)
    user = "\n\n".join(
        [
            block("SCHEMA", schema_text(chosen.tables)),
            block("METRICS", metrics_text(chosen.metrics)),
            block("DATES", periods_text(today)),
            block("QUESTION", question),
        ]
    )
    system = load_prompt("sql_analyst")
    if guidance:
        system += "\n\n" + block("AGENT", guidance)
    return system, user, tuple(t.table for t in chosen.tables)


async def answer_with_sql(
    *,
    question: str,
    caller: Caller,
    llm: LLM,
    pg: Postgres,
    vocabulary: Vocabulary,
    today: date,
    max_rows: int,
    guidance: str = "",
    whole_vocabulary: bool = False,
    tools: ToolContext | None = None,
) -> SqlOutcome:
    """`tools` is given by callers that know the settings. With `BI_TOOLS_TRANSPORT=mcp`
    each query is then checked and run by the `run_query` tool on the MCP server (the
    same checks, in the same order, in that process) instead of in this one."""
    system, user, shown = build_prompt(question, vocabulary, today, guidance, whole_vocabulary)
    over_mcp = tools is not None and tools.settings.tools_transport == "mcp"
    catalog = Catalog(vocabulary.catalog())
    unique = vocabulary.unique_keys()
    facts = Facts(**vocabulary.correctness_facts())
    outcome = SqlOutcome(status="failed", tables_shown=shown)
    feedback = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        outcome.attempts = attempt
        try:
            draft = await llm.structured(
                task="sql" if attempt == 1 else "sql_repair",
                system=system,
                user=user + feedback,
                schema=SqlDraft,
            )
        except LLMInvalidResponseError:
            feedback = _feedback(None, "Your answer was not in the requested form.")
            continue

        sql = (draft.sql or "").strip()
        outcome.explanation, outcome.assumptions = draft.explanation, draft.assumptions
        if not sql:
            outcome.status = "unanswerable"
            return outcome

        if over_mcp and tools is not None:
            ran = await use_tool(
                tools,
                "run_query",
                {
                    "sql": sql,
                    # this analyst's own tables: the server narrows the guard to them
                    "only_tables": sorted(table.table for table in vocabulary.tables),
                    "asked": question[:2000],
                },
            )
            if ran.status == "ok":
                outcome.assumptions = draft.assumptions + [
                    note for note in ran.notes if note not in draft.assumptions
                ]
                outcome.status, outcome.sql = "answered", ran.sql or sql
                outcome.result = analytics.QueryResult(
                    columns=ran.columns, rows=ran.rows, row_count=ran.row_count,
                    truncated=ran.truncated, duration_ms=0,
                )  # fmt: skip
                return outcome
            if ran.status == "timeout":
                outcome.status, outcome.sql = "timeout", ran.sql or sql
                return outcome
            log.info("query refused", extra={"code": ran.code, "attempt": attempt})
            reason = ran.reason or "The query could not be run."
            feedback = _feedback(
                ran.sql or sql, f"PostgreSQL said: {reason}" if ran.status == "error" else reason
            )
            continue

        # one row more than the cap, so a result that was cut short can be told from a full one
        checked = check(sql, catalog, max_rows=max_rows + 1)
        if isinstance(checked, Refused):
            log.info("query refused", extra={"code": checked.code, "attempt": attempt})
            await analytics.log_query(
                pg, caller, question=question, sql=sql, status="rejected", reason=checked.code
            )
            feedback = _feedback(sql, checked.reason)
            continue

        # safe to run, but would its figures be right? Only the guard's own output is
        # looked at, and it is never changed here: the query runs as the guard left it
        sound = check_aggregation(checked.sql, catalog, unique)
        if isinstance(sound, Refused):
            log.info("query refused", extra={"code": sound.code, "attempt": attempt})
            await analytics.log_query(
                pg, caller, question=question, sql=checked.sql, status="rejected",
                reason=sound.code,
            )  # fmt: skip
            feedback = _feedback(checked.sql, sound.reason)
            continue

        # ... and five more mistakes that can be proved from the SQL alone (a DISTINCT
        # sum, a LEFT JOIN the WHERE undoes, sales with no status limit, a period cut
        # short on a timestamp, a count of combinations). Its notes say what a join or
        # a status condition leaves out; they change neither the query nor a figure.
        reviewed = check_correctness(checked.sql, catalog, facts)
        if isinstance(reviewed, Refused):
            log.info("query refused", extra={"code": reviewed.code, "attempt": attempt})
            await analytics.log_query(
                pg, caller, question=question, sql=checked.sql, status="rejected",
                reason=reviewed.code,
            )  # fmt: skip
            feedback = _feedback(checked.sql, reviewed.reason)
            continue
        outcome.assumptions = draft.assumptions + [
            note for note in reviewed.notes if note not in draft.assumptions
        ]

        try:
            result = await analytics.run_checked_sql(pg, caller, checked.sql, max_rows=max_rows)
        except QueryTimeoutError:
            await analytics.log_query(
                pg, caller, question=question, sql=checked.sql, status="timeout"
            )
            outcome.status, outcome.sql = "timeout", checked.sql
            return outcome  # asking again would only wait again
        except QueryFailedError as exc:
            log.info("query failed", extra={"sqlstate": exc.sqlstate, "attempt": attempt})
            await analytics.log_query(
                pg, caller, question=question, sql=checked.sql, status="error", reason=exc.sqlstate
            )
            feedback = _feedback(checked.sql, f"PostgreSQL said: {exc.reason}")
            continue

        await analytics.log_query(
            pg, caller, question=question, sql=checked.sql, status="ok",
            row_count=result.row_count, duration_ms=result.duration_ms,
        )  # fmt: skip
        outcome.status, outcome.sql, outcome.result = "answered", checked.sql, result
        return outcome

    return outcome


def _feedback(sql: str | None, reason: str) -> str:
    parts = ["\n\n" + block("PREVIOUS ATTEMPT FAILED", (f"{sql}\n\n" if sql else "") + reason)]
    parts.append("Write a corrected answer that follows every rule.")
    return "\n".join(parts)
