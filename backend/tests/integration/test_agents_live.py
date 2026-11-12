"""LIVE: the three domain agents against real PostgreSQL, with a scripted model.

Real: the guard (with each agent's own table list), the read-only user, row security,
the query log, the API. Scripted: only what the model would have written. The figures
are checked against values computed separately, as the database owner, straight from
the tables. "Today" is pinned to 2026-11-01, so last month is October 2026.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.agents import domain
from app.agents.domain import AgentAnswer, AgentReport
from app.agents.registry import AGENTS
from app.agents.sql_analyst import SqlDraft
from app.core.config import Settings
from app.llm.fake import FakeLLM
from app.main import create_app
from app.seed.generate import uid
from app.store.connections import Stores
from tests.integration.test_analytics_live import expected
from tests.integration.test_schema_live import who

BREW = uid("tenant", "brewbite")
URBAN = uid("tenant", "urbanmart")
ANALYST = "analyst@brewbite.example"
URBAN_ADMIN = "admin@urbanmart.example"
VIEWER = "viewer@brewbite.example"  # limited to Indiranagar and Koramangala

# -- what a model is expected to write, per agent ------------------------------------------

SALES_COMPARISON = (
    "SELECT ROUND(SUM(o.total_amount - o.tax_amount) FILTER (WHERE o.order_date BETWEEN "
    "DATE '2026-10-01' AND DATE '2026-10-31'), 2) AS current_revenue, "
    "ROUND(SUM(o.total_amount - o.tax_amount) FILTER (WHERE o.order_date BETWEEN "
    "DATE '2026-09-01' AND DATE '2026-09-30'), 2) AS previous_revenue, "
    "ROUND(SUM(o.total_amount - o.tax_amount) FILTER (WHERE o.order_date BETWEEN "
    "DATE '2026-10-01' AND DATE '2026-10-31') - SUM(o.total_amount - o.tax_amount) FILTER "
    "(WHERE o.order_date BETWEEN DATE '2026-09-01' AND DATE '2026-09-30'), 2) AS change, "
    "ROUND(100.0 * (SUM(o.total_amount - o.tax_amount) FILTER (WHERE o.order_date BETWEEN "
    "DATE '2026-10-01' AND DATE '2026-10-31') - SUM(o.total_amount - o.tax_amount) FILTER "
    "(WHERE o.order_date BETWEEN DATE '2026-09-01' AND DATE '2026-09-30')) / "
    "NULLIF(SUM(o.total_amount - o.tax_amount) FILTER (WHERE o.order_date BETWEEN "
    "DATE '2026-09-01' AND DATE '2026-09-30'), 0), 1) AS change_percent "
    "FROM orders o WHERE o.status = 'completed'"
)
SALES_BY_OUTLET = (
    "SELECT t.name AS outlet, ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue "
    "FROM orders o JOIN outlets t ON t.id = o.outlet_id WHERE o.status = 'completed' "
    "AND o.order_date BETWEEN DATE '2026-10-01' AND DATE '2026-10-31' "
    "GROUP BY t.name ORDER BY revenue DESC"
)
SALES_DAILY_CHANGES = (
    "SELECT d.day AS day, d.revenue AS revenue, "
    "LAG(d.revenue) OVER (ORDER BY d.day) AS previous_revenue FROM ("
    "SELECT o.order_date AS day, ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue "
    "FROM orders o WHERE o.status = 'completed' GROUP BY o.order_date) AS d ORDER BY d.day"
)
LOW_STOCK = (
    "SELECT t.name AS outlet, p.name AS product, l.quantity_on_hand AS on_hand, "
    "l.reorder_level AS reorder_level FROM inventory_levels l "
    "JOIN products p ON p.id = l.product_id JOIN outlets t ON t.id = l.outlet_id "
    "WHERE p.is_active AND l.quantity_on_hand <= l.reorder_level AND l.quantity_on_hand > 0 "
    "ORDER BY t.name, p.name"
)
OUT_OF_STOCK = (
    "SELECT t.name AS outlet, p.name AS product, l.quantity_on_hand AS on_hand "
    "FROM inventory_levels l JOIN products p ON p.id = l.product_id "
    "JOIN outlets t ON t.id = l.outlet_id WHERE p.is_active AND l.quantity_on_hand <= 0 "
    "ORDER BY t.name, p.name"
)
SHORTAGE_BY_OUTLET = (
    "SELECT t.name AS outlet, COUNT(*) AS products_short, "
    "SUM(l.reorder_level - l.quantity_on_hand) AS shortage FROM inventory_levels l "
    "JOIN outlets t ON t.id = l.outlet_id WHERE l.quantity_on_hand < l.reorder_level "
    "GROUP BY t.name ORDER BY shortage DESC, t.name"
)
NOT_SOLD_RECENTLY = (
    "SELECT p.name AS product, MAX(i.order_date) AS last_sold FROM products p "
    "LEFT JOIN order_items i ON i.product_id = p.id AND i.order_date >= DATE '2026-10-02' "
    "WHERE p.is_active GROUP BY p.name HAVING MAX(i.order_date) IS NULL ORDER BY p.name"
)
STOCK_MOVEMENTS = (
    "SELECT m.movement_type AS movement, COUNT(*) AS movements, SUM(m.quantity) AS quantity "
    "FROM stock_movements m GROUP BY m.movement_type ORDER BY m.movement_type"
)
CUSTOMERS_VISITED = (
    "SELECT COUNT(DISTINCT v.customer_id) AS customers, COUNT(*) AS visits "
    "FROM customer_visits v WHERE v.visit_date BETWEEN DATE '2026-10-01' AND DATE '2026-10-31'"
)
VISITS_BY_OUTLET = (
    "SELECT t.name AS outlet, COUNT(*) AS visits FROM customer_visits v "
    "JOIN outlets t ON t.id = v.outlet_id WHERE v.visit_date BETWEEN DATE '2026-10-01' "
    "AND DATE '2026-10-31' GROUP BY t.name ORDER BY visits DESC, t.name"
)
RETURNING_CUSTOMERS = (
    "SELECT COUNT(*) AS returning_customers FROM (SELECT v.customer_id FROM customer_visits v "
    "WHERE v.visit_date BETWEEN DATE '2026-10-01' AND DATE '2026-10-31' "
    "AND v.customer_id IS NOT NULL AND EXISTS (SELECT 1 FROM customer_visits e "
    "WHERE e.customer_id = v.customer_id AND e.visit_date < DATE '2026-10-01') "
    "GROUP BY v.customer_id) AS r"
)
VISITS_TREND = (
    "SELECT COUNT(*) FILTER (WHERE v.visit_date BETWEEN DATE '2026-10-01' AND DATE '2026-10-31') "
    "AS current_visits, COUNT(*) FILTER (WHERE v.visit_date BETWEEN DATE '2026-09-01' AND "
    "DATE '2026-09-30') AS previous_visits FROM customer_visits v"
)
NEW_CUSTOMERS_BY_SEGMENT = (
    "SELECT c.segment AS segment, COUNT(*) AS new_customers FROM customers c "
    "WHERE c.joined_on BETWEEN DATE '2026-10-01' AND DATE '2026-10-31' "
    "GROUP BY c.segment ORDER BY c.segment"
)


async def ask(
    stores: Stores,
    settings: Settings,
    agent: str,
    sql: str | None,
    *,
    email: str = ANALYST,
    question: str = "a question",
    llm: FakeLLM | None = None,
    summary: str = "Done.",
    insights: tuple[str, ...] = (),
) -> tuple[AgentAnswer, FakeLLM]:
    llm = llm or FakeLLM().on("sql", SqlDraft(sql=sql, explanation="because"))
    llm.on("agent_report", AgentReport(summary=summary, insights=list(insights)))
    answer = await domain.run(
        AGENTS[agent],
        question=question,
        caller=await who(stores, email),
        llm=llm,
        pg=stores.postgres,
        settings=settings,
    )
    return answer, llm


def plain(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dates as text, as the API returns them."""
    return [
        {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in r.items()} for r in rows
    ]


