"""Users and their companies (schema `app`)."""

from __future__ import annotations

from app.core.context import Caller
from app.store.postgres import Postgres


async def find_caller_by_email(pg: Postgres, email: str) -> Caller | None:
    """The active user with this email, in an active company, with their outlets."""
    async with pg.session() as conn:
        row = await (
            await conn.execute(
                """
                SELECT u.id, u.role, u.all_outlets,
                       t.id AS tenant_id, t.name AS tenant_name, t.timezone, t.currency
                FROM app.users u
                JOIN app.tenants t ON t.id = u.tenant_id
                WHERE u.email = %s AND u.is_active AND t.is_active
                """,
                (email.strip().lower(),),
            )
        ).fetchone()
        if row is None:
            return None
        outlets: list[dict[str, object]] = []
        if not row["all_outlets"]:
            # biz.outlets is under row security: name the company to read its outlets
            await conn.execute(
                "SELECT set_config('app.tenant_id', %s, true), "
                "set_config('app.outlet_ids', '*', true)",
                (str(row["tenant_id"]),),
            )
            outlets = await (
                await conn.execute(
                    """
                    SELECT o.id, o.name
                    FROM app.user_outlets uo
                    JOIN biz.outlets o ON o.tenant_id = uo.tenant_id AND o.id = uo.outlet_id
                    WHERE uo.tenant_id = %s AND uo.user_id = %s
                    ORDER BY o.name
                    """,
                    (row["tenant_id"], row["id"]),
                )
            ).fetchall()
    return Caller(
        user_id=row["id"],
        tenant_id=row["tenant_id"],
        tenant_name=row["tenant_name"],
        role=row["role"],
        timezone=row["timezone"],
        currency=row["currency"],
        all_outlets=row["all_outlets"],
        outlet_ids=tuple(o["id"] for o in outlets),  # type: ignore[misc]
        outlet_names=tuple(str(o["name"]) for o in outlets),
    )


async def list_demo_users(pg: Postgres, limit: int = 50) -> list[dict[str, str]]:
    """Active users with their company, for the development sign-in list only."""
    async with pg.session() as conn:
        rows = await (
            await conn.execute(
                """
                SELECT u.email, u.role, t.name AS company
                FROM app.users u
                JOIN app.tenants t ON t.id = u.tenant_id
                WHERE u.is_active AND t.is_active
                ORDER BY t.name, u.role, u.email
                LIMIT %s
                """,
                (limit,),
            )
        ).fetchall()
    return [
        {"email": str(r["email"]), "role": str(r["role"]), "company": str(r["company"])}
        for r in rows
    ]
