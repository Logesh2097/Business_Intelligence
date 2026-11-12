"""`python -m app.mcp_server`: the bi-mcp process."""

from __future__ import annotations

import asyncio

import uvicorn

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.llm.embeddings import build_embedder
from app.mcp_server.server import McpState, create_mcp_app
from app.store.postgres import Postgres
from app.store.qdrant import Qdrant


async def serve() -> None:
    settings = get_settings()
    settings.validate_or_raise(process="mcp")
    configure_logging()
    pg = Postgres(settings)
    await pg.open()
    try:
        state = McpState(
            settings=settings, pg=pg, qdrant=Qdrant(settings), embedder=build_embedder(settings)
        )
        config = uvicorn.Config(
            create_mcp_app(state),
            host=settings.mcp_host,
            port=settings.mcp_port,
            log_config=None,
            access_log=False,
        )
        await uvicorn.Server(config).serve()
    finally:
        await pg.close()


if __name__ == "__main__":
    asyncio.run(serve())
