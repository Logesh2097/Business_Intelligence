"""LIVE: the assistant graph against real PostgreSQL, with a scripted model.

Real: LangGraph, the guard, each agent's table list, the read-only user, row security,
the query log, the API. Scripted: the supervisor's decision and what the model would
have written. Figures are checked against values computed separately, as the database
owner. "Today" is pinned to 2026-11-01.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.agents.domain import AgentReport
from app.agents.registry import AGENTS
from app.agents.sql_analyst import SqlDraft
from app.agents.summarizer import Summary
from app.agents.supervisor import AgentTask, Decision
from app.core.config import Settings
from app.llm.fake import Call, FakeLLM
from app.main import create_app
from app.seed.generate import uid
from app.store.connections import Stores
from app.workflows import assistant
from app.workflows.assistant import AssistantAnswer, Combined
from tests.integration.test_agents_live import (
    CUSTOMERS_VISITED,
    SALES_BY_OUTLET,
    SHORTAGE_BY_OUTLET,
)
from tests.integration.test_analytics_live import REVENUE_LAST_MONTH, expected
from tests.integration.test_schema_live import who

URL = "/api/v1/assistant/query"
BREW = uid("tenant", "brewbite")
URBAN = uid("tenant", "urbanmart")
ANALYST = "analyst@brewbite.example"
URBAN_ADMIN = "admin@urbanmart.example"
VIEWER = "viewer@brewbite.example"  # limited to Indiranagar and Koramangala

# what a model is expected to write for the general route
EXPENSES_BY_CATEGORY = (
    "SELECT c.name AS category, ROUND(SUM(e.amount), 2) AS spend FROM expenses e "
    "JOIN expense_categories c ON c.id = e.category_id WHERE e.expense_date BETWEEN "
    "DATE '2026-10-01' AND DATE '2026-10-31' GROUP BY c.name ORDER BY spend DESC, c.name"
)
PURCHASES_BY_SUPPLIER = (
    "SELECT s.name AS supplier, COUNT(*) AS purchase_orders, "
    "ROUND(SUM(p.total_amount), 2) AS purchases FROM purchase_orders p "
    "JOIN suppliers s ON s.id = p.supplier_id WHERE p.status = 'received' "
    "GROUP BY s.name ORDER BY purchases DESC, s.name"
)
# sales joined to inventory: no single agent may do this
REVENUE_OF_LOW_STOCK_PRODUCTS = (
    "SELECT p.name AS product, ROUND(SUM(i.line_total), 2) AS revenue FROM order_items i "
    "JOIN orders o ON o.id = i.order_id JOIN products p ON p.id = i.product_id "
    "WHERE o.status = 'completed' AND EXISTS (SELECT 1 FROM inventory_levels l "
    "WHERE l.product_id = p.id AND l.quantity_on_hand <= l.reorder_level) "
    "GROUP BY p.name ORDER BY revenue DESC, p.name"
)
# customers joined to sales
REVENUE_BY_SEGMENT = (
    "SELECT c.segment AS segment, ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue "
    "FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.status = 'completed' "
    "GROUP BY c.segment ORDER BY c.segment"
)
EXPENSES_BY_OUTLET = (
    "SELECT t.name AS outlet, ROUND(SUM(e.amount), 2) AS spend FROM expenses e "
    "JOIN outlets t ON t.id = e.outlet_id GROUP BY t.name ORDER BY t.name"
)


def to_agents(*names: str) -> Decision:
    return Decision(route="agents", agents=[AgentTask(agent=n) for n in names], reason="areas")


GENERAL = Decision(route="general", reason="Needs data outside one agent.")


def target_of(call: Call) -> str:
    for agent in AGENTS.values():
        if agent.prompt in call.system:
            return agent.name
    return "general"


def per_target(sql: dict[str, str]) -> Callable[[Call], BaseModel]:
    return lambda call: SqlDraft(sql=sql[target_of(call)], explanation="x")


def scripted(decision: Decision, sql: dict[str, str], combined: str = "") -> FakeLLM:
    llm = FakeLLM().on("route", decision)
    for _ in sql:
        llm.on("sql", per_target(sql))
        llm.on("sql_repair", per_target(sql), per_target(sql))
        llm.on("agent_report", AgentReport(summary="Done."))
    llm.on("summary", Summary(text="Done."))
    if combined:
        llm.on("supervisor_report", Combined(text=combined))
    return llm


async def ask(
    stores: Stores, settings: Settings, llm: FakeLLM, *, email: str = ANALYST, question: str = "q?"
) -> AssistantAnswer:
    return await assistant.ask(
        question=question,
        caller=await who(stores, email),
        llm=llm,
        pg=stores.postgres,
        settings=settings,
    )


# -- routing to agents -------------------------------------------------------------------


async def test_a_single_agent_through_the_supervisor_gives_the_owners_figure(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT round(sum(total_amount - tax_amount), 2) AS revenue FROM biz.orders "
        "WHERE tenant_id = %s AND status = 'completed' "
        "AND order_date BETWEEN '2026-10-01' AND '2026-10-31'",
        BREW,
    )
    llm = scripted(to_agents("sales_agent"), {"sales_agent": REVENUE_LAST_MONTH})
    answer = await ask(
        seeded, seeded_settings, llm, question="What was our total revenue last month?"
    )
    assert (answer.status, answer.route) == ("answered", "agents")
    assert [p.agent for p in answer.results] == ["sales_agent"]
    assert answer.results[0].data == want
    assert answer.results[0].question == "What was our total revenue last month?"


async def test_three_agents_in_parallel_each_give_the_owners_figures(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    revenue = expected(
        owner,
        "SELECT t.name AS outlet, round(sum(o.total_amount - o.tax_amount), 2) AS revenue "
        "FROM biz.orders o JOIN biz.outlets t ON t.tenant_id = o.tenant_id AND t.id = o.outlet_id "
        "WHERE o.tenant_id = %s AND o.status = 'completed' "
        "AND o.order_date BETWEEN '2026-10-01' AND '2026-10-31' GROUP BY 1 ORDER BY 2 DESC",
        BREW,
    )
    shortage = expected(
        owner,
        "SELECT t.name AS outlet, count(*) AS products_short, "
        "sum(l.reorder_level - l.quantity_on_hand) AS shortage FROM biz.inventory_levels l "
        "JOIN biz.outlets t ON t.tenant_id = l.tenant_id AND t.id = l.outlet_id "
        "WHERE l.tenant_id = %s AND l.quantity_on_hand < l.reorder_level "
        "GROUP BY 1 ORDER BY 3 DESC, 1",
        BREW,
    )
    visitors = expected(
        owner,
        "SELECT count(DISTINCT customer_id) AS customers, count(*) AS visits "
        "FROM biz.customer_visits WHERE tenant_id = %s "
        "AND visit_date BETWEEN '2026-10-01' AND '2026-10-31'",
        BREW,
    )
    sql = {
        "sales_agent": SALES_BY_OUTLET,
        "inventory_agent": SHORTAGE_BY_OUTLET,
        "customer_agent": CUSTOMERS_VISITED,
    }
    combined = (
        f"{revenue[0]['outlet']} led on revenue and {visitors[0]['customers']} customers visited."
    )
    llm = scripted(to_agents("sales_agent", "inventory_agent", "customer_agent"), sql, combined)

    answer = await ask(seeded, seeded_settings, llm, question="Revenue, shortage and visitors?")

    assert answer.status == "answered" and answer.summary == combined
    assert [p.agent for p in answer.results] == list(sql)
    assert [p.data for p in answer.results] == [revenue, shortage, visitors]


# -- the general route -------------------------------------------------------------------


async def test_an_expenses_question_goes_through_the_general_route(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT c.name AS category, round(sum(e.amount), 2) AS spend FROM biz.expenses e "
        "JOIN biz.expense_categories c ON c.tenant_id = e.tenant_id AND c.id = e.category_id "
        "WHERE e.tenant_id = %s AND e.expense_date BETWEEN '2026-10-01' AND '2026-10-31' "
        "GROUP BY 1 ORDER BY 2 DESC, 1",
        BREW,
    )
    llm = scripted(GENERAL, {"general": EXPENSES_BY_CATEGORY})
    question = "What did we spend by expense category last month?"
    answer = await ask(seeded, seeded_settings, llm, question=question)

    assert (answer.status, answer.route) == ("answered", "general")
    assert [p.agent for p in answer.results] == ["general"]
    assert want and answer.results[0].data == want
    assert answer.results[0].question == question
    # the general workflow was shown the expenses tables, which no agent has
    assert "TABLE expenses:" in llm.calls_for("sql")[0].user


async def test_a_purchasing_question_goes_through_the_general_route(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT s.name AS supplier, count(*) AS purchase_orders, "
        "round(sum(p.total_amount), 2) AS purchases FROM biz.purchase_orders p "
        "JOIN biz.suppliers s ON s.tenant_id = p.tenant_id AND s.id = p.supplier_id "
        "WHERE p.tenant_id = %s AND p.status = 'received' GROUP BY 1 ORDER BY 3 DESC, 1",
        BREW,
    )
    llm = scripted(GENERAL, {"general": PURCHASES_BY_SUPPLIER})
    answer = await ask(seeded, seeded_settings, llm, question="How much did we buy per supplier?")
    assert want and answer.results[0].data == want and answer.status == "answered"


async def test_a_question_joining_sales_and_inventory_is_one_query_in_the_general_route(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT p.name AS product, round(sum(i.line_total), 2) AS revenue FROM biz.order_items i "
        "JOIN biz.orders o ON o.tenant_id = i.tenant_id AND o.id = i.order_id "
        "JOIN biz.products p ON p.tenant_id = i.tenant_id AND p.id = i.product_id "
        "WHERE i.tenant_id = %s AND o.status = 'completed' AND p.id IN ("
        "SELECT l.product_id FROM biz.inventory_levels l WHERE l.tenant_id = i.tenant_id "
        "AND l.quantity_on_hand <= l.reorder_level) GROUP BY 1 ORDER BY 2 DESC, 1",
        BREW,
    )
    llm = scripted(GENERAL, {"general": REVENUE_OF_LOW_STOCK_PRODUCTS})
    answer = await ask(
        seeded, seeded_settings, llm, question="What revenue comes from products low on stock?"
    )
    assert want and answer.results[0].data == want
    assert len(answer.results) == 1 and answer.route == "general"


async def test_a_question_joining_customers_and_sales_is_answered_by_the_general_route(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT c.segment AS segment, round(sum(o.total_amount - o.tax_amount), 2) AS revenue "
        "FROM biz.orders o JOIN biz.customers c ON c.tenant_id = o.tenant_id "
        "AND c.id = o.customer_id WHERE o.tenant_id = %s AND o.status = 'completed' "
        "GROUP BY 1 ORDER BY 1",
        BREW,
    )
    llm = scripted(GENERAL, {"general": REVENUE_BY_SEGMENT})
    answer = await ask(seeded, seeded_settings, llm, question="Revenue by customer segment?")
    assert want and answer.results[0].data == want


async def test_the_same_join_is_refused_when_it_is_sent_to_a_single_agent(
    seeded: Stores, seeded_settings: Settings
) -> None:
    llm = scripted(to_agents("sales_agent"), {"sales_agent": REVENUE_OF_LOW_STOCK_PRODUCTS})
    answer = await ask(seeded, seeded_settings, llm, question="Revenue of low-stock products?")
    # inventory_levels is not the Sales agent's: only the general route may join the two
    assert answer.status == "failed" and answer.results[0].data == []


# -- tenant and outlet isolation through every route ----------------------------------------

ROUTES = [
    (to_agents("sales_agent"), "sales_agent", "SELECT COUNT(*) AS n FROM orders o", "biz.orders"),
    (
        to_agents("inventory_agent"),
        "inventory_agent",
        "SELECT COUNT(*) AS n FROM inventory_levels l",
        "biz.inventory_levels",
    ),
    (
        to_agents("customer_agent"),
        "customer_agent",
        "SELECT COUNT(*) AS n FROM customer_visits v",
        "biz.customer_visits",
    ),
    (GENERAL, "general", "SELECT COUNT(*) AS n FROM expenses e", "biz.expenses"),
    (GENERAL, "general", "SELECT COUNT(*) AS n FROM purchase_orders p", "biz.purchase_orders"),
]


@pytest.mark.parametrize(("decision", "target", "sql", "table"), ROUTES)
async def test_each_company_gets_only_its_own_rows_through_every_route(
    decision: Decision,
    target: str,
    sql: str,
    table: str,
    seeded: Stores,
    seeded_settings: Settings,
    owner: psycopg.Connection[Any],
) -> None:
    counts = {
        row["tenant_id"]: row["n"]
        for row in owner.execute(f"SELECT tenant_id, count(*) AS n FROM {table} GROUP BY 1")  # noqa: S608
    }
    brew = await ask(seeded, seeded_settings, scripted(decision, {target: sql}))
    urban = await ask(seeded, seeded_settings, scripted(decision, {target: sql}), email=URBAN_ADMIN)

    assert brew.results[0].data == [{"n": counts[BREW]}]
    assert urban.results[0].data == [{"n": counts[URBAN]}]
    assert counts[BREW] > 0 and counts[URBAN] > 0 and counts[BREW] != counts[URBAN]


async def test_a_limited_viewer_gets_only_their_outlets_through_the_general_route(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT t.name AS outlet, round(sum(e.amount), 2) AS spend FROM biz.expenses e "
        "JOIN biz.outlets t ON t.tenant_id = e.tenant_id AND t.id = e.outlet_id "
        "WHERE e.tenant_id = %s AND t.name IN ('Indiranagar', 'Koramangala') GROUP BY 1 ORDER BY 1",
        BREW,
    )
    everyone = await ask(
        seeded, seeded_settings, scripted(GENERAL, {"general": EXPENSES_BY_OUTLET})
    )
    viewer = await ask(
        seeded, seeded_settings, scripted(GENERAL, {"general": EXPENSES_BY_OUTLET}), email=VIEWER
    )
    assert viewer.results[0].data == want and len(want) == 2
    assert len(everyone.results[0].data) > 2  # the analyst sees more outlets with the same SQL
    assert viewer.scope_note == "Limited to your outlets: Indiranagar, Koramangala."
    assert viewer.summary == "Done. Limited to your outlets: Indiranagar, Koramangala."


async def test_a_limited_viewer_gets_only_their_outlets_from_agents_in_parallel(
    seeded: Stores, seeded_settings: Settings
) -> None:
    by_outlet = {
        "sales_agent": "SELECT DISTINCT t.name AS outlet FROM orders o "
        "JOIN outlets t ON t.id = o.outlet_id ORDER BY outlet",
        "customer_agent": "SELECT DISTINCT t.name AS outlet FROM customer_visits v "
        "JOIN outlets t ON t.id = v.outlet_id ORDER BY outlet",
    }
    llm = scripted(to_agents("sales_agent", "customer_agent"), by_outlet, "Two outlets each.")
    answer = await ask(seeded, seeded_settings, llm, email=VIEWER)
    mine = [{"outlet": "Indiranagar"}, {"outlet": "Koramangala"}]
    assert [p.data for p in answer.results] == [mine, mine]
    assert answer.summary == "Two outlets each. Limited to your outlets: Indiranagar, Koramangala."


async def test_naming_the_other_company_gets_nothing_through_the_general_route(
    seeded: Stores, seeded_settings: Settings
) -> None:
    sql = "SELECT t.name AS outlet FROM outlets t WHERE t.name ILIKE '%pune%'"  # UrbanMart's
    answer = await ask(seeded, seeded_settings, scripted(GENERAL, {"general": sql}))
    assert answer.status == "no_data" and answer.results[0].data == []


# -- SQL security through the graph ---------------------------------------------------------


@pytest.mark.parametrize(
    "attack",
    [
        "DELETE FROM expenses",
        "UPDATE inventory_levels SET quantity_on_hand = 0",
        "SELECT 1 AS one; DROP TABLE orders",
        "SELECT u.email AS email, u.password_hash AS hash FROM app.users u",
        "SELECT set_config('app.tenant_id', '00000000-0000-0000-0000-000000000000', false) AS s",
        "SELECT c.full_name AS name, c.phone AS phone FROM customers c",
    ],
)
@pytest.mark.parametrize(
    ("decision", "target"),
    [(GENERAL, "general"), (to_agents("inventory_agent"), "inventory_agent")],
)
async def test_unsafe_sql_is_refused_through_the_graph_and_changes_nothing(
    decision: Decision,
    target: str,
    attack: str,
    seeded: Stores,
    seeded_settings: Settings,
    owner: psycopg.Connection[Any],
) -> None:
    def counts() -> dict[str, Any]:
        owner.rollback()
        row = owner.execute(
            "SELECT (SELECT count(*) FROM biz.orders) AS orders, "
            "(SELECT count(*) FROM biz.expenses) AS expenses, "
            "(SELECT sum(quantity_on_hand) FROM biz.inventory_levels) AS stock"
        ).fetchone()
        assert row is not None
        return dict(row)

    before = counts()
    question = f"attack through {target}: {attack[:20]}"
    answer = await ask(
        seeded, seeded_settings, scripted(decision, {target: attack}), question=question
    )

    assert answer.status == "failed" and answer.results[0].data == []
    assert answer.results[0].sql is None and attack not in answer.summary
    assert counts() == before
    logged = owner.execute(
        "SELECT status FROM app.query_log WHERE question = %s", (question,)
    ).fetchall()
    assert [r["status"] for r in logged] == ["rejected"] * 3


async def test_every_part_is_in_the_query_log_under_the_right_company_and_user(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    sql = {"sales_agent": SALES_BY_OUTLET, "customer_agent": CUSTOMERS_VISITED}
    llm = scripted(to_agents("sales_agent", "customer_agent"), sql, "Done.")
    question = "logged through the supervisor?"
    await ask(seeded, seeded_settings, llm, question=question)
    owner.rollback()
    rows = owner.execute(
        "SELECT q.tenant_id, u.email, q.status FROM app.query_log q "
        "JOIN app.users u ON u.id = q.user_id WHERE q.question = %s",
        (question,),
    ).fetchall()
    assert len(rows) == 2
    assert {(r["tenant_id"], r["email"], r["status"]) for r in rows} == {(BREW, ANALYST, "ok")}


# -- the API, end to end --------------------------------------------------------------------


@pytest.fixture
def api(seeded_settings: Settings) -> Iterator[tuple[TestClient, FakeLLM]]:
    with TestClient(create_app(seeded_settings)) as client:
        llm = FakeLLM()
        client.app.state.llm = llm  # type: ignore[attr-defined]
        yield client, llm


def test_the_api_runs_the_general_route_for_an_analyst_and_a_viewer(
    api: tuple[TestClient, FakeLLM],
) -> None:
    client, llm = api
    bodies = {}
    for email in (ANALYST, VIEWER):
        llm.on("route", GENERAL).on("sql", SqlDraft(sql=EXPENSES_BY_OUTLET))
        llm.on("summary", Summary(text="Spend by outlet."))
        response = client.post(
            URL, json={"question": "What did each outlet spend?"}, headers={"X-Dev-User": email}
        )
        assert response.status_code == 200
        bodies[email] = response.json()

    analyst, viewer = bodies[ANALYST], bodies[VIEWER]
    assert analyst["route"] == viewer["route"] == "general"
    assert analyst["results"][0]["sql"].startswith("SELECT t.name AS outlet")
    assert viewer["results"][0]["sql"] is None
    assert {r["outlet"] for r in viewer["results"][0]["data"]} == {"Indiranagar", "Koramangala"}
    assert len(analyst["results"][0]["data"]) > 2


def test_the_api_combines_two_agents_and_refuses_an_unknown_user(
    api: tuple[TestClient, FakeLLM],
) -> None:
    client, llm = api
    sql = {"sales_agent": SALES_BY_OUTLET, "inventory_agent": SHORTAGE_BY_OUTLET}
    llm.on("route", to_agents("sales_agent", "inventory_agent"))
    llm.on("sql", per_target(sql), per_target(sql))
    llm.on("agent_report", AgentReport(summary="Ranked."), AgentReport(summary="Ranked."))
    llm.on("supervisor_report", Combined(text="Outlets are ranked by revenue and by shortage."))
    body = client.post(
        URL, json={"question": "Revenue and shortage by outlet?"}, headers={"X-Dev-User": ANALYST}
    ).json()
    assert body["status"] == "answered"
    assert [r["agent"] for r in body["results"]] == ["sales_agent", "inventory_agent"]
    assert body["summary"] == "Outlets are ranked by revenue and by shortage."

    unknown = client.post(URL, json={"question": "Revenue?"}, headers={"X-Dev-User": "x@x.example"})
    assert unknown.status_code == 401
