"""LIVE: the question pipeline against real PostgreSQL, with a scripted model.

Real: the guard, the read-only user, row security, the timeout, the row cap, the
query log, the API. Scripted: only what the model would have written. So these prove
that the SQL a model is expected to write returns the right figures, safely.

The figures are checked against values computed separately, as the database owner,
straight from the tables.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.agents.sql_analyst import SqlDraft
from app.agents.summarizer import Summary
from app.core.config import Settings
from app.llm.fake import FakeLLM
from app.main import create_app
from app.seed.generate import uid
from app.store.connections import Stores
from app.workflows.analytics import AnalyticsAnswer, answer_question
from tests.integration.test_schema_live import who

URL = "/api/v1/analytics/query"
BREW = uid("tenant", "brewbite")
URBAN = uid("tenant", "urbanmart")
ANALYST = "analyst@brewbite.example"

# What a model is expected to write for the example questions of the brief.
# "Today" is pinned to 2026-11-01, so last month is October 2026.
REVENUE_LAST_MONTH = (
    "SELECT ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue FROM orders o "
    "WHERE o.status = 'completed' AND o.order_date BETWEEN DATE '2026-10-01' AND DATE '2026-10-31'"
)
HIGHEST_SALES_PRODUCTS = (
    "SELECT p.name AS product, ROUND(SUM(i.line_total), 2) AS sales FROM order_items i "
    "JOIN orders o ON o.id = i.order_id JOIN products p ON p.id = i.product_id "
    "WHERE o.status = 'completed' GROUP BY p.name ORDER BY sales DESC LIMIT 5"
)
LOWEST_REVENUE_OUTLET = (
    "SELECT t.name AS outlet, ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue "
    "FROM orders o JOIN outlets t ON t.id = o.outlet_id WHERE o.status = 'completed' "
    "GROUP BY t.name ORDER BY revenue ASC LIMIT 1"
)
CUSTOMERS_VISITED_LAST_MONTH = (
    "SELECT COUNT(DISTINCT v.customer_id) AS customers FROM customer_visits v "
    "WHERE v.visit_date BETWEEN DATE '2026-10-01' AND DATE '2026-10-31'"
)
TOP_10_SELLING = (
    "SELECT p.name AS product, SUM(i.quantity) AS units_sold FROM order_items i "
    "JOIN orders o ON o.id = i.order_id JOIN products p ON p.id = i.product_id "
    "WHERE o.status = 'completed' GROUP BY p.name ORDER BY units_sold DESC, p.name LIMIT 10"
)


async def ask(
    stores: Stores,
    settings: Settings,
    sql: str | None,
    *,
    email: str = ANALYST,
    question: str = "a question",
    llm: FakeLLM | None = None,
    summary: str | None = None,
) -> tuple[AnalyticsAnswer, FakeLLM]:
    llm = llm or FakeLLM().on("sql", SqlDraft(sql=sql, explanation="because"))
    if summary is not None:
        llm.on("summary", Summary(text=summary))
    answer = await answer_question(
        question=question,
        caller=await who(stores, email),
        llm=llm,
        pg=stores.postgres,
        settings=settings,
    )
    return answer, llm


def expected(owner: psycopg.Connection[Any], sql: str, *params: Any) -> list[dict[str, Any]]:
    return [
        {k: float(v) if hasattr(v, "as_tuple") else v for k, v in row.items()}
        for row in owner.execute(sql, params).fetchall()  # type: ignore[arg-type]
    ]


# -- the example questions, against real data -----------------------------------------


async def test_total_revenue_last_month(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT round(sum(total_amount - tax_amount), 2) AS revenue FROM biz.orders "
        "WHERE tenant_id = %s AND status = 'completed' AND order_date >= '2026-10-01' "
        "AND order_date <= '2026-10-31'",
        BREW,
    )
    figure = want[0]["revenue"]
    answer, _ = await ask(
        seeded, seeded_settings, REVENUE_LAST_MONTH,
        question="What was our total revenue last month?",
        summary=f"Revenue last month was INR {figure:,.2f}.",
    )  # fmt: skip
    assert answer.status == "answered"
    assert answer.data == want and figure > 100_000
    assert answer.columns == [{"name": "revenue", "type": "money"}]
    assert answer.summary == f"Revenue last month was INR {figure:,.2f}."


async def test_products_with_the_highest_sales(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT p.name AS product, round(sum(i.line_total), 2) AS sales FROM biz.order_items i "
        "JOIN biz.orders o ON o.id = i.order_id JOIN biz.products p ON p.id = i.product_id "
        "WHERE i.tenant_id = %s AND o.status = 'completed' GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
        BREW,
    )
    answer, _ = await ask(seeded, seeded_settings, HIGHEST_SALES_PRODUCTS, summary="See the table.")
    assert answer.data == want and answer.row_count == 5
    assert [c["type"] for c in answer.columns] == ["text", "money"]


async def test_the_outlet_with_the_lowest_revenue(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT t.name AS outlet, round(sum(o.total_amount - o.tax_amount), 2) AS revenue "
        "FROM biz.orders o JOIN biz.outlets t ON t.id = o.outlet_id "
        "WHERE o.tenant_id = %s AND o.status = 'completed' GROUP BY 1 ORDER BY 2 LIMIT 1",
        BREW,
    )
    answer, _ = await ask(seeded, seeded_settings, LOWEST_REVENUE_OUTLET, summary="See the table.")
    assert answer.data == want
    assert answer.data[0]["outlet"] == "Chennai Adyar"  # the quietest outlet in the demo data


async def test_how_many_customers_visited(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT count(DISTINCT customer_id) AS customers FROM biz.customer_visits "
        "WHERE tenant_id = %s AND visit_date BETWEEN '2026-10-01' AND '2026-10-31'",
        BREW,
    )
    answer, _ = await ask(
        seeded, seeded_settings, CUSTOMERS_VISITED_LAST_MONTH,
        summary=f"{want[0]['customers']} customers visited.",
    )  # fmt: skip
    assert answer.data == want and 0 < want[0]["customers"] <= 300


async def test_the_top_10_selling_products(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    want = expected(
        owner,
        "SELECT p.name AS product, sum(i.quantity) AS units_sold FROM biz.order_items i "
        "JOIN biz.orders o ON o.id = i.order_id JOIN biz.products p ON p.id = i.product_id "
        "WHERE i.tenant_id = %s AND o.status = 'completed' "
        "GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10",
        BREW,
    )
    answer, _ = await ask(seeded, seeded_settings, TOP_10_SELLING, summary="The top 10 are listed.")
    assert answer.data == want and answer.row_count == 10
    assert answer.data[0]["units_sold"] >= answer.data[9]["units_sold"]


# -- tenant isolation through the whole pipeline ----------------------------------------


async def test_the_same_query_gives_each_company_its_own_figure(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    figures = {}
    for email, tenant in ((ANALYST, BREW), ("admin@urbanmart.example", URBAN)):
        answer, _ = await ask(
            seeded, seeded_settings, REVENUE_LAST_MONTH, email=email, summary="ok"
        )
        want = expected(
            owner,
            "SELECT round(sum(total_amount - tax_amount), 2) AS revenue FROM biz.orders "
            "WHERE tenant_id = %s AND status = 'completed' "
            "AND order_date BETWEEN '2026-10-01' AND '2026-10-31'",
            tenant,
        )
        assert answer.data == want
        figures[tenant] = answer.data[0]["revenue"]
    assert figures[BREW] != figures[URBAN]


async def test_a_product_both_companies_sell_is_never_mixed(
    seeded: Stores, seeded_settings: Settings
) -> None:
    sql = (
        "SELECT p.name AS product, p.unit_price AS price FROM products p "
        "WHERE p.name = 'Bottled Water'"
    )
    brew, _ = await ask(seeded, seeded_settings, sql, summary="ok")
    urban, _ = await ask(
        seeded, seeded_settings, sql, email="admin@urbanmart.example", summary="ok"
    )
    assert brew.data == [{"product": "Bottled Water", "price": 40.0}]
    assert urban.data == [{"product": "Bottled Water", "price": 20.0}]


async def test_a_query_that_names_another_company_gets_nothing(
    seeded: Stores, seeded_settings: Settings
) -> None:
    sql = f"SELECT COUNT(*) AS orders FROM orders o WHERE o.tenant_id = '{URBAN}'"
    llm = (
        FakeLLM()
        .on("sql", SqlDraft(sql=sql))
        .on("sql_repair", SqlDraft(sql=sql), SqlDraft(sql=sql))
    )
    answer, _ = await ask(seeded, seeded_settings, None, llm=llm)
    # tenant_id is not a column the model is told about, so the guard refuses it outright;
    # test_schema_live proves the database returns nothing even without the guard
    assert answer.status == "failed" and answer.data == []


async def test_a_limited_viewer_gets_only_their_outlets_and_is_told_so(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    by_outlet = (
        "SELECT t.name AS outlet, COUNT(*) AS orders FROM orders o "
        "JOIN outlets t ON t.id = o.outlet_id GROUP BY t.name ORDER BY t.name"
    )
    answer, _ = await ask(
        seeded, seeded_settings, by_outlet, email="viewer@brewbite.example", summary="Two outlets."
    )
    want = expected(
        owner,
        "SELECT t.name AS outlet, count(*) AS orders FROM biz.orders o JOIN biz.outlets t "
        "ON t.id = o.outlet_id WHERE o.tenant_id = %s AND t.name IN ('Indiranagar', 'Koramangala') "
        "GROUP BY 1 ORDER BY 1",
        BREW,
    )
    assert answer.data == want and len(want) == 2
    assert answer.scope_note == "Limited to your outlets: Indiranagar, Koramangala."
    assert answer.summary == "Two outlets. Limited to your outlets: Indiranagar, Koramangala."


# -- unsafe SQL is stopped, and the data is untouched --------------------------------------


@pytest.mark.parametrize(
    "attack",
    [
        "DELETE FROM orders",
        "UPDATE products SET unit_price = 0",
        "DROP TABLE orders",
        "TRUNCATE orders",
        "ALTER TABLE orders ADD COLUMN x int",
        "INSERT INTO categories (name) VALUES ('x')",
        "SELECT id FROM orders; DELETE FROM orders",
        "WITH gone AS (DELETE FROM orders RETURNING id) SELECT id FROM gone",
        "SELECT email FROM app.users",
        "SELECT c.phone FROM customers c",
        "SELECT set_config('app.tenant_id', '00000000-0000-0000-0000-000000000000', true)",
        "SELECT pg_sleep(60)",
    ],
)
async def test_an_unsafe_query_is_refused_logged_and_changes_nothing(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any], attack: str
) -> None:
    before = owner.execute(
        "SELECT (SELECT count(*) FROM biz.orders) AS orders, "
        "(SELECT sum(unit_price) FROM biz.products) AS prices, "
        "(SELECT count(*) FROM biz.categories) AS categories"
    ).fetchone()
    llm = (
        FakeLLM()
        .on("sql", SqlDraft(sql=attack))
        .on("sql_repair", SqlDraft(sql=attack), SqlDraft(sql=attack))
    )
    answer, _ = await ask(seeded, seeded_settings, None, llm=llm, question=f"attack: {attack}")

    assert answer.status == "failed" and answer.sql is None and answer.data == []
    owner.rollback()  # a fresh look at the data
    after = owner.execute(
        "SELECT (SELECT count(*) FROM biz.orders) AS orders, "
        "(SELECT sum(unit_price) FROM biz.products) AS prices, "
        "(SELECT count(*) FROM biz.categories) AS categories"
    ).fetchone()
    assert after == before
    logged = owner.execute(
        "SELECT status, sql FROM app.query_log WHERE question = %s", (f"attack: {attack}",)
    ).fetchall()
    assert [(r["status"], r["sql"]) for r in logged] == [("rejected", attack)] * 3


# -- timeouts and row limits -------------------------------------------------------------


async def test_a_query_past_its_time_limit_is_cancelled_and_reported(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    quick = seeded_settings.model_copy(update={"query_timeout_seconds": 1})
    stores = await Stores.open(quick)
    slow = "SELECT COUNT(*) AS n FROM generate_series(1, 400000000) AS g(x)"  # passes the guard
    try:
        answer, llm = await ask(stores, quick, slow, question="a slow one")
    finally:
        await stores.close()
    assert answer.status == "timeout" and answer.data == []
    assert "shorter period" in answer.summary
    assert len(llm.calls) == 1  # not retried
    owner.rollback()
    logged = owner.execute(
        "SELECT status FROM app.query_log WHERE question = 'a slow one'"
    ).fetchall()
    assert logged == [{"status": "timeout"}]


async def test_the_row_limit_cuts_the_result_and_says_so(
    seeded: Stores, seeded_settings: Settings
) -> None:
    few = seeded_settings.model_copy(update={"query_max_rows": 5})
    answer, _ = await ask(
        seeded, few, "SELECT o.order_no FROM orders o ORDER BY o.order_no", summary="Orders."
    )
    assert answer.row_count == 5 and len(answer.data) == 5
    assert answer.truncated is True
    assert answer.sql is not None and answer.sql.endswith("LIMIT 6")


async def test_a_result_within_the_limit_is_not_marked_as_cut(
    seeded: Stores, seeded_settings: Settings
) -> None:
    few = seeded_settings.model_copy(update={"query_max_rows": 5})
    answer, _ = await ask(seeded, few, "SELECT t.name FROM outlets t", summary="Five outlets.")
    assert answer.row_count == 5 and answer.truncated is False


async def test_a_limit_the_model_asked_for_is_kept_when_it_is_smaller(
    seeded: Stores, seeded_settings: Settings
) -> None:
    answer, _ = await ask(seeded, seeded_settings, TOP_10_SELLING, summary="The top 10.")
    assert answer.sql is not None and answer.sql.endswith("LIMIT 10")
    assert answer.truncated is False


# -- empty results and database errors ---------------------------------------------------


async def test_no_rows_is_said_clearly_and_the_model_is_not_asked_to_explain(
    seeded: Stores, seeded_settings: Settings
) -> None:
    sql = "SELECT o.order_no FROM orders o WHERE o.order_date = DATE '1999-01-01'"
    answer, llm = await ask(seeded, seeded_settings, sql)
    assert answer.status == "no_data" and answer.data == [] and answer.row_count == 0
    assert answer.summary == "No data matched this question."
    assert [c.task for c in llm.calls] == ["sql"]


async def test_a_database_error_is_fed_back_and_the_repaired_query_answers(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    broken = (
        "SELECT CAST(t.name AS INT) AS n FROM outlets t"  # passes the guard, fails in PostgreSQL
    )
    llm = FakeLLM().on("sql", SqlDraft(sql=broken))
    llm.on("sql_repair", SqlDraft(sql="SELECT COUNT(*) AS outlets FROM outlets t"))
    answer, _ = await ask(
        seeded, seeded_settings, None, llm=llm, question="a broken one", summary="5 outlets."
    )
    assert answer.status == "answered" and answer.data == [{"outlets": 5}]
    repair = llm.calls_for("sql_repair")[0].user
    assert "PostgreSQL said: invalid input syntax for type integer" in repair
    owner.rollback()
    logged = owner.execute(
        "SELECT status, reason FROM app.query_log WHERE question = 'a broken one' "
        "ORDER BY created_at"
    ).fetchall()
    assert logged == [{"status": "error", "reason": "22P02"}, {"status": "ok", "reason": None}]


async def test_a_database_error_never_reaches_the_user(
    seeded: Stores, seeded_settings: Settings
) -> None:
    broken = "SELECT CAST(t.name AS INT) AS n FROM outlets t"
    llm = FakeLLM().on("sql", SqlDraft(sql=broken))
    llm.on("sql_repair", SqlDraft(sql=broken), SqlDraft(sql=broken))
    answer, _ = await ask(seeded, seeded_settings, None, llm=llm)
    assert answer.status == "failed"
    assert "invalid input syntax" not in answer.summary and "Indiranagar" not in answer.summary


async def test_every_run_is_in_the_query_log_with_its_company_and_user(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    await ask(seeded, seeded_settings, REVENUE_LAST_MONTH, question="logged?", summary="ok")
    owner.rollback()
    row = owner.execute(
        "SELECT q.tenant_id, u.email, q.status, q.row_count, q.sql FROM app.query_log q "
        "JOIN app.users u ON u.id = q.user_id WHERE q.question = 'logged?'"
    ).fetchone()
    assert row is not None
    assert (row["tenant_id"], row["email"], row["status"], row["row_count"]) == (
        BREW, ANALYST, "ok", 1,
    )  # fmt: skip
    assert row["sql"].startswith("SELECT ROUND(SUM(")


# -- the API, end to end ------------------------------------------------------------------


@pytest.fixture
def api(seeded_settings: Settings) -> Iterator[tuple[TestClient, FakeLLM]]:
    with TestClient(create_app(seeded_settings)) as client:
        llm = FakeLLM()
        client.app.state.llm = llm  # type: ignore[attr-defined]
        yield client, llm


def test_the_api_answers_an_analyst_with_the_sql(
    api: tuple[TestClient, FakeLLM], owner: psycopg.Connection[Any]
) -> None:
    client, llm = api
    want = expected(
        owner,
        "SELECT round(sum(total_amount - tax_amount), 2) AS revenue FROM biz.orders "
        "WHERE tenant_id = %s AND status = 'completed' "
        "AND order_date BETWEEN '2026-10-01' AND '2026-10-31'",
        BREW,
    )
    llm.on("sql", SqlDraft(sql=REVENUE_LAST_MONTH, assumptions=["Sales means revenue."]))
    llm.on("summary", Summary(text=f"Revenue last month was INR {want[0]['revenue']:,.2f}."))
    response = client.post(
        URL,
        json={"question": "What was our total revenue last month?"},
        headers={"X-Dev-User": ANALYST},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered" and body["data"] == want
    assert body["sql"].startswith("SELECT ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue")
    assert body["summary"].startswith("Revenue last month was INR ")
    # the model was told the date range of "last month", and only the orders table
    prompt = llm.calls[0].user
    assert "- last month: 2026-10-01 to 2026-10-31" in prompt
    assert "TABLE orders:" in prompt and "TABLE products" not in prompt


def test_the_api_answers_a_viewer_without_the_sql_and_within_their_outlets(
    api: tuple[TestClient, FakeLLM],
) -> None:
    client, llm = api
    llm.on("sql", SqlDraft(sql="SELECT COUNT(DISTINCT o.outlet_id) AS outlets FROM orders o"))
    llm.on("summary", Summary(text="Orders came from 2 outlets."))
    body = client.post(
        URL, json={"question": "How many outlets took orders?"},
        headers={"X-Dev-User": "viewer@brewbite.example"},
    ).json()  # fmt: skip
    assert body["data"] == [{"outlets": 2}]
    assert body["sql"] is None
    assert body["scope_note"] == "Limited to your outlets: Indiranagar, Koramangala."


def test_the_api_refuses_an_unknown_user_and_the_platform_admin(
    api: tuple[TestClient, FakeLLM],
) -> None:
    client, llm = api
    for email in ("stranger@example.com", "root@bi.example"):  # the platform admin has no company
        response = client.post(URL, json={"question": "revenue?"}, headers={"X-Dev-User": email})
        assert response.status_code == 401
    assert client.post(URL, json={"question": "revenue?"}).status_code == 401
    assert llm.calls == []
