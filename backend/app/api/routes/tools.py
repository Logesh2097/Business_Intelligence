"""/api/v1/tools and /api/v1/activity (docs/api.md §10).

The tools are the same ones the MCP server exposes (app/tools/registry.py); this is
the in-process doorway the web app uses for its dashboard and analytics pages.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.deps import CallerDep, EmbedderDep, SettingsDep, StoresDep
from app.core.errors import PermissionDeniedError
from app.store import activity
from app.tools.context import ToolContext
from app.tools.registry import ToolResult, call_tool, visible_to

router = APIRouter(prefix="/api/v1", tags=["tools"])


class ToolOut(BaseModel):
    name: str
    title: str
    description: str
    group: str = Field(description="business, catalog, query or documents")
    input_schema: dict[str, Any] = Field(description="JSON Schema of the arguments")


class ToolList(BaseModel):
    tools: list[ToolOut]
    transport: str = Field(description="How the agents reach tools: direct or mcp")


class ToolCall(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class ActivityEntry(BaseModel):
    id: str
    created_at: dt.datetime
    asked_by: str
    question: str
    status: str = Field(description="ok, rejected, error or timeout")
    reason: str | None = Field(description="The refusal code or the database error class")
    row_count: int | None
    duration_ms: int | None
    sql: str | None


class ActivityOut(BaseModel):
    entries: list[ActivityEntry]
    last_24_hours: dict[str, int] = Field(description="How many attempts ended in each status")


def _context(caller: CallerDep, stores: StoresDep, settings: SettingsDep,
             embedder: EmbedderDep) -> ToolContext:  # fmt: skip
    return ToolContext(
        caller=caller, pg=stores.postgres, settings=settings, qdrant=stores.qdrant,
        embedder=embedder,
    )  # fmt: skip


@router.get("/tools")
async def list_tools(
    caller: CallerDep, stores: StoresDep, settings: SettingsDep, embedder: EmbedderDep
) -> ToolList:
    ctx = _context(caller, stores, settings, embedder)
    return ToolList(
        tools=[
            ToolOut(
                name=spec.name,
                title=spec.title,
                description=spec.description,
                group=spec.group,
                input_schema=spec.input.model_json_schema(),
            )
            for spec in visible_to(ctx)
        ],
        transport=settings.tools_transport,
    )


@router.post("/tools/{name}")
async def run_tool(
    name: str,
    body: ToolCall,
    caller: CallerDep,
    stores: StoresDep,
    settings: SettingsDep,
    embedder: EmbedderDep,
) -> ToolResult:
    return await call_tool(_context(caller, stores, settings, embedder), name, body.arguments)


@router.get("/activity")
async def recent_activity(
    caller: CallerDep,
    stores: StoresDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ActivityOut:
    """The company's recent AI-written queries. For those who may see SQL."""
    if not caller.can("sql:view"):
        raise PermissionDeniedError()
    entries = await activity.recent(stores.postgres, tenant_id=caller.tenant_id, limit=limit)
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
    return ActivityOut(
        entries=[ActivityEntry(**vars(entry)) for entry in entries],
        last_24_hours=await activity.counts(
            stores.postgres, tenant_id=caller.tenant_id, since=since
        ),
    )
