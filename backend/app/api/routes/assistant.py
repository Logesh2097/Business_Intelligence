"""POST /api/v1/assistant/query: the supervisor chooses who answers (docs/api.md §5b)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CallerDep, EmbedderDep, LLMDep, SettingsDep, StoresDep
from app.api.routes.analytics import ColumnOut, QueryRequest, model_unavailable
from app.core.errors import PermissionDeniedError
from app.llm.base import LLMUnavailableError
from app.workflows import assistant

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])

PartStatus = Literal["answered", "no_data", "unanswerable", "failed", "timeout"]


class SourceOut(BaseModel):
    number: int = Field(description="The [n] this source has in the summary")
    document_id: str
    filename: str
    title: str
    page: int | None = Field(description="The PDF page; null for other kinds of file")
    quote: str = Field(description="The start of the passage, as stored")
    score: float


class PartOut(BaseModel):
    agent: Literal["sales_agent", "inventory_agent", "customer_agent", "general", "document_agent"]
    question: str = Field(description="What this part was asked")
    status: PartStatus
    summary: str
    insights: list[str]
    sql: str | None = Field(description="The query that ran. Null unless the caller may see SQL")
    columns: list[ColumnOut]
    data: list[dict[str, Any]]
    row_count: int
    truncated: bool
    assumptions: list[str]
    sources: list[SourceOut] = Field(
        default_factory=list, description="Document passages the summary cites; documents only"
    )


class AssistantResponse(BaseModel):
    question: str
    status: Literal[
        "answered", "partial", "no_data", "unanswerable", "needs_clarification",
        "unsupported", "failed", "timeout",
    ]  # fmt: skip
    route: Literal["agents", "general", "documents", "clarify", "unsupported", "undecided"]
    reason: str
    summary: str
    clarification: str | None
    results: list[PartOut]
    scope_note: str | None


@router.post("/query")
async def query(
    body: QueryRequest,
    caller: CallerDep,
    stores: StoresDep,
    llm: LLMDep,
    settings: SettingsDep,
    embedder: EmbedderDep,
) -> AssistantResponse:
    if not caller.can("chat:ask"):
        raise PermissionDeniedError()
    try:
        answer = await assistant.ask(
            question=body.question,
            caller=caller,
            llm=llm,
            pg=stores.postgres,
            settings=settings,
            qdrant=stores.qdrant,
            embedder=embedder,
        )
    except LLMUnavailableError as exc:  # the supervisor itself could not be reached
        raise model_unavailable(exc) from exc
    may_see_sql = caller.can("sql:view")
    return AssistantResponse(
        question=answer.question,
        status=answer.status,
        route=answer.route,
        reason=answer.reason,
        summary=answer.summary,
        clarification=answer.clarification,
        results=[
            PartOut(
                agent=p.agent,
                question=p.question,
                status=p.status,
                summary=p.summary,
                insights=p.insights,
                sql=p.sql if may_see_sql else None,
                columns=[ColumnOut(**c) for c in p.columns],
                data=p.data,
                row_count=p.row_count,
                truncated=p.truncated,
                assumptions=p.assumptions,
                sources=[SourceOut(**vars(source)) for source in p.sources],
            )
            for p in answer.results
        ],
        scope_note=answer.scope_note,
    )
