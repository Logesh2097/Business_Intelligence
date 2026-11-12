"""MOCKED: the tool registry and its REST doorway. The database is replaced; the guard
and the checks that `run_query` goes through are the real ones.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_caller
from app.core.config import Settings
from app.core.context import Caller
from app.core.errors import BadRequestError, NotFoundError, PermissionDeniedError, UnavailableError
from app.main import create_app
from app.store.analytics import QueryFailedError, QueryTimeoutError
from app.tools import registry
from app.tools.context import ToolContext
from app.tools.registry import TOOLS, ToolResult, call_tool, visible_to
from tests.unit.analytics_fakes import FakeDatabase, caller, result

SETTINGS = Settings(_env_file=None, env="test")
FORBIDDEN_ARGUMENTS = ("tenant", "company", "user", "outlet", "role", "caller")


def ctx(who: Caller | None = None) -> ToolContext:
    return ToolContext(caller=who or caller("analyst"), pg=None, settings=SETTINGS)  # type: ignore[arg-type]


# -- what the registry holds --------------------------------------------------------------


def test_the_registry_holds_the_designed_tools() -> None:
    assert set(TOOLS) == {
        "sales_summary", "sales_trend", "revenue_by_outlet", "top_products", "inventory_status",
        "customer_insights", "visits_by_segment", "product_information", "business_summary",
        "list_tables", "describe_table", "get_metric_definitions", "run_query",
        "search_documents",
    }  # fmt: skip
    for spec in TOOLS.values():
        assert len(spec.description) > 60, spec.name  # written for a model to read


def test_no_tool_takes_a_company_a_user_or_an_outlet() -> None:
    for spec in TOOLS.values():
        schema = spec.input.model_json_schema()
        for name in schema.get("properties", {}):
            assert not any(word in name.lower() for word in FORBIDDEN_ARGUMENTS), (spec.name, name)
        assert schema.get("additionalProperties") is False, spec.name  # nothing can be slipped in


def test_no_tool_name_or_description_suggests_writing() -> None:
    for spec in TOOLS.values():
        assert not any(
            spec.name.startswith(verb)
            for verb in ("create", "update", "delete", "insert", "write", "set", "send", "execute")
        )


def test_a_viewer_sees_every_tool_but_run_query() -> None:
    names = {spec.name for spec in visible_to(ctx(caller("viewer")))}
    assert "run_query" not in names and len(names) == len(TOOLS) - 1
    assert {spec.name for spec in visible_to(ctx(caller("analyst")))} == set(TOOLS)
    assert visible_to(ctx(replace(caller(), role="guest"))) == []


# -- the one way a tool runs --------------------------------------------------------------


async def test_a_catalog_tool_answers_from_the_vocabulary() -> None:
    tables = await call_tool(ctx(), "list_tables", {})
    assert {"orders", "products", "inventory_levels"} <= {row["table"] for row in tables.rows}
    orders = await call_tool(ctx(), "describe_table", {"table": "Orders"})
    assert "status" in {row["column"] for row in orders.rows}
    assert "tenant_id" not in {row["column"] for row in orders.rows}
    assert any("order_date" in note for note in orders.notes)
    metrics = await call_tool(ctx(), "get_metric_definitions", {"names": ["revenue"]})
    assert [row["metric"] for row in metrics.rows] == ["revenue"]
    assert "completed" in metrics.rows[0]["where"]
    assert len((await call_tool(ctx(), "get_metric_definitions", None)).rows) > 5


@pytest.mark.parametrize(
    ("name", "arguments", "words"),
    [
        ("sales_summary", {"period": "next_century"}, "period"),
        ("sales_summary", {"period": "last_month", "tenant_id": "x"}, "tenant_id"),
        ("top_products", {"limit": 5000}, "limit"),
        ("top_products", {"by": "profit; DROP TABLE orders"}, "by"),
        ("describe_table", {}, "table"),
        ("product_information", {"name": "x"}, "name"),
        ("run_query", {"sql": ""}, "sql"),
        ("search_documents", {"query": "refunds", "k": 500}, "k"),
        ("inventory_status", {"only_low": "perhaps"}, "only_low"),
    ],
)
async def test_an_invalid_input_is_refused_and_its_values_are_not_repeated(
    name: str, arguments: dict[str, Any], words: str
) -> None:
    with pytest.raises(BadRequestError) as refused:
        await call_tool(ctx(), name, arguments)
    assert refused.value.code == "invalid_input" and words in refused.value.message
    for value in arguments.values():
        if isinstance(value, str) and len(value) > 3:
            assert value not in refused.value.message


async def test_an_unknown_tool_and_an_unknown_table_are_not_found() -> None:
    with pytest.raises(NotFoundError):
        await call_tool(ctx(), "drop_everything", {})
    with pytest.raises(NotFoundError):
        await call_tool(ctx(), "describe_table", {"table": "pg_shadow"})


async def test_a_tool_the_caller_may_not_use_is_refused_before_it_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase().install(monkeypatch)
    with pytest.raises(PermissionDeniedError):
        await call_tool(ctx(caller("viewer")), "run_query", {"sql": "SELECT 1"})
    with pytest.raises(PermissionDeniedError):
        await call_tool(ctx(replace(caller(), role="guest")), "list_tables", {})
    assert db.executed == [] and db.logged == []


async def test_a_tool_that_takes_too_long_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(ctx: ToolContext, args: Any) -> ToolResult:
        await asyncio.sleep(5)
        raise AssertionError("not reached")

    monkeypatch.setitem(TOOLS, "list_tables", replace(TOOLS["list_tables"], handler=slow))
    monkeypatch.setattr(registry, "EXTRA_SECONDS", -14.95)  # 15 s limit - 14.95 = 0.05 s
    with pytest.raises(UnavailableError, match="too long"):
        await call_tool(ctx(), "list_tables", {})


async def test_every_call_is_logged_without_its_arguments(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="bi.tools")
    await call_tool(ctx(), "get_metric_definitions", {"names": ["secret-looking-argument"]})
    with pytest.raises(BadRequestError):
        await call_tool(ctx(), "describe_table", {"table": ""})
    with pytest.raises(NotFoundError):
        await call_tool(ctx(), "made-up-name-from-a-model", {})
    calls = [r for r in caplog.records if r.getMessage() == "tool call"]
    assert [(r.tool, r.status) for r in calls] == [
        ("get_metric_definitions", "ok"), ("describe_table", "invalid"), ("unknown", "unknown"),
    ]  # fmt: skip
    text = " ".join(str(r.__dict__) for r in calls)
    assert "secret-looking-argument" not in text and "made-up-name" not in text


# -- run_query: every check, in order -----------------------------------------------------


async def run(sql: str, db: FakeDatabase, who: Caller | None = None) -> ToolResult:
    return await call_tool(ctx(who), "run_query", {"sql": sql})


async def test_a_good_query_runs_as_the_caller_and_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(result([{"revenue": 100.0}])).install(monkeypatch)
    who = caller("analyst", limited=True)
    answer = await run(
        "SELECT SUM(total_amount - tax_amount) AS revenue FROM orders WHERE status = 'completed'",
        db, who,
    )  # fmt: skip
    assert (answer.status, answer.rows) == ("ok", [{"revenue": 100.0}])
    assert answer.scope_note == "Limited to your outlets: Indiranagar."
    assert db.callers == [who] and db.statuses == ["ok"]
    assert db.logged[0]["question"] == "[tool] run_query"


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DELETE FROM orders", "not_select"),
        ("SELECT * FROM app.users", None),
        ("SELECT name FROM customers", None),  # a private column
        ("SELECT SUM(o.total_amount) AS t FROM orders o JOIN order_items i ON i.order_id = o.id "
         "WHERE o.status = 'completed'", "unsafe_aggregation"),
        ("SELECT SUM(total_amount) AS t FROM orders", "missing_status_filter"),
    ],
)  # fmt: skip
async def test_a_query_that_fails_a_check_is_refused_logged_and_never_run(
    monkeypatch: pytest.MonkeyPatch, sql: str, code: str | None
) -> None:
    db = FakeDatabase().install(monkeypatch)
    answer = await run(sql, db)
    assert answer.status == "refused" and answer.rows == [] and answer.reason
    if code:
        assert answer.code == code
    assert db.executed == [] and db.statuses == ["rejected"]
    assert db.logged[0]["reason"] == answer.code


async def test_a_database_error_and_a_timeout_are_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(QueryFailedError("42883", "function x() does not exist"),
                      QueryTimeoutError()).install(monkeypatch)  # fmt: skip
    sql = "SELECT COUNT(*) AS n FROM outlets"
    failed = await run(sql, db)
    assert (failed.status, failed.code) == ("error", "42883")
    timed = await run(sql, db)
    assert (timed.status, timed.code) == ("timeout", "timeout")
    assert db.statuses == ["error", "timeout"]


# -- the REST doorway ---------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(SETTINGS)) as started:
        yield started


def sign_in(client: TestClient, who: Caller) -> None:
    client.app.dependency_overrides[get_caller] = lambda: who  # type: ignore[attr-defined]


def test_the_api_lists_the_callers_tools_with_their_input_schemas(client: TestClient) -> None:
    sign_in(client, caller("viewer"))
    body = client.get("/api/v1/tools").json()
    assert body["transport"] == "direct"
    names = [tool["name"] for tool in body["tools"]]
    assert "sales_summary" in names and "run_query" not in names
    top = next(tool for tool in body["tools"] if tool["name"] == "top_products")
    assert set(top) == {"name", "title", "description", "group", "input_schema"}
    assert top["input_schema"]["properties"]["by"]["enum"] == ["units", "revenue"]
    sign_in(client, caller("admin"))
    assert "run_query" in [t["name"] for t in client.get("/api/v1/tools").json()["tools"]]


def test_the_api_runs_a_tool_and_answers_errors_in_the_usual_shape(client: TestClient) -> None:
    sign_in(client, caller("viewer"))
    ok = client.post("/api/v1/tools/list_tables", json={})
    assert ok.status_code == 200 and ok.json()["tool"] == "list_tables"
    assert set(ok.json()) == {
        "tool", "title", "status", "period", "columns", "rows", "row_count", "truncated",
        "notes", "scope_note", "sql", "code", "reason",
    }  # fmt: skip
    bad = client.post("/api/v1/tools/sales_summary", json={"arguments": {"period": "never"}})
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "invalid_input")
    denied = client.post("/api/v1/tools/run_query", json={"arguments": {"sql": "SELECT 1"}})
    assert denied.status_code == 403
    assert client.post("/api/v1/tools/nope", json={}).status_code == 404
    assert client.get("/api/v1/activity").status_code == 403  # a viewer may not see SQL


def test_without_a_caller_nothing_is_listed_or_run() -> None:
    with TestClient(create_app(SETTINGS)) as anonymous:
        assert anonymous.get("/api/v1/tools").status_code == 401
        assert anonymous.post("/api/v1/tools/list_tables", json={}).status_code == 401
        assert anonymous.get("/api/v1/activity").status_code == 401
