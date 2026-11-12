"""LIVE: the business tools against real PostgreSQL, in process, over the REST route,
and through a real MCP server process (`python -m app.mcp_server`).

Real: the fixed SQL of store/business.py, bi_readonly, row security, the query log,
the MCP SDK and its HTTP transport, the context token. No model is involved: these
tools do not use one. Figures are compared with values computed separately as the
database owner. "Today" is pinned to 2026-11-01, so last_month is October 2026.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from app.core.config import Settings
from app.core.context import Caller
from app.core.errors import PermissionDeniedError
from app.main import create_app
from app.store.connections import Stores
from app.tools.client import call_over_mcp
from app.tools.context import ToolContext
from app.tools.registry import TOOLS, ToolResult, call_tool
from tests.integration.test_schema_live import who

ANALYST = "analyst@brewbite.example"
VIEWER = "viewer@brewbite.example"  # limited to two outlets
URBAN_ADMIN = "admin@urbanmart.example"
OCTOBER = ("2026-10-01", "2026-10-31")
KEY = "live-test-context-key-" + "k" * 24


def ctx(stores: Stores, settings: Settings, caller: Caller) -> ToolContext:
    return ToolContext(caller=caller, pg=stores.postgres, settings=settings)


def number(value: Any) -> float:
    return round(float(value or 0), 2)


def owner_sales(owner: psycopg.Connection[Any], caller: Caller) -> dict[str, float]:
    outlets = "" if caller.all_outlets else "AND outlet_id = ANY(%s)"
    params: list[Any] = [caller.tenant_id, *OCTOBER]
    if not caller.all_outlets:
        params.append(list(caller.outlet_ids))
    row = owner.execute(
        "SELECT COALESCE(ROUND(SUM(total_amount - tax_amount), 2), 0) AS revenue, "  # noqa: S608
        "COUNT(*) AS orders FROM biz.orders WHERE tenant_id = %s AND status = 'completed' "
        f"AND order_date BETWEEN %s AND %s {outlets}",
        params,
    ).fetchone()
    assert row is not None
    return {"revenue": number(row["revenue"]), "orders": number(row["orders"])}


# -- in process ---------------------------------------------------------------------------


async def test_the_sales_summary_equals_the_owners_own_sum_for_each_caller(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    seen = []
    for email in (ANALYST, VIEWER, URBAN_ADMIN):
        caller = await who(seeded, email)
        result = await call_tool(ctx(seeded, seeded_settings, caller), "sales_summary", {})
        want = owner_sales(owner, caller)
        (row,) = result.rows
        assert number(row["revenue"]) == want["revenue"] and row["orders"] == want["orders"], email
        assert result.period == {"name": "last_month", "start": OCTOBER[0], "end": OCTOBER[1]}
        assert row["units_sold"] > 0 and row["average_order_value"] > 0
        seen.append(want["revenue"])
    analyst, viewer, urban = seen
    assert viewer < analyst and urban != analyst and min(seen) > 0  # three different scopes


async def test_a_limited_user_sees_only_their_outlets(
    seeded: Stores, seeded_settings: Settings
) -> None:
    viewer, analyst = await who(seeded, VIEWER), await who(seeded, ANALYST)
    limited = await call_tool(ctx(seeded, seeded_settings, viewer), "revenue_by_outlet", {})
    everyone = await call_tool(ctx(seeded, seeded_settings, analyst), "revenue_by_outlet", {})
    assert {row["outlet"] for row in limited.rows} == set(viewer.outlet_names)
    assert len(everyone.rows) > len(limited.rows)
    assert limited.scope_note and everyone.scope_note is None
    by_name = {row["outlet"]: row["revenue"] for row in everyone.rows}
    for row in limited.rows:
        assert row["revenue"] == by_name[row["outlet"]]  # the same figure, not a recomputed one
    stock = await call_tool(ctx(seeded, seeded_settings, viewer), "inventory_status", {})
    assert stock.rows and {row["outlet"] for row in stock.rows} <= set(viewer.outlet_names)


async def test_top_products_are_ranked_by_units_unless_revenue_is_asked(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    caller = await who(seeded, ANALYST)
    units = await call_tool(ctx(seeded, seeded_settings, caller), "top_products", {"limit": 5})
    want = owner.execute(
        "SELECT p.name, SUM(i.quantity) AS units FROM biz.order_items i "
        "JOIN biz.orders o ON o.tenant_id = i.tenant_id AND o.id = i.order_id "
        "JOIN biz.products p ON p.tenant_id = i.tenant_id AND p.id = i.product_id "
        "WHERE i.tenant_id = %s AND o.status = 'completed' AND i.order_date BETWEEN %s AND %s "
        "GROUP BY p.name ORDER BY units DESC, SUM(i.line_total) DESC, p.name LIMIT 5",
        (caller.tenant_id, *OCTOBER),
    ).fetchall()
    assert [(r["product"], number(r["units_sold"])) for r in units.rows] == [
        (r["name"], number(r["units"])) for r in want
    ]
    revenue = await call_tool(
        ctx(seeded, seeded_settings, caller), "top_products", {"limit": 5, "by": "revenue"}
    )
    figures = [row["revenue"] for row in revenue.rows]
    assert figures == sorted(figures, reverse=True)
    assert "units sold" in units.notes[0] and "revenue" in revenue.notes[0]


async def test_inventory_customers_products_and_the_summary_answer(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    caller = await who(seeded, ANALYST)
    tools = ctx(seeded, seeded_settings, caller)

    low = await call_tool(tools, "inventory_status", {"limit": 200})
    assert low.rows and all(
        row["low_stock"] and row["on_hand"] <= row["reorder_level"] for row in low.rows
    )
    everything = await call_tool(tools, "inventory_status", {"only_low": False, "limit": 200})
    assert everything.row_count > low.row_count

    customers = await call_tool(tools, "customer_insights", {})
    want = owner.execute(
        "SELECT COUNT(*) AS visits, COUNT(DISTINCT customer_id) AS visitors "
        "FROM biz.customer_visits WHERE tenant_id = %s AND visit_date BETWEEN %s AND %s",
        (caller.tenant_id, *OCTOBER),
    ).fetchone()
    assert want is not None
    (row,) = customers.rows
    assert (row["visits"], row["visiting_customers"]) == (want["visits"], want["visitors"])

    found = await call_tool(tools, "product_information", {"name": "capp"})
    assert found.rows and all("capp" in row["product"].lower() for row in found.rows)
    assert set(found.rows[0]) == {"product", "sku", "category", "unit", "unit_price", "active"}
    # a wildcard typed by the user is a character, not a pattern: it does not list everything
    assert (await call_tool(tools, "product_information", {"name": "%%"})).rows == []
    assert (await call_tool(tools, "product_information", {"name": "__"})).rows == []

    summary = await call_tool(tools, "business_summary", {})
    (row,) = summary.rows
    assert {
        "revenue",
        "orders",
        "visits",
        "expenses",
        "purchases_received_amount",
        "low_stock_lines",
    } <= set(row)
    sales = (await call_tool(tools, "sales_summary", {})).rows[0]
    assert row["revenue"] == sales["revenue"]  # one definition, two tools


async def test_every_period_name_is_accepted_and_gives_dates(
    seeded: Stores, seeded_settings: Settings
) -> None:
    caller = await who(seeded, ANALYST)
    periods = TOOLS["sales_summary"].input.model_json_schema()["properties"]["period"]["enum"]
    for period in periods:
        result = await call_tool(
            ctx(seeded, seeded_settings, caller), "sales_trend", {"period": period}
        )
        assert result.period is not None and result.period["start"] <= result.period["end"], period


async def test_run_query_runs_under_row_security_and_is_logged(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    viewer_scope = await who(seeded, VIEWER)
    analyst = await who(seeded, ANALYST)
    sql = "SELECT COUNT(*) AS outlets FROM outlets"
    everything = await call_tool(ctx(seeded, seeded_settings, analyst), "run_query", {"sql": sql})
    assert everything.status == "ok" and everything.rows[0]["outlets"] > len(
        viewer_scope.outlet_ids
    )

    refused = await call_tool(
        ctx(seeded, seeded_settings, analyst), "run_query", {"sql": "SELECT * FROM app.users"}
    )
    assert refused.status == "refused" and refused.rows == []
    stolen = await call_tool(
        ctx(seeded, seeded_settings, analyst), "run_query",
        {"sql": "UPDATE orders SET status = 'cancelled'"},
    )  # fmt: skip
    assert stolen.status == "refused"

    logged = owner.execute(
        "SELECT status FROM app.query_log WHERE tenant_id = %s AND question = '[tool] run_query' "
        "ORDER BY created_at DESC LIMIT 3",
        (analyst.tenant_id,),
    ).fetchall()
    assert [row["status"] for row in logged] == ["rejected", "rejected", "ok"]


# -- the REST route -----------------------------------------------------------------------


@pytest.fixture
def client(seeded_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(seeded_settings)) as started:
        yield started


def test_the_api_serves_the_tools_and_the_activity_of_the_callers_company(
    client: TestClient, owner: psycopg.Connection[Any]
) -> None:
    as_analyst, as_urban = {"X-Dev-User": ANALYST}, {"X-Dev-User": URBAN_ADMIN}
    body = client.post("/api/v1/tools/sales_summary", json={}, headers=as_analyst).json()
    assert body["status"] == "ok" and body["rows"][0]["revenue"] > 0
    other = client.post("/api/v1/tools/sales_summary", json={}, headers=as_urban).json()
    assert other["rows"][0]["revenue"] != body["rows"][0]["revenue"]

    marker = "SELECT COUNT(*) AS products_for_activity_test FROM products"
    ran = client.post(
        "/api/v1/tools/run_query", json={"arguments": {"sql": marker}}, headers=as_analyst
    )
    assert ran.status_code == 200 and ran.json()["status"] == "ok"
    activity = client.get("/api/v1/activity?limit=20", headers=as_analyst).json()
    assert any("products_for_activity_test" in (e["sql"] or "") for e in activity["entries"])
    assert activity["last_24_hours"].get("ok", 0) >= 1
    assert all(e["asked_by"].endswith("@brewbite.example") for e in activity["entries"])
    theirs = client.get("/api/v1/activity?limit=200", headers=as_urban).json()
    assert not any("products_for_activity_test" in (e["sql"] or "") for e in theirs["entries"])
    assert client.get("/api/v1/activity", headers={"X-Dev-User": VIEWER}).status_code == 403


# -- a real MCP server process ------------------------------------------------------------


def _url(dsn: str) -> str:
    """A key=value connection string as the postgresql:// address the settings require."""
    info = conninfo_to_dict(dsn)
    login = quote(str(info["user"]), safe="")
    if info.get("password"):
        login += ":" + quote(str(info["password"]), safe="")
    host, port = info.get("host", "localhost"), info.get("port", 5432)
    return f"postgresql://{login}@{host}:{port}/{info['dbname']}"


