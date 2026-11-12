"""POST /api/v1/analytics/query: a question in, a table and an explanation out (docs/api.md §5)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import CallerDep, LLMDep, SettingsDep, StoresDep
from app.core.errors import PermissionDeniedError, UnavailableError
from app.llm.base import LLMUnavailableError
from app.workflows.analytics import answer_question

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]


class QueryRequest(BaseModel):
    question: Question = Field(examples=["What was our total revenue last month?"])


class ColumnOut(BaseModel):
    name: str
    type: Literal["text", "number", "money", "percent", "date"]


class QueryResponse(BaseModel):
    question: str
    status: Literal["answered", "no_data", "unanswerable", "failed", "timeout"]
    summary: str
    sql: str | None = Field(description="The query that ran. Null unless the caller may see SQL")
    columns: list[ColumnOut]
    data: list[dict[str, Any]]
    row_count: int
    truncated: bool
    assumptions: list[str]
    scope_note: str | None


def model_unavailable(exc: LLMUnavailableError) -> UnavailableError:
    """The 503 for a model that is down or not set up. Shared with the agents' route."""
    reason = str(exc)  # the provider error's class name, or "not configured"; never its text
    if reason == "not configured":
        return UnavailableError("The assistant is not set up yet: no model key is configured.")
    # the provider's SDK calls it RateLimitError; LangChain's client, OpenAIRateLimitError
    if reason.endswith("RateLimitError"):
        return UnavailableError(
            "The assistant is busy right now: the model's request limit was reached. "
            "Please wait a minute and try again."
        )
    return UnavailableError("The assistant is not available right now. Please try again.")


@router.post("/query")
async def query(
    body: QueryRequest, caller: CallerDep, stores: StoresDep, llm: LLMDep, settings: SettingsDep
) -> QueryResponse:
    if not caller.can("chat:ask"):
        raise PermissionDeniedError()
    try:
        answer = await answer_question(
            question=body.question, caller=caller, llm=llm, pg=stores.postgres, settings=settings
        )
    except LLMUnavailableError as exc:
        raise model_unavailable(exc) from exc
    return QueryResponse(
        question=answer.question,
        status=answer.status,
        summary=answer.summary,
        sql=answer.sql if caller.can("sql:view") else None,
        columns=[ColumnOut(**c) for c in answer.columns],
        data=answer.data,
        row_count=answer.row_count,
        truncated=answer.truncated,
        assumptions=answer.assumptions,
        scope_note=answer.scope_note,
    )
