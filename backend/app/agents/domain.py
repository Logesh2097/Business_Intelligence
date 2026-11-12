"""A domain agent: a specialist for one area of the business (sales, inventory, customers).

    question -> the SQL analyst, limited to this agent's tables -> the result
             -> this agent's write-up: a summary and a few insights, numbers checked

An agent is data, not a new pipeline: a name, its responsibilities, the tables it may
read and its own prompt. Writing SQL, checking it, running it under the caller's
company and outlets, and logging it are the SQL analyst's and the store's, reused as
they are. The table list is enforced twice: only those tables are in the prompt, and
only those are in the guard's allow-list, so a query that reaches outside is refused.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.agents import sql_analyst
from app.agents.prompting import block, load_prompt
from app.agents.summarizer import plain_summary, result_block, unsupported_numbers
from app.core.clock import today
from app.core.config import Settings
from app.core.context import Caller
from app.llm.base import LLM, LLMError
from app.semantic.model import load_vocabulary
from app.store.analytics import QueryResult
from app.store.postgres import Postgres
from app.tools.context import ToolContext

log = logging.getLogger("bi.agents")

MAX_INSIGHTS = 4
TIMEOUT_MESSAGE = "This took too long to answer. Try a narrower question or a shorter period."


@dataclass(frozen=True)
class DomainAgent:
    name: str  # "sales_agent": the id in the API and the name of its prompt file
    title: str
    responsibilities: tuple[str, ...]
    tables: frozenset[str]  # the only tables this agent may read
    examples: tuple[str, ...]

    @property
    def prompt(self) -> str:
        return load_prompt(self.name)


class AgentReport(BaseModel):
    """What the model must return for the write-up."""

    summary: str = Field(description="The answer, one to three sentences")
    insights: list[str] = Field(default_factory=list, description="Up to four observations")


@dataclass
class AgentAnswer:
    agent: str
    question: str
    status: str  # answered | no_data | unanswerable | failed | timeout
    summary: str
    insights: list[str] = field(default_factory=list)
    sql: str | None = None
    columns: list[dict[str, str]] = field(default_factory=list)
    data: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    assumptions: list[str] = field(default_factory=list)
    scope_note: str | None = None


async def run(
    agent: DomainAgent,
    *,
    question: str,
    caller: Caller,
    llm: LLM,
    pg: Postgres,
    settings: Settings,
) -> AgentAnswer:
    """One question to one agent. Never longer than `BI_AGENT_TIMEOUT_SECONDS`."""
    try:
        return await asyncio.wait_for(
            _run(agent, question=question, caller=caller, llm=llm, pg=pg, settings=settings),
            timeout=settings.agent_timeout_seconds,
        )
    except asyncio.TimeoutError:
        log.warning("agent timed out", extra={"agent": agent.name})
        return AgentAnswer(
            agent=agent.name,
            question=question,
            status="timeout",
            summary=TIMEOUT_MESSAGE,
            scope_note=caller.scope_note,
        )


async def _run(
    agent: DomainAgent,
    *,
    question: str,
    caller: Caller,
    llm: LLM,
    pg: Postgres,
    settings: Settings,
) -> AgentAnswer:
    outcome = await sql_analyst.answer_with_sql(
        question=question,
        caller=caller,
        llm=llm,
        pg=pg,
        vocabulary=load_vocabulary().restricted_to(agent.tables),
        today=today(settings, caller.timezone),
        max_rows=settings.query_max_rows,
        guidance=agent.prompt,
        whole_vocabulary=True,  # all of this agent's tables, and only those
        tools=ToolContext(caller=caller, pg=pg, settings=settings),
    )
    answer = AgentAnswer(
        agent=agent.name,
        question=question,
        status=outcome.status,
        summary="",
        sql=outcome.sql,
        assumptions=outcome.assumptions,
        scope_note=caller.scope_note,
    )
    log.info(
        "agent ran",
        extra={"agent": agent.name, "status": outcome.status, "attempts": outcome.attempts},
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
    answer.summary, answer.insights = await write_up(
        agent, question=question, result=result, caller=caller, llm=llm
    )
    return answer


async def write_up(
    agent: DomainAgent, *, question: str, result: QueryResult, caller: Caller, llm: LLM
) -> tuple[str, list[str]]:
    """The summary and the insights. Every figure in either must be a value of the result.

    A summary with a figure that is not in the result is asked for once more, then
    replaced by a sentence built by code. An insight with such a figure is dropped.
    """
    note = f" {caller.scope_note}" if caller.scope_note else ""
    if result.row_count == 0:
        # nothing to explain: the model is not asked, so it cannot invent a figure
        return plain_summary(result, caller) + note, []

    system = load_prompt("agent_report") + "\n\n" + block("AGENT", agent.prompt)
    user = block("QUESTION", question) + "\n\n" + result_block(result, caller)
    extra = ""
    insights: list[str] = []
    for _ in range(2):
        try:
            report = await llm.structured(
                task="agent_report", system=system, user=user + extra, schema=AgentReport, fast=True
            )
        except LLMError as exc:
            # the data is already here; a failed write-up must not lose it
            log.warning("write-up failed", extra={"agent": agent.name, "kind": type(exc).__name__})
            break
        insights = [
            text
            for text in (i.strip() for i in report.insights[:MAX_INSIGHTS])
            if text and not unsupported_numbers(text, question, result)
        ]
        summary = report.summary.strip()
        if summary and not unsupported_numbers(summary, question, result):
            return summary + note, insights
        log.warning("write-up had unsupported numbers", extra={"agent": agent.name})
        extra = (
            "\n\nYour previous answer used numbers that are not in RESULT. "
            "Use only values from RESULT."
        )
    return plain_summary(result, caller) + note, insights