@pytest.fixture(scope="module")
def mcp_process(seeded_settings: Settings) -> Iterator[Settings]:
    """`python -m app.mcp_server` on a free port, on the seeded database."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {
        **os.environ,
        "BI_ENV": "test",
        "BI_PG_DSN": _url(seeded_settings.pg_dsn),
        "BI_PG_READONLY_DSN": _url(seeded_settings.pg_readonly_dsn),
        "BI_PG_OWNER_DSN": "",
        "BI_SECRET_KEY": KEY,
        "BI_MCP_HOST": "127.0.0.1",
        "BI_MCP_PORT": str(port),
        "BI_FAKE_TODAY": str(seeded_settings.fake_today or ""),
        "BI_OPENAI_API_KEY": "",  # no model and no embeddings in this process
        "BI_EVAL_CONFIRM_REAL_MODEL": "",
    }
    process = subprocess.Popen(  # noqa: S603 - our own interpreter and module
        [sys.executable, "-m", "app.mcp_server"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                detail = (process.stderr.read() if process.stderr else b"")[-500:]
                pytest.fail(f"the MCP server did not start: {detail!r}")
            time.sleep(0.1)
        yield seeded_settings.model_copy(
            update={"secret_key": KEY, "mcp_url": f"http://127.0.0.1:{port}/mcp",
                    "tools_transport": "mcp"}
        )  # fmt: skip
    finally:
        process.terminate()
        process.wait(timeout=10)


async def over_mcp(seeded: Stores, settings: Settings, email: str, name: str,
                   arguments: dict[str, Any] | None = None) -> ToolResult:  # fmt: skip
    caller = await who(seeded, email)
    return await call_over_mcp(ctx(seeded, settings, caller), name, arguments or {})


async def test_every_business_tool_gives_the_same_result_over_mcp_as_in_process(
    seeded: Stores, seeded_settings: Settings, mcp_process: Settings
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = [
        ("sales_summary", {}), ("sales_trend", {"period": "last_7_days"}),
        ("revenue_by_outlet", {}), ("top_products", {"limit": 5}),
        ("inventory_status", {"limit": 20}), ("customer_insights", {}),
        ("visits_by_segment", {}), ("product_information", {"name": "latte"}),
        ("business_summary", {}), ("list_tables", {}),
        ("run_query", {"sql": "SELECT COUNT(*) AS products FROM products"}),
    ]  # fmt: skip
    for email in (ANALYST, URBAN_ADMIN):
        caller = await who(seeded, email)
        for name, arguments in calls:
            direct = await call_tool(ctx(seeded, seeded_settings, caller), name, arguments)
            through = await over_mcp(seeded, mcp_process, email, name, arguments)
            assert through == direct, (email, name)
            assert direct.status == "ok", (email, name)
            if name != "product_information":  # the second company sells no latte
                assert direct.row_count >= 1, (email, name)


async def test_the_company_and_outlets_in_the_token_are_what_the_server_applies(
    seeded: Stores, mcp_process: Settings, owner: psycopg.Connection[Any]
) -> None:
    seen = {}
    for email in (ANALYST, VIEWER, URBAN_ADMIN):
        caller = await who(seeded, email)
        result = await over_mcp(seeded, mcp_process, email, "sales_summary")
        assert number(result.rows[0]["revenue"]) == owner_sales(owner, caller)["revenue"], email
        seen[email] = result.rows[0]["revenue"]
    assert seen[VIEWER] < seen[ANALYST] and seen[URBAN_ADMIN] != seen[ANALYST]
    limited = await over_mcp(seeded, mcp_process, VIEWER, "revenue_by_outlet")
    assert {row["outlet"] for row in limited.rows} == set((await who(seeded, VIEWER)).outlet_names)
    # the pipeline may run a query for a viewer (that is how a viewer's question is
    # answered); row security still limits it to the viewer's outlets
    counted = await over_mcp(
        seeded, mcp_process, VIEWER, "run_query", {"sql": "SELECT COUNT(*) AS outlets FROM outlets"}
    )
    assert counted.rows == [{"outlets": len((await who(seeded, VIEWER)).outlet_ids)}]
    # typed by the viewer through the API's own doorway, the same tool is refused
    with pytest.raises(PermissionDeniedError):
        await call_tool(
            ctx(seeded, mcp_process, await who(seeded, VIEWER)), "run_query", {"sql": "SELECT 1"}
        )


def test_the_server_process_refuses_a_call_without_a_valid_token(mcp_process: Settings) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream"}
    assert httpx.post(mcp_process.mcp_url, json=body, headers=headers).status_code == 401
    forged = {**headers, "Authorization": "Bearer abc.def"}
    assert httpx.post(mcp_process.mcp_url, json=body, headers=forged).status_code == 401


async def test_the_added_time_of_a_call_over_mcp_is_small(
    seeded: Stores, seeded_settings: Settings, mcp_process: Settings
) -> None:
    caller = await who(seeded, ANALYST)
    await over_mcp(seeded, mcp_process, ANALYST, "list_tables")  # warm up

    async def timed(call: Any) -> float:
        started = time.perf_counter()
        for _ in range(5):
            await call()
        return (time.perf_counter() - started) / 5 * 1000

    direct = await timed(
        lambda: call_tool(ctx(seeded, seeded_settings, caller), "sales_summary", {})
    )
    through = await timed(lambda: over_mcp(seeded, mcp_process, ANALYST, "sales_summary"))
    print(f"\nsales_summary: direct {direct:.1f} ms, over MCP {through:.1f} ms")
    assert through < direct + 1000  # a loose bound; the measured figure goes in plan.md
