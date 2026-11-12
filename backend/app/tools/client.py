"""Calling a tool: in this process, or through the MCP server (`BI_TOOLS_TRANSPORT`).

`direct` (the default) calls `registry.call_tool` here. `mcp` makes a two-minute
context token for the caller and calls the same tool on the MCP server; the result has
the same shape. The tool code is the same on both paths.

What is switched: `search_documents` (the document agent) and `run_query` (the SQL
analyst, and so every domain agent and the general route).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

from app.core import context_token
from app.core.errors import AppError, UnavailableError
from app.rag.retrieve import Passage
from app.tools.context import ToolContext
from app.tools.registry import ToolResult, call_tool

log = logging.getLogger("bi.tools")

MCP_DOWN = "The tool server is not available right now. Please try again."
TIMEOUT_SECONDS = 30.0
_FAILURE = re.compile(r"(?:^|: )([a-z][a-z_]{2,40}): (.+)$", re.DOTALL)


class ToolRefused(AppError):
    """The MCP server refused or failed the call. `code` is the tool's own error code."""

    status = 502


async def call_over_mcp(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolResult:
    token = context_token.make(ctx.caller, ctx.settings.secret_key)
    try:
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_SECONDS
        ) as http:
            transport = streamable_http_client(ctx.settings.mcp_url, http_client=http)
            async with Client(transport, read_timeout_seconds=TIMEOUT_SECONDS) as client:
                result = await client.call_tool(name, arguments)
    except Exception as failure:  # noqa: BLE001 - any transport failure is "the server is down"
        log.warning("mcp call failed", extra={"tool": name, "kind": type(failure).__name__})
        raise UnavailableError(MCP_DOWN) from failure

    if result.is_error:
        text = " ".join(getattr(part, "text", "") for part in result.content).strip()
        # our server words a failure as "<code>: <message>"; the SDK may put its own
        # "Error executing tool <name>: " in front
        found = _FAILURE.search(text)
        if found:
            raise ToolRefused(found.group(2), code=found.group(1))
        raise ToolRefused("The tool could not answer.", code="tool_failed")
    data = result.structured_content
    if data is None:  # a server that sent text only
        data = json.loads("".join(getattr(part, "text", "") for part in result.content))
    return ToolResult.model_validate(data)


async def use_tool(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolResult:
    """One tool call, by the configured transport."""
    if ctx.settings.tools_transport == "mcp":
        return await call_over_mcp(ctx, name, arguments)
    return await call_tool(ctx, name, arguments)


async def search_documents(ctx: ToolContext, query: str, k: int = 8) -> list[Passage]:
    """What the document agent calls: the search_documents tool, by either transport."""
    result = await use_tool(ctx, "search_documents", {"query": query[:2000], "k": k})
    return [
        Passage(
            document_id=str(row["document_id"]),
            filename=str(row["filename"]),
            title=str(row["title"]),
            page=row["page"],
            chunk_index=int(row["chunk_index"]),
            text=str(row["text"]),
            score=float(row["score"]),
        )
        for row in result.rows
    ]