# -- the Sales agent ------------------------------------------------------------------------


async def test_sales_agent_compares_two_months_with_the_change_worked_out_by_the_database(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    months = expected(
        owner,
        "SELECT to_char(order_date, 'YYYY-MM') AS month, "
        "round(sum(total_amount - tax_amount), 2) AS revenue FROM biz.orders "
        "WHERE tenant_id = %s AND status = 'completed' "
        "AND order_date BETWEEN '2026-09-01' AND '2026-10-31' GROUP BY 1 ORDER BY 1",
        BREW,
    )
    september, october = (m["revenue"] for m in months)
    change = round(october - september, 2)
    percent = round(100 * (october - september) / september, 1)

    answer, llm = await ask(
        seeded, seeded_settings, "sales_agent", SALES_COMPARISON,
        question="How much did sales increase compared to last month?",
        summary=f"Revenue was INR {october:,.2f}, a change of {percent}% on the month before.",
        insights=(f"The change was INR {change:,.2f}.", "Sales doubled to INR 9,999,999."),
    )  # fmt: skip

    assert answer.agent == "sales_agent" and answer.status == "answered"
    assert answer.data == [
        {
            "current_revenue": october,
            "previous_revenue": september,
            "change": change,
            "change_percent": percent,
        }
    ]
    assert answer.summary.startswith(f"Revenue was INR {october:,.2f}")
    # the true insight is kept; the one with a figure that is not in the result is dropped
    assert answer.insights == [f"The change was INR {change:,.2f}."]
    # the model was given the sales tables and the ranges, and nothing of the other areas
    prompt = llm.calls[0].user
    assert "- the month before last: 2026-09-01 to 2026-09-30" in prompt
    assert "TABLE orders:" in prompt and "inventory_levels" not in prompt


async def test_sales_agent_ranks_outlets_by_revenue(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT t.name AS outlet, round(sum(o.total_amount - o.tax_amount), 2) AS revenue "
        "FROM biz.orders o JOIN biz.outlets t ON t.tenant_id = o.tenant_id AND t.id = o.outlet_id "
        "WHERE o.tenant_id = %s AND o.status = 'completed' "
        "AND o.order_date BETWEEN '2026-10-01' AND '2026-10-31' GROUP BY 1 ORDER BY 2 DESC",
        BREW,
    )
    answer, _ = await ask(
        seeded, seeded_settings, "sales_agent", SALES_BY_OUTLET,
        question="Which outlet performed best this month?",
        summary=f"{want[0]['outlet']} performed best.",
    )  # fmt: skip
    assert answer.data == want and len(want) == 5
    assert answer.columns[1] == {"name": "revenue", "type": "money"}


async def test_sales_agent_returns_each_day_beside_the_day_before(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT order_date AS day, round(sum(total_amount - tax_amount), 2) AS revenue "
        "FROM biz.orders WHERE tenant_id = %s AND status = 'completed' GROUP BY 1 ORDER BY 1",
        BREW,
    )
    answer, _ = await ask(seeded, seeded_settings, "sales_agent", SALES_DAILY_CHANGES)
    assert [r["revenue"] for r in answer.data] == [r["revenue"] for r in want]
    assert answer.data[0]["previous_revenue"] is None
    assert answer.data[1]["previous_revenue"] == want[0]["revenue"]


# -- the Inventory agent --------------------------------------------------------------------


async def test_inventory_agent_lists_low_stock_products(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT t.name AS outlet, p.name AS product, l.quantity_on_hand AS on_hand, "
        "l.reorder_level AS reorder_level FROM biz.inventory_levels l "
        "JOIN biz.products p ON p.tenant_id = l.tenant_id AND p.id = l.product_id "
        "JOIN biz.outlets t ON t.tenant_id = l.tenant_id AND t.id = l.outlet_id "
        "WHERE l.tenant_id = %s AND p.is_active AND l.quantity_on_hand <= l.reorder_level "
        "AND l.quantity_on_hand > 0 ORDER BY 1, 2",
        BREW,
    )
    answer, _ = await ask(
        seeded, seeded_settings, "inventory_agent", LOW_STOCK,
        question="Which products are running low on stock?",
        summary=f"{len(want)} products are running low.",
    )  # fmt: skip
    assert want, "the demo data has low-stock rows"
    assert answer.agent == "inventory_agent" and answer.data == want
    assert all(0 < r["on_hand"] <= r["reorder_level"] for r in answer.data)


async def test_inventory_agent_finds_out_of_stock_products(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT count(*) AS n FROM biz.inventory_levels l JOIN biz.products p "
        "ON p.tenant_id = l.tenant_id AND p.id = l.product_id "
        "WHERE l.tenant_id = %s AND p.is_active AND l.quantity_on_hand <= 0",
        BREW,
    )[0]["n"]
    answer, _ = await ask(seeded, seeded_settings, "inventory_agent", OUT_OF_STOCK)
    assert answer.row_count == want
    assert answer.status == ("answered" if want else "no_data")
    assert all(r["on_hand"] <= 0 for r in answer.data)


async def test_inventory_agent_compares_the_shortage_across_outlets(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT t.name AS outlet, count(*) AS products_short, "
        "sum(l.reorder_level - l.quantity_on_hand) AS shortage FROM biz.inventory_levels l "
        "JOIN biz.outlets t ON t.tenant_id = l.tenant_id AND t.id = l.outlet_id "
        "WHERE l.tenant_id = %s AND l.quantity_on_hand < l.reorder_level "
        "GROUP BY 1 ORDER BY 3 DESC, 1",
        BREW,
    )
    answer, _ = await ask(
        seeded, seeded_settings, "inventory_agent", SHORTAGE_BY_OUTLET,
        question="Which outlet has the highest stock shortage?",
    )  # fmt: skip
    assert want and answer.data == want


async def test_inventory_agent_finds_products_that_have_not_sold_recently(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT p.name AS product FROM biz.products p WHERE p.tenant_id = %s AND p.is_active "
        "AND NOT EXISTS (SELECT 1 FROM biz.order_items i WHERE i.tenant_id = p.tenant_id "
        "AND i.product_id = p.id AND i.order_date >= '2026-10-02') ORDER BY 1",
        BREW,
    )
    answer, _ = await ask(seeded, seeded_settings, "inventory_agent", NOT_SOLD_RECENTLY)
    assert [r["product"] for r in answer.data] == [r["product"] for r in want]
    assert all(r["last_sold"] is None for r in answer.data)


async def test_inventory_agent_summarises_stock_movements(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT movement_type AS movement, count(*) AS movements, sum(quantity) AS quantity "
        "FROM biz.stock_movements WHERE tenant_id = %s GROUP BY 1 ORDER BY 1",
        BREW,
    )
    answer, _ = await ask(seeded, seeded_settings, "inventory_agent", STOCK_MOVEMENTS)
    assert want and answer.data == want


# -- the Customer agent ---------------------------------------------------------------------


async def test_customer_agent_counts_the_customers_who_visited(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT count(DISTINCT customer_id) AS customers, count(*) AS visits "
        "FROM biz.customer_visits WHERE tenant_id = %s "
        "AND visit_date BETWEEN '2026-10-01' AND '2026-10-31'",
        BREW,
    )
    answer, _ = await ask(
        seeded, seeded_settings, "customer_agent", CUSTOMERS_VISITED,
        question="How many customers visited this month?",
        summary=f"{want[0]['customers']} customers visited.",
        insights=(f"They made {want[0]['visits']} visits.",),
    )  # fmt: skip
    assert answer.agent == "customer_agent" and answer.data == want
    assert want[0]["customers"] > 0
    assert answer.insights == [f"They made {want[0]['visits']} visits."]


async def test_customer_agent_compares_visits_across_outlets(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT t.name AS outlet, count(*) AS visits FROM biz.customer_visits v "
        "JOIN biz.outlets t ON t.tenant_id = v.tenant_id AND t.id = v.outlet_id "
        "WHERE v.tenant_id = %s AND v.visit_date BETWEEN '2026-10-01' AND '2026-10-31' "
        "GROUP BY 1 ORDER BY 2 DESC, 1",
        BREW,
    )
    answer, _ = await ask(seeded, seeded_settings, "customer_agent", VISITS_BY_OUTLET)
    assert answer.data == want and len(want) == 5


async def test_customer_agent_counts_returning_customers(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT count(DISTINCT v.customer_id) AS returning_customers FROM biz.customer_visits v "
        "WHERE v.tenant_id = %s AND v.visit_date BETWEEN '2026-10-01' AND '2026-10-31' "
        "AND v.customer_id IN (SELECT e.customer_id FROM biz.customer_visits e "
        "WHERE e.tenant_id = v.tenant_id AND e.visit_date < '2026-10-01')",
        BREW,
    )
    answer, _ = await ask(seeded, seeded_settings, "customer_agent", RETURNING_CUSTOMERS)
    assert answer.data == want and want[0]["returning_customers"] > 0


async def test_customer_agent_sets_this_months_visits_beside_last_months(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT count(*) FILTER (WHERE visit_date >= '2026-10-01') AS current_visits, "
        "count(*) FILTER (WHERE visit_date < '2026-10-01') AS previous_visits "
        "FROM biz.customer_visits WHERE tenant_id = %s "
        "AND visit_date BETWEEN '2026-09-01' AND '2026-10-31'",
        BREW,
    )
    answer, _ = await ask(seeded, seeded_settings, "customer_agent", VISITS_TREND)
    assert answer.data == want


async def test_customer_agent_groups_new_customers_and_never_lists_people(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT segment, count(*) AS new_customers FROM biz.customers WHERE tenant_id = %s "
        "AND joined_on BETWEEN '2026-10-01' AND '2026-10-31' GROUP BY 1 ORDER BY 1",
        BREW,
    )
    answer, _ = await ask(seeded, seeded_settings, "customer_agent", NEW_CUSTOMERS_BY_SEGMENT)
    assert answer.data == want

    # a personal column is refused by the guard, and by the database behind it
    private = "SELECT c.full_name AS name, c.phone AS phone FROM customers c"
    llm = FakeLLM().on("sql", SqlDraft(sql=private))
    llm.on("sql_repair", SqlDraft(sql=private), SqlDraft(sql=private))
    refused, _ = await ask(seeded, seeded_settings, "customer_agent", None, llm=llm)
    assert refused.status == "failed" and refused.data == [] and refused.sql is None


# -- tenant and outlet isolation, for every agent -------------------------------------------

PER_AGENT = [
    ("sales_agent", "SELECT COUNT(*) AS n FROM orders o", "biz.orders"),
    ("inventory_agent", "SELECT COUNT(*) AS n FROM inventory_levels l", "biz.inventory_levels"),
    ("customer_agent", "SELECT COUNT(*) AS n FROM customer_visits v", "biz.customer_visits"),
]


@pytest.mark.parametrize(("agent", "sql", "table"), PER_AGENT)
async def test_each_agent_gives_each_company_its_own_rows_and_never_the_other(
    agent: str,
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
    brew, _ = await ask(seeded, seeded_settings, agent, sql)
    urban, _ = await ask(seeded, seeded_settings, agent, sql, email=URBAN_ADMIN)

    assert brew.data == [{"n": counts[BREW]}]
    assert urban.data == [{"n": counts[URBAN]}]
    assert counts[BREW] != counts[URBAN] and counts[BREW] > 0 and counts[URBAN] > 0


@pytest.mark.parametrize(
    ("agent", "sql"),
    [
        ("sales_agent", "SELECT t.name AS outlet FROM outlets t ORDER BY t.name"),
        ("inventory_agent", "SELECT DISTINCT t.name AS outlet FROM inventory_levels l "
         "JOIN outlets t ON t.id = l.outlet_id ORDER BY outlet"),
        ("customer_agent", "SELECT DISTINCT t.name AS outlet FROM customer_visits v "
         "JOIN outlets t ON t.id = v.outlet_id ORDER BY outlet"),
    ],
)  # fmt: skip
async def test_each_agent_gives_a_limited_viewer_only_their_outlets(
    agent: str, sql: str, seeded: Stores, seeded_settings: Settings
) -> None:
    answer, _ = await ask(seeded, seeded_settings, agent, sql, email=VIEWER, summary="Two.")
    assert answer.data == [{"outlet": "Indiranagar"}, {"outlet": "Koramangala"}]
    assert answer.scope_note == "Limited to your outlets: Indiranagar, Koramangala."
    assert answer.summary == "Two. Limited to your outlets: Indiranagar, Koramangala."


@pytest.mark.parametrize("agent", list(AGENTS))
async def test_naming_the_other_company_in_the_query_gets_an_agent_nothing(
    agent: str, seeded: Stores, seeded_settings: Settings
) -> None:
    sql = "SELECT t.name AS outlet FROM outlets t WHERE t.name ILIKE '%pune%'"
    answer, _ = await ask(seeded, seeded_settings, agent, sql)  # Pune is UrbanMart's
    assert answer.status == "no_data" and answer.data == []


# -- SQL security -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent", "outside"),
    [
        ("sales_agent", "SELECT SUM(l.quantity_on_hand) AS stock FROM inventory_levels l"),
        ("inventory_agent", "SELECT COUNT(*) AS visits FROM customer_visits v"),
        ("customer_agent", "SELECT SUM(e.amount) AS spend FROM expenses e"),
    ],
)
async def test_an_agent_reaching_outside_its_tables_is_refused_and_it_is_logged(
    agent: str,
    outside: str,
    seeded: Stores,
    seeded_settings: Settings,
    owner: psycopg.Connection[Any],
) -> None:
    question = f"outside the area of {agent}"
    llm = FakeLLM().on("sql", SqlDraft(sql=outside))
    llm.on("sql_repair", SqlDraft(sql=outside), SqlDraft(sql=outside))
    answer, _ = await ask(seeded, seeded_settings, agent, None, llm=llm, question=question)

    assert answer.status == "failed" and answer.data == [] and answer.sql is None
    owner.rollback()
    logged = owner.execute(
        "SELECT status, reason FROM app.query_log WHERE question = %s", (question,)
    ).fetchall()
    assert [(r["status"], r["reason"]) for r in logged] == [("rejected", "table_not_allowed")] * 3


@pytest.mark.parametrize(
    "attack",
    [
        "DELETE FROM orders",
        "UPDATE inventory_levels SET quantity_on_hand = 0",
        "SELECT 1 AS one; DROP TABLE customer_visits",
        "WITH gone AS (DELETE FROM orders RETURNING id) SELECT COUNT(*) AS n FROM gone",
        "SELECT u.email AS email, u.password_hash AS hash FROM app.users u",
        "SELECT set_config('app.tenant_id', '00000000-0000-0000-0000-000000000000', false) AS s",
    ],
)
@pytest.mark.parametrize("agent", list(AGENTS))
async def test_unsafe_sql_from_any_agent_is_refused_and_changes_nothing(
    agent: str,
    attack: str,
    seeded: Stores,
    seeded_settings: Settings,
    owner: psycopg.Connection[Any],
) -> None:
    def counts() -> dict[str, Any]:
        owner.rollback()
        row = owner.execute(
            "SELECT (SELECT count(*) FROM biz.orders) AS orders, "
            "(SELECT sum(quantity_on_hand) FROM biz.inventory_levels) AS stock, "
            "(SELECT count(*) FROM biz.customer_visits) AS visits"
        ).fetchone()
        assert row is not None
        return dict(row)

    before = counts()
    llm = FakeLLM().on("sql", SqlDraft(sql=attack))
    llm.on("sql_repair", SqlDraft(sql=attack), SqlDraft(sql=attack))
    answer, _ = await ask(seeded, seeded_settings, agent, None, llm=llm)

    assert answer.status == "failed" and answer.data == [] and answer.sql is None
    assert attack not in answer.summary
    assert counts() == before


# -- the API, end to end --------------------------------------------------------------------


@pytest.fixture
def api(seeded_settings: Settings) -> Iterator[tuple[TestClient, FakeLLM]]:
    with TestClient(create_app(seeded_settings)) as client:
        llm = FakeLLM()
        client.app.state.llm = llm  # type: ignore[attr-defined]
        yield client, llm


def test_the_api_lists_the_agents_for_a_demo_user(api: tuple[TestClient, FakeLLM]) -> None:
    client, _ = api
    response = client.get("/api/v1/agents", headers={"X-Dev-User": VIEWER})
    assert response.status_code == 200
    assert [a["agent"] for a in response.json()] == list(AGENTS)


@pytest.mark.parametrize(
    ("agent", "sql"),
    [
        ("sales_agent", SALES_BY_OUTLET),
        ("inventory_agent", SHORTAGE_BY_OUTLET),
        ("customer_agent", VISITS_BY_OUTLET),
    ],
)
def test_the_api_answers_for_each_agent_with_the_sql_for_an_analyst_and_not_for_a_viewer(
    agent: str, sql: str, api: tuple[TestClient, FakeLLM]
) -> None:
    client, llm = api
    bodies = {}
    for email in (ANALYST, VIEWER):
        llm.on("sql", SqlDraft(sql=sql)).on("agent_report", AgentReport(summary="Ranked."))
        response = client.post(
            f"/api/v1/agents/{agent}/query",
            json={"question": "Compare the outlets."},
            headers={"X-Dev-User": email},
        )
        assert response.status_code == 200
        bodies[email] = response.json()

    analyst, viewer = bodies[ANALYST], bodies[VIEWER]
    assert analyst["agent"] == viewer["agent"] == agent
    assert analyst["status"] == viewer["status"] == "answered"
    assert analyst["sql"].startswith("SELECT t.name AS outlet") and viewer["sql"] is None
    # the viewer sees the same figures for their two outlets, and no other outlet
    mine = [r for r in analyst["data"] if r["outlet"] in ("Indiranagar", "Koramangala")]
    assert viewer["data"] == mine and mine
    assert viewer["scope_note"] == "Limited to your outlets: Indiranagar, Koramangala."


def test_the_api_refuses_an_unknown_user_and_an_unknown_agent(
    api: tuple[TestClient, FakeLLM],
) -> None:
    client, llm = api
    body = {"question": "What was revenue?"}
    unknown = client.post(
        "/api/v1/agents/sales_agent/query", json=body, headers={"X-Dev-User": "nobody@x.example"}
    )
    assert unknown.status_code == 401
    missing = client.post(
        "/api/v1/agents/finance_agent/query", json=body, headers={"X-Dev-User": ANALYST}
    )
    assert missing.status_code == 404
    assert llm.calls == []
