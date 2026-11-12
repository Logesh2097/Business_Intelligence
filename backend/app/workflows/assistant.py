"""The assistant: a question goes to the supervisor, then to whoever it chose.

                    supervise
        ┌──────────┬────┴─────┬──────────────┬───────────────┐
     clarify   unsupported  general        agents (1 to 3)     documents
        │          │           │         ┌─────┼─────┐           │
        │          │      run_general  run_agent  ...      run_documents
        └──────────┴───────────┴─────────┴─────┴─────┴───────────┘
                           aggregate

A LangGraph graph. The nodes add no way of reading data: `run_agent` calls the
existing `domain.run`, `run_general` calls the existing `answer_question`, and both
take the caller from the run's context, never from the model. Agents chosen together
do not read each other's result, so they run at the same time. `run_documents` calls
the document agent, which answers from the company's uploaded documents (rag.md).
"""

from __future__ import annotations

import asyncio
import logging
import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send
from pydantic import BaseModel

from app.agents import document_agent, domain, supervisor
from app.agents.prompting import block, load_prompt
from app.agents.registry import AGENTS
from app.agents.summarizer import result_block, unsupported_numbers
from app.agents.supervisor import DOCUMENTS, GENERAL, Plan, Task
from app.core.config import Settings
from app.core.context import Caller
from app.core.errors import AppError
from app.llm.base import LLM, LLMError
from app.llm.embeddings import Embedder
from app.store.analytics import QueryResult
from app.store.postgres import Postgres
from app.store.qdrant import Qdrant
from app.tools.context import ToolContext
from app.workflows.analytics import answer_question

log = logging.getLogger("bi.assistant")

PART_FAILED = "This part could not be answered right now. Please try again."
TIMEOUT_MESSAGE = "This took too long to answer. Try a narrower question or a shorter period."
_HAS_ROWS = ("answered",)
_RAN = ("answered", "no_data")


@dataclass
class Part:
    """What one agent, or the general workflow, returned."""

    agent: str  # an agent's name, or "general"
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
    sources: list[document_agent.Source] = field(default_factory=list)  # documents only


@dataclass
class AssistantAnswer:
    question: str
    status: str  # answered | partial | no_data | unanswerable | needs_clarification
    #              | unsupported | failed | timeout
    route: str  # agents | general | documents | clarify | unsupported | undecided
    reason: str
    summary: str
    clarification: str | None = None
    results: list[Part] = field(default_factory=list)
    scope_note: str | None = None


@dataclass
class Context:
    """What every node may use. Set by our code for one run; the model never sees it."""

    caller: Caller
    llm: LLM
    pg: Postgres
    settings: Settings
    qdrant: Qdrant | None = None  # with the embedder: what document search needs
    embedder: Embedder | None = None
    finished: list[Part] = field(default_factory=list)  # kept so a timeout can show them


class State(TypedDict, total=False):
    question: str  # the user's words; never overwritten
    plan: Plan
    parts: Annotated[list[Part], operator.add]
    answer: AssistantAnswer


class AgentJob(TypedDict):
    """What one `run_agent` branch is sent: its own task, not the whole state."""

    task: Task


class Combined(BaseModel):
    text: str


# -- nodes -----------------------------------------------------------------------------


