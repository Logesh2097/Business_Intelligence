"""The domain agents, each asked on its own (docs/api.md §5a).

GET  /api/v1/agents                  which agents exist and what each one does
POST /api/v1/agents/{agent}/query    one question to one agent
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.agents import domain
from app.agents.registry import AGENTS
from app.api.deps import CallerDep, LLMDep, SettingsDep, StoresDep
from app.api.routes.analytics import ColumnOut, QueryRequest, model_unavailable
from app.core.errors import NotFoundError, PermissionDeniedError
from app.llm.base import LLMUnavailableError

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


class AgentOut(BaseModel):
    agent: str
    title: str
    responsibilities: list[str]
    example_questions: list[str]


class AgentResponse(BaseModel):
    agent: str
    question: str
    status: Literal["answered", "no_data", "unanswerable", "failed", "timeout"]
    summary: str
    insights: list[str]
    sql: str | None = Field(description="The query that ran. Null unless the caller may see SQL")
    columns: list[ColumnOut]
    data: list[dict[str, Any]]
    row_count: int
    truncated: bool
    assumptions: list[str]
    scope_note: str | None


@router.get("")
async def list_agents(caller: CallerDep) -> list[AgentOut]:
    if not caller.can("chat:ask"):
        raise PermissionDeniedError()
    return [
        AgentOut(
            agent=a.name,
            title=a.title,
            responsibilities=list(a.responsibilities),
            example_questions=list(a.examples),
        )
        for a in AGENTS.values()
    ]


@router.post("/{agent}/query")
async def ask_agent(
    agent: str,
    body: QueryRequest,
    caller: CallerDep,
    stores: StoresDep,
    llm: LLMDep,
    settings: SettingsDep,
) -> AgentResponse:
    if not caller.can("chat:ask"):
        raise PermissionDeniedError()
    chosen = AGENTS.get(agent)
    if chosen is None:
        raise NotFoundError("There is no such agent.")
    try:
        answer = await domain.run(
            chosen,
            question=body.question,
            caller=caller,
            llm=llm,
            pg=stores.postgres,
            settings=settings,
        )
    except LLMUnavailableError as exc:
        raise model_unavailable(exc) from exc
    return AgentResponse(
        agent=answer.agent,
        question=answer.question,
        status=answer.status,
        summary=answer.summary,
        insights=answer.insights,
        sql=answer.sql if caller.can("sql:view") else None,
        columns=[ColumnOut(**c) for c in answer.columns],
        data=answer.data,
        row_count=answer.row_count,
        truncated=answer.truncated,
        assumptions=answer.assumptions,
        scope_note=answer.scope_note,
    )
