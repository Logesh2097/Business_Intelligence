"""LIVE: the schema's own protections, tested on the real database WITHOUT the guard.

Each lock on AI-written SQL must hold alone (docs/design/security.md §5). These tests
talk to PostgreSQL as bi_readonly and try the things the guard would have refused.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from app.core.context import Caller
from app.seed.generate import uid
from app.semantic.model import load_vocabulary
from app.store import users
from app.store.connections import Stores

BREW = uid("tenant", "brewbite")
URBAN = uid("tenant", "urbanmart")
BIZ_TABLES = sorted(t.table for t in load_vocabulary().tables)


async def who(stores: Stores, email: str) -> Caller:
    caller = await users.find_caller_by_email(stores.postgres, email)
    assert caller is not None
    return caller


async def rows(stores: Stores, caller: Caller | None, sql: str) -> list[dict[str, Any]]:
    """Run raw SQL as bi_readonly: no guard in the way."""
    session = (
        stores.postgres.tenant_session(caller) if caller else stores.postgres.readonly_session()
    )
    async with session as conn:
        return await (await conn.execute(sql)).fetchall()  # type: ignore[arg-type]


# -- the schema is what the design says ---------------------------------------------


def test_every_business_table_has_forced_row_security(owner: psycopg.Connection[Any]) -> None:
    found = owner.execute(
        "SELECT c.relname AS name, c.relrowsecurity AS enabled, c.relforcerowsecurity AS forced "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'biz' AND c.relkind = 'r' ORDER BY 1"
    ).fetchall()
    assert [t["name"] for t in found] == BIZ_TABLES  # a new table cannot be forgotten
    assert all(t["enabled"] and t["forced"] for t in found)


def test_the_vocabulary_lists_exactly_the_columns_the_readonly_user_may_select(
    owner: psycopg.Connection[Any],
) -> None:
    granted: dict[str, set[str]] = {}
    for row in owner.execute(
        "SELECT table_name, column_name FROM information_schema.column_privileges "
        "WHERE grantee = 'bi_readonly' AND privilege_type = 'SELECT' AND table_schema = 'biz'"
    ):
        granted.setdefault(row["table_name"], set()).add(row["column_name"])
    described = {t: set(columns) for t, columns in load_vocabulary().catalog().items()}
    # tenant_id is granted (row security needs it) but is never described to the model
    assert {t: cols - {"tenant_id"} for t, cols in granted.items()} == described


def test_the_readonly_user_has_no_privilege_but_select(owner: psycopg.Connection[Any]) -> None:
    others = owner.execute(
        "SELECT DISTINCT privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee = 'bi_readonly' UNION "
        "SELECT DISTINCT privilege_type FROM information_schema.column_privileges "
        "WHERE grantee = 'bi_readonly'"
    ).fetchall()
    assert {r["privilege_type"] for r in others} == {"SELECT"}
    in_app = owner.execute(
        "SELECT count(*) AS n FROM information_schema.role_table_grants "
        "WHERE grantee = 'bi_readonly' AND table_schema <> 'biz'"
    ).fetchone()
    assert in_app == {"n": 0}


# -- tenant isolation, by the database alone ----------------------------------------


@pytest.mark.parametrize("table", BIZ_TABLES)
async def test_a_company_sees_only_its_own_rows_in_every_table(
    seeded: Stores, owner: psycopg.Connection[Any], table: str
) -> None:
    brew = await who(seeded, "admin@brewbite.example")
    seen = await rows(seeded, brew, f"SELECT DISTINCT tenant_id FROM {table}")
    assert [r["tenant_id"] for r in seen] == [BREW]
    expected = owner.execute(
        f"SELECT count(*) AS n FROM biz.{table} WHERE tenant_id = %s", (BREW,)
    ).fetchone()
    counted = await rows(seeded, brew, f"SELECT count(*) AS n FROM {table}")
    assert counted == [expected] and expected["n"] > 0  # type: ignore[index]


@pytest.mark.parametrize("table", BIZ_TABLES)
async def test_with_no_company_set_every_table_is_empty(seeded: Stores, table: str) -> None:
    assert await rows(seeded, None, f"SELECT count(*) AS n FROM {table}") == [{"n": 0}]


async def test_asking_for_another_company_by_id_returns_nothing(seeded: Stores) -> None:
    brew = await who(seeded, "admin@brewbite.example")
    sql = f"SELECT count(*) AS n FROM orders WHERE tenant_id = '{URBAN}'"
    assert await rows(seeded, brew, sql) == [{"n": 0}]


async def test_the_company_cannot_be_changed_from_inside_a_query(seeded: Stores) -> None:
    brew = await who(seeded, "admin@brewbite.example")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await rows(seeded, brew, f"SELECT set_config('app.tenant_id', '{URBAN}', true)")
    # and the caller's own scope is intact afterwards
    seen = await rows(seeded, brew, "SELECT DISTINCT tenant_id FROM outlets")
    assert [r["tenant_id"] for r in seen] == [BREW]


async def test_a_pooled_connection_does_not_remember_the_last_company(seeded: Stores) -> None:
    brew = await who(seeded, "admin@brewbite.example")
    urban = await who(seeded, "admin@urbanmart.example")
    for _ in range(3):  # the same connection is reused from the pool
        a = await rows(seeded, brew, "SELECT DISTINCT tenant_id FROM orders")
        b = await rows(seeded, urban, "SELECT DISTINCT tenant_id FROM orders")
        c = await rows(seeded, None, "SELECT count(*) AS n FROM orders")
        assert [r["tenant_id"] for r in a] == [BREW]
        assert [r["tenant_id"] for r in b] == [URBAN]
        assert c == [{"n": 0}]


async def test_a_user_limited_to_some_outlets_sees_only_those(
    seeded: Stores, owner: psycopg.Connection[Any]
) -> None:
    viewer = await who(seeded, "viewer@brewbite.example")
    assert viewer.outlet_names == ("Indiranagar", "Koramangala")
    expected = owner.execute(
        "SELECT count(*) AS orders, sum(total_amount) AS total FROM biz.orders "
        "WHERE tenant_id = %s AND outlet_id = ANY(%s)",
        (BREW, list(viewer.outlet_ids)),
    ).fetchone()
    seen = await rows(
        seeded, viewer, "SELECT count(*) AS orders, sum(total_amount) AS total FROM orders"
    )
    assert seen == [expected]
    outlets = await rows(seeded, viewer, "SELECT name FROM outlets ORDER BY name")
    assert [o["name"] for o in outlets] == ["Indiranagar", "Koramangala"]
    for table in ("order_items", "customer_visits", "inventory_levels", "expenses"):
        ids = await rows(seeded, viewer, f"SELECT DISTINCT outlet_id FROM {table}")
        assert {r["outlet_id"] for r in ids} == set(viewer.outlet_ids), table


# -- the other locks, each alone -------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE orders SET status = 'cancelled'",
        "INSERT INTO categories (tenant_id, name) VALUES (gen_random_uuid(), 'x')",
        "TRUNCATE orders",
        "DROP TABLE orders",
        "CREATE TABLE biz.stolen (id int)",
        "CREATE TEMP TABLE stolen AS SELECT id FROM orders",
    ],
)
async def test_the_readonly_user_cannot_write_anything(seeded: Stores, sql: str) -> None:
    brew = await who(seeded, "admin@brewbite.example")
    with pytest.raises(
        (psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)
    ):
        await rows(seeded, brew, sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT email FROM app.users",
        "SELECT name FROM app.tenants",
        "SELECT question FROM app.query_log",
        "SELECT version FROM public.schema_migrations",
    ],
)
async def test_the_readonly_user_cannot_read_outside_biz(seeded: Stores, sql: str) -> None:
    brew = await who(seeded, "admin@brewbite.example")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await rows(seeded, brew, sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM customers",
        "SELECT phone FROM customers",
        "SELECT email FROM customers",
        "SELECT * FROM customers",
        "SELECT id FROM customers WHERE email LIKE '%@%'",
        "SELECT contact_name FROM suppliers",
        "SELECT phone FROM suppliers",
    ],
)
async def test_the_readonly_user_cannot_read_a_private_column(seeded: Stores, sql: str) -> None:
    brew = await who(seeded, "admin@brewbite.example")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await rows(seeded, brew, sql)


async def test_customers_can_still_be_counted_and_grouped(seeded: Stores) -> None:
    brew = await who(seeded, "admin@brewbite.example")
    segments = await rows(seeded, brew, "SELECT segment, count(*) AS n FROM customers GROUP BY 1")
    assert sum(s["n"] for s in segments) == 300


async def test_a_row_cannot_point_at_another_companys_row(owner: psycopg.Connection[Any]) -> None:
    brew_product = uid("brewbite", "product", "Latte")
    urban_outlet = uid("urbanmart", "outlet", "PUN-KOT")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        owner.execute(
            "INSERT INTO biz.inventory_levels (tenant_id, outlet_id, product_id, "
            "quantity_on_hand, reorder_level) VALUES (%s, %s, %s, 1, 1)",
            (URBAN, urban_outlet, brew_product),  # UrbanMart stock of a Brew & Bite product
        )
    owner.rollback()
