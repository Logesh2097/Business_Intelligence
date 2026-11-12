"""What every tool is given: who is asking, and the clients it may use.

Built by our code for one request: from the session on the direct path, from a
verified context token on the MCP path. A model never fills any of it in.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.context import Caller
from app.llm.embeddings import Embedder
from app.store.postgres import Postgres
from app.store.qdrant import Qdrant


@dataclass(frozen=True)
class ToolContext:
    caller: Caller
    pg: Postgres
    settings: Settings
    qdrant: Qdrant | None = None
    embedder: Embedder | None = None
    # True only on the MCP server, whose callers hold a context token. Tokens are made
    # by the API for its own agent pipeline and never leave it, so a call there is the
    # pipeline working for the user, not the user typing a tool call.
    pipeline: bool = False
