"""The question-to-answer workflow of this phase: write SQL, run it, explain it.

Two steps in a straight line. The LangGraph graph with a supervisor and more agents
replaces this function in a later phase; the steps themselves stay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents import sql_analyst, summarizer
from app.core.clock import today
from app.core.config import Settings
from app.core.context import Caller
from app.llm.base import LLM
from app.semantic.model import load_vocabulary
from app.store.postgres import Postgres
from app.tools.context import ToolContext


@dataclass
class AnalyticsAnswer:
    question: str
    status: str  # answered | no_data | unanswerable | failed | timeout
    summary: str
    sql: str | None = None
    columns: list[dict[str, str]] = field(default_factory=list)
    data: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    assumptions: list[str] = field(default_factory=list)
    scope_note: str | None = None


async def answer_question(
    *, question: str, caller: Caller, llm: LLM, pg: Postgres, settings: Settings
) -> AnalyticsAnswer:
    outcome = await sql_analyst.answer_with_sql(
        question=question,
        caller=caller,
        llm=llm,
        pg=pg,
        vocabulary=load_vocabulary(),
        today=today(settings, caller.timezone),
        max_rows=settings.query_max_rows,
        tools=ToolContext(caller=caller, pg=pg, settings=settings),
    )
    answer = AnalyticsAnswer(
        question=question,
        status=outcome.status,
        summary="",
        sql=outcome.sql,
        assumptions=outcome.assumptions,
        scope_note=caller.scope_note,
    )

    if outcome.result is None:
        answer.summary = sql_analyst.FAILURE_MESSAGES[outcome.status]
        if outcome.status == "unanswerable" and outcome.explanation:
            answer.summary = outcome.explanation
        return answer

    result = outcome.result
    answer.columns, answer.data = result.columns, result.rows
    answer.row_count, answer.truncated = result.row_count, result.truncated
    if result.row_count == 0:
        answer.status = "no_data"
    answer.summary = await summarizer.summarise(
        question=question, result=result, caller=caller, llm=llm
    )
    return answer
