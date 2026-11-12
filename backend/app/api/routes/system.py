"""/api/v1/system: what the API can say about its own stores (docs/api.md §2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import SettingsDep, StoresDep
from app.core.errors import NotFoundError
from app.store import status

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/databases")
async def databases(settings: SettingsDep, stores: StoresDep) -> dict[str, Any]:
    """Connection status of PostgreSQL, MongoDB and Qdrant. Always 200: it is a report.

    Development and test only for now. It shows server versions and pool sizes, which
    an outsider has no business seeing; it opens to admins when sign-in exists.
    """
    if not settings.relaxed:
        raise NotFoundError()
    return await status.database_status(stores)
