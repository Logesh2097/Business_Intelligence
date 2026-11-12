"""/healthz and /readyz (docs/api.md §2). No sign-in; outside /api/v1."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.deps import SettingsDep, StoresDep
from app.store import health

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(settings: SettingsDep) -> dict[str, str]:
    """The process is alive. Touches nothing."""
    return {"status": "ok", "version": settings.version, "env": settings.env}


@router.get("/readyz")
async def readyz(stores: StoresDep) -> JSONResponse:
    """200 when every store answers, else 503 naming the ones that are down."""
    results = await health.run_all(stores)
    all_ok = all(result.ok for result in results.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={
            "status": "ok" if all_ok else "degraded",
            "checks": {name: result.as_dict() for name, result in results.items()},
        },
    )
