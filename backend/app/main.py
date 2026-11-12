"""The API process: settings -> logging -> store connections -> error handlers -> routes
-> (the built web app, when present)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    agents,
    analytics,
    assistant,
    documents,
    health,
    session,
    system,
    tools,
)
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, request_logging_middleware
from app.llm import build_llm
from app.llm.embeddings import build_embedder
from app.store.connections import Stores
from app.store.files import FileStore

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_or_raise(process="api")
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Opening does not wait for the stores: the API starts and /readyz tells the truth.
        app.state.stores = await Stores.open(settings)
        app.state.llm = build_llm(settings)
        app.state.embedder = build_embedder(settings)
        app.state.files = FileStore(settings.upload_dir)
        try:
            yield
        finally:
            await app.state.stores.close()

    app = FastAPI(
        title="BI Assistant",
        lifespan=lifespan,
        version=settings.version,
        # the OpenAPI document and its viewers are for development only
        openapi_url="/api/v1/openapi.json" if settings.relaxed else None,
        docs_url="/api/v1/docs" if settings.relaxed else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.middleware("http")(request_logging_middleware)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(system.router)
    app.include_router(analytics.router)
    app.include_router(agents.router)
    app.include_router(assistant.router)
    app.include_router(documents.router)
    app.include_router(tools.router)
    app.include_router(session.router)

    if STATIC_DIR.is_dir():
        _serve_web_app(app, STATIC_DIR)
    return app


def _serve_web_app(app: FastAPI, static_dir: Path) -> None:
    """Serve the built Vue app from the same origin as the API (the cloud layout)."""
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    root = static_dir.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def web_app(path: str) -> FileResponse:
        # a plain function: FastAPI runs it in a worker thread, so file checks do not block
        if path.startswith("api/"):
            # an unknown API path is an error, not a page
            raise HTTPException(status_code=404)
        candidate = (root / path).resolve()
        if path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        # any other path is a page of the single-page app
        return FileResponse(index)


def app_factory() -> FastAPI:
    """Entry point for `uvicorn app.main:app_factory --factory`."""
    return create_app()
