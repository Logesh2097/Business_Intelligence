"""/api/v1/me and the development sign-in list (docs/api.md §3).

SIGN-IN IS NOT BUILT. `/me` says who the request is from, which today is whoever the
`X-Dev-User` header names, in development and test only (api/deps.py). The list of
demo users exists so that the web app can offer that choice; outside development and
test it does not exist (404), exactly as the header is refused there.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CallerDep, SettingsDep, StoresDep
from app.core.errors import NotFoundError
from app.store import users

router = APIRouter(prefix="/api/v1", tags=["session"])

# what the web app shows or hides; the server checks every call regardless
_SHOWN = ("chat:ask", "sql:view", "documents:read", "documents:manage")


class Me(BaseModel):
    role: str
    company: str
    currency: str
    timezone: str
    all_outlets: bool
    outlets: list[str] = Field(description="The outlets a limited user may see; empty for all")
    permissions: list[str]
    scope_note: str | None
    sign_in: str = Field(description="How this caller was identified: 'development header'")


class DemoUser(BaseModel):
    email: str
    role: str
    company: str


@router.get("/me")
async def me(caller: CallerDep) -> Me:
    return Me(
        role=caller.role,
        company=caller.tenant_name,
        currency=caller.currency,
        timezone=caller.timezone,
        all_outlets=caller.all_outlets,
        outlets=list(caller.outlet_names),
        permissions=[p for p in _SHOWN if caller.can(p)],
        scope_note=caller.scope_note,
        sign_in="development header",
    )


@router.get("/dev/users")
async def demo_users(settings: SettingsDep, stores: StoresDep) -> list[DemoUser]:
    if not settings.relaxed:
        raise NotFoundError()
    return [DemoUser(**user) for user in await users.list_demo_users(stores.postgres)]
