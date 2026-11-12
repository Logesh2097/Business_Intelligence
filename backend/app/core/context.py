"""Who is asking. Built once per request from the session, read-only afterwards.

It is what scopes every query to a company and its outlets. It is never taken from
a model's output and never placed in a prompt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

# the permissions this phase needs; the full table is docs/design/security.md §3
_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"chat:ask", "documents:read"}),
    "analyst": frozenset({"chat:ask", "sql:view", "documents:read"}),
    "admin": frozenset({"chat:ask", "sql:view", "documents:read", "documents:manage"}),
}


@dataclass(frozen=True)
class Caller:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    role: str
    timezone: str
    currency: str
    all_outlets: bool
    outlet_ids: tuple[uuid.UUID, ...] = ()
    outlet_names: tuple[str, ...] = ()

    def can(self, permission: str) -> bool:
        return permission in _PERMISSIONS.get(self.role, frozenset())

    @property
    def outlet_scope(self) -> str:
        """The value of `app.outlet_ids` for row security: '*' or a list of ids."""
        if self.all_outlets:
            return "*"
        # an empty list must match nothing, not everything
        return ",".join(str(o) for o in self.outlet_ids) or str(uuid.UUID(int=0))

    @property
    def scope_note(self) -> str | None:
        if self.all_outlets:
            return None
        return "Limited to your outlets: " + ", ".join(self.outlet_names) + "."