async def supervise(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    return {"plan": await supervisor.decide(state["question"], runtime.context.llm)}


def dispatch(state: State) -> list[Send] | str:
    plan = state["plan"]
    if plan.route == "agents":
        return [Send("run_agent", {"task": task}) for task in plan.tasks]
    if plan.route == "general":
        return "run_general"
    if plan.route == "documents":
        return "run_documents"
    return "aggregate"


async def run_agent(state: AgentJob, runtime: Runtime[Context]) -> dict[str, Any]:
    task = state["task"]  # this branch's own input, sent by `dispatch`
    ctx = runtime.context
    try:
        answer = await domain.run(
            AGENTS[task.target],
            question=task.question,
            caller=ctx.caller,
            llm=ctx.llm,
            pg=ctx.pg,
            settings=ctx.settings,
        )
    except (LLMError, AppError) as exc:
        return _keep(ctx, _failed(task, exc))
    return _keep(
        ctx,
        Part(
            agent=answer.agent,
            question=task.question,
            status=answer.status,
            summary=answer.summary,
            insights=answer.insights,
            sql=answer.sql,
            columns=answer.columns,
            data=answer.data,
            row_count=answer.row_count,
            truncated=answer.truncated,
            assumptions=answer.assumptions,
        ),
    )


async def run_general(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    task = state["plan"].tasks[0]
    ctx = runtime.context
    try:
        answer = await answer_question(
            question=task.question, caller=ctx.caller, llm=ctx.llm, pg=ctx.pg, settings=ctx.settings
        )
    except (LLMError, AppError) as exc:
        return _keep(ctx, _failed(task, exc))
    return _keep(
        ctx,
        Part(
            agent=GENERAL,
            question=task.question,
            status=answer.status,
            summary=answer.summary,
            sql=answer.sql,
            columns=answer.columns,
            data=answer.data,
            row_count=answer.row_count,
            truncated=answer.truncated,
            assumptions=answer.assumptions,
        ),
    )


async def run_documents(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    task = state["plan"].tasks[0]
    ctx = runtime.context
    tools = ToolContext(
        caller=ctx.caller, pg=ctx.pg, settings=ctx.settings, qdrant=ctx.qdrant,
        embedder=ctx.embedder,
    )  # fmt: skip
    try:
        answer = await document_agent.answer(task.question, ctx=tools, llm=ctx.llm)
    except AppError as exc:
        # search is down, not set up, or not allowed: say which, as the error words it
        log.warning("part failed", extra={"target": task.target, "kind": type(exc).__name__})
        return _keep(
            ctx, Part(agent=DOCUMENTS, question=task.question, status="failed", summary=exc.message)
        )
    except LLMError as exc:  # the embedding of the question could not be made
        return _keep(ctx, _failed(task, exc))
    return _keep(
        ctx,
        Part(
            agent=DOCUMENTS,
            question=task.question,
            status=answer.status,
            summary=answer.summary,
            sources=answer.sources,
        ),
    )


async def aggregate(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    ctx = runtime.context
    plan, question = state["plan"], state["question"]
    answer = AssistantAnswer(
        question=question,
        status="failed",
        route=plan.route,
        reason=plan.reason,
        summary="",
        scope_note=ctx.caller.scope_note,
    )
    if plan.route == "clarify":
        answer.status, answer.clarification = "needs_clarification", plan.clarification
        answer.summary = plan.clarification
    elif plan.route == "unsupported":
        answer.status, answer.summary = "unsupported", supervisor.UNSUPPORTED
    elif plan.route == "undecided":
        answer.summary = supervisor.UNDECIDED
    else:
        order = [task.target for task in plan.tasks]
        answer.results = sorted(state.get("parts", []), key=lambda p: order.index(p.agent))
        answer.status = overall_status(answer.results)
        answer.summary = await combined_summary(question, answer.results, ctx.caller, ctx.llm)
    return {"answer": answer}


# -- helpers ---------------------------------------------------------------------------


def _keep(ctx: Context, part: Part) -> dict[str, Any]:
    ctx.finished.append(part)
    return {"parts": [part]}


def _failed(task: Task, exc: Exception) -> Part:
    # the cause is logged; the user is told only that this part is missing
    log.warning("part failed", extra={"target": task.target, "kind": type(exc).__name__})
    return Part(agent=task.target, question=task.question, status="failed", summary=PART_FAILED)


def overall_status(parts: list[Part]) -> str:
    if len(parts) == 1:
        return parts[0].status
    statuses = [p.status for p in parts]
    ran = [s for s in statuses if s in _RAN]
    if len(ran) == len(statuses):
        return "no_data" if all(s == "no_data" for s in statuses) else "answered"
    if ran:
        return "partial"
    if all(s == "unanswerable" for s in statuses):
        return "unanswerable"
    return "timeout" if all(s == "timeout" for s in statuses) else "failed"


def _title(part: Part) -> str:
    if part.agent == DOCUMENTS:
        return document_agent.TITLE
    return AGENTS[part.agent].title if part.agent in AGENTS else "General analysis"


def _without_note(text: str, caller: Caller) -> str:
    note = caller.scope_note
    return text[: -len(note)].rstrip() if note and text.endswith(note) else text


def joined_by_code(parts: list[Part], caller: Caller) -> str:
    """Each part's own, already checked, summary under its agent's name."""
    lines = [f"{_title(p)}: {_without_note(p.summary, caller)}" for p in parts]
    note = f" {caller.scope_note}" if caller.scope_note else ""
    return " ".join(lines) + note


async def combined_summary(question: str, parts: list[Part], caller: Caller, llm: LLM) -> str:
    """One answer for the whole question. Every figure must be a value of some part's result."""
    if len(parts) == 1:
        return parts[0].summary  # one part: its answer, passed through untouched
    with_rows = [p for p in parts if p.status in _HAS_ROWS]
    if len(with_rows) < 2:
        return joined_by_code(parts, caller)  # nothing to combine: say what each part said

    results = [
        QueryResult(
            columns=p.columns, rows=p.data, row_count=p.row_count, truncated=p.truncated,
            duration_ms=0,
        )
        for p in with_rows
    ]  # fmt: skip
    pieces = [
        f"{_title(p)} was asked: {p.question}\n{result_block(r, caller)}"
        for p, r in zip(with_rows, results, strict=True)
    ]
    user = block("QUESTION", question) + "\n\n" + block("PARTS", "\n\n".join(pieces))
    try:
        combined = await llm.structured(
            task="supervisor_report",
            system=load_prompt("supervisor_report"),
            user=user,
            schema=Combined,
            fast=True,
        )
    except LLMError as exc:
        log.warning("combined summary failed", extra={"kind": type(exc).__name__})
        return joined_by_code(parts, caller)

    text = combined.text.strip()
    # a figure is fine if it is a value of any one part's result
    wrong = set.intersection(*(set(unsupported_numbers(text, question, r)) for r in results))
    if not text or wrong:
        log.warning("combined summary had unsupported numbers", extra={"count": len(wrong)})
        return joined_by_code(parts, caller)

    missing = [p for p in parts if p.status not in _HAS_ROWS]
    for part in missing:
        text += f" {_title(part)}: {_without_note(part.summary, caller)}"
    return text + (f" {caller.scope_note}" if caller.scope_note else "")


# -- the graph -------------------------------------------------------------------------


def build_graph() -> Any:
    graph = StateGraph(State, context_schema=Context)
    graph.add_node("supervise", supervise)
    graph.add_node("run_agent", run_agent, input_schema=AgentJob)
    graph.add_node("run_general", run_general)
    graph.add_node("run_documents", run_documents)
    graph.add_node("aggregate", aggregate)
    graph.add_edge(START, "supervise")
    graph.add_conditional_edges(
        "supervise", dispatch, ["run_agent", "run_general", "run_documents", "aggregate"]
    )
    graph.add_edge("run_agent", "aggregate")
    graph.add_edge("run_general", "aggregate")
    graph.add_edge("run_documents", "aggregate")
    graph.add_edge("aggregate", END)
    return graph.compile()  # no checkpointer: nothing about a run is stored


GRAPH = build_graph()


async def ask(
    *,
    question: str,
    caller: Caller,
    llm: LLM,
    pg: Postgres,
    settings: Settings,
    qdrant: Qdrant | None = None,
    embedder: Embedder | None = None,
) -> AssistantAnswer:
    """One question through the graph. Never longer than `BI_ASSISTANT_TIMEOUT_SECONDS`.

    `qdrant` and `embedder` are what the documents route needs. Without them a
    documents question is answered with "document search is not set up".
    """
    ctx = Context(
        caller=caller, llm=llm, pg=pg, settings=settings, qdrant=qdrant, embedder=embedder
    )
    try:
        state = await asyncio.wait_for(
            GRAPH.ainvoke({"question": question}, context=ctx),
            timeout=settings.assistant_timeout_seconds,
        )
    except asyncio.TimeoutError:
        log.warning("assistant timed out", extra={"finished": len(ctx.finished)})
        return AssistantAnswer(
            question=question,
            status="timeout",
            route="undecided",
            reason="",
            summary=TIMEOUT_MESSAGE,
            results=list(ctx.finished),
            scope_note=caller.scope_note,
        )
    answer: AssistantAnswer = state["answer"]
    return answer
