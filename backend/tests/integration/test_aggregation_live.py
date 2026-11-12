"""LIVE: the aggregation check against real PostgreSQL, with a scripted model.

Three things are proved here that the unit tests cannot:
1. the unique keys in tables.yaml are the database's own constraints;
2. every join the check refuses really gives a wrong figure on real data, and every
   join it passes really gives the right one (the figures are computed separately,
   as the database owner, straight from one table);
3. in the pipeline, a refused query never reaches the database, goes back to the
   model, and the repaired query answers, for the same caller and in the query log.

"Today" is pinned to 2026-11-01, so last month is October 2026.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from app.agents.sql_analyst import FAILURE_MESSAGES, SqlDraft
from app.core.config import Settings
from app.llm.fake import FakeLLM
from app.semantic.model import load_vocabulary
from app.sqlguard import Catalog, Checked, Passed, Refused, check, check_aggregation
from app.store.connections import Stores
from tests.integration.test_analytics_live import ANALYST, BREW, ask, expected
from tests.integration.test_schema_live import rows, who

VOCABULARY = load_vocabulary()
CATALOG = Catalog(VOCABULARY.catalog())
UNIQUE = VOCABULARY.unique_keys()
VIEWER = "viewer@brewbite.example"  # limited to Indiranagar and Koramangala
OCT = "BETWEEN DATE '2026-10-01' AND DATE '2026-10-31'"


def number(value: Any) -> float:
    return round(float(value or 0), 2)


def verdict(sql: str) -> Passed | Refused:
    guarded = check(sql, CATALOG)
    assert isinstance(guarded, Checked), guarded
    return check_aggregation(guarded.sql, CATALOG, UNIQUE)


# -- 1. the metadata is the database's ----------------------------------------------------


def test_the_unique_keys_in_the_vocabulary_are_the_databases_own_constraints(
    owner: psycopg.Connection[Any],
) -> None:
    found = owner.execute(
        "SELECT c.conrelid::regclass::text AS tbl, array_agg(a.attname::text) AS cols "
        "FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid "
        "AND a.attnum = ANY (c.conkey) WHERE c.connamespace = 'biz'::regnamespace "
        "AND c.contype IN ('p', 'u') GROUP BY c.oid, c.conrelid"
    ).fetchall()
    in_database: dict[str, set[frozenset[str]]] = {}
    for row in found:
        # row security pins every query to one company, so tenant_id adds nothing to a key
        key = frozenset(row["cols"]) - {"tenant_id"}
        in_database.setdefault(row["tbl"].removeprefix("biz."), set()).add(key)

    for table in VOCABULARY.tables:
        listed = set(UNIQUE[table.table])
        assert listed, f"{table.table} lists no unique key"
        # every key the check trusts is a real constraint (a column the model cannot
        # read, such as none today, would simply be absent from the vocabulary)
        assert listed <= in_database[table.table], table.table
        readable = {c.name for c in table.columns}
        assert listed == {k for k in in_database[table.table] if k <= readable}, table.table


# -- 2. what is refused is wrong, what is passed is right -------------------------------------

# name -> (the join as a model might write it, the same figure from one table as the owner)
WRONG = {
    "order totals across order lines": (
        "SELECT SUM(o.total_amount - o.tax_amount) AS v FROM orders o "
        f"JOIN order_items oi ON oi.order_id = o.id WHERE o.status = 'completed' "
        f"AND o.order_date {OCT}",
        "SELECT SUM(total_amount - tax_amount) AS v FROM biz.orders WHERE tenant_id = %s "
        f"AND status = 'completed' AND order_date {OCT}",
    ),
    "average order value across order lines": (
        "SELECT AVG(o.total_amount) AS v FROM orders o JOIN order_items oi ON oi.order_id = o.id "
        f"WHERE o.order_date {OCT}",
        f"SELECT AVG(total_amount) AS v FROM biz.orders WHERE tenant_id = %s AND order_date {OCT}",
    ),
    "orders counted across order lines": (
        "SELECT COUNT(o.id) AS v FROM orders o JOIN order_items oi ON oi.order_id = o.id "
        f"WHERE o.order_date {OCT}",
        f"SELECT COUNT(id) AS v FROM biz.orders WHERE tenant_id = %s AND order_date {OCT}",
    ),
    "revenue with visits joined on the outlet": (
        "SELECT SUM(o.total_amount - o.tax_amount) AS v FROM orders o "
        "JOIN customer_visits v ON v.outlet_id = o.outlet_id WHERE o.status = 'completed' "
        f"AND o.order_date {OCT} AND v.visit_date {OCT}",
        "SELECT SUM(total_amount - tax_amount) AS v FROM biz.orders WHERE tenant_id = %s "
        f"AND status = 'completed' AND order_date {OCT}",
    ),
    "revenue with visits joined on the customer": (
        "SELECT SUM(o.total_amount - o.tax_amount) AS v FROM orders o "
        "JOIN customer_visits v ON v.customer_id = o.customer_id WHERE o.status = 'completed' "
        f"AND o.order_date {OCT} AND v.visit_date {OCT}",
        "SELECT SUM(total_amount - tax_amount) AS v FROM biz.orders WHERE tenant_id = %s "
        f"AND status = 'completed' AND order_date {OCT}",
    ),
    "visits of customers joined to their orders too": (
        "SELECT COUNT(v.id) AS v FROM customers c JOIN customer_visits v ON v.customer_id = c.id "
        f"JOIN orders o ON o.customer_id = c.id WHERE v.visit_date {OCT} AND o.order_date {OCT}",
        f"SELECT COUNT(id) AS v FROM biz.customer_visits WHERE tenant_id = %s AND visit_date {OCT}",
    ),
    "purchase totals across purchase lines": (
        "SELECT SUM(po.total_amount) AS v FROM purchase_orders po "
        "JOIN purchase_order_items i ON i.purchase_order_id = po.id WHERE po.status = 'received'",
        "SELECT SUM(total_amount) AS v FROM biz.purchase_orders WHERE tenant_id = %s "
        "AND status = 'received'",
    ),
    "expenses joined to orders on the outlet": (
        "SELECT SUM(e.amount) AS v FROM expenses e JOIN orders o ON o.outlet_id = e.outlet_id "
        f"WHERE e.expense_date {OCT} AND o.order_date {OCT}",
        f"SELECT SUM(amount) AS v FROM biz.expenses WHERE tenant_id = %s AND expense_date {OCT}",
    ),
    "stock units across order lines": (
        "SELECT SUM(il.quantity_on_hand) AS v FROM inventory_levels il "
        "JOIN order_items oi ON oi.outlet_id = il.outlet_id AND oi.product_id = il.product_id",
        "SELECT SUM(quantity_on_hand) AS v FROM biz.inventory_levels WHERE tenant_id = %s",
    ),
    "all sales joined to stock on the product only": (
        "SELECT SUM(oi.line_total) AS v FROM order_items oi "
        "JOIN inventory_levels il ON il.product_id = oi.product_id",
        "SELECT SUM(line_total) AS v FROM biz.order_items WHERE tenant_id = %s",
    ),
}


@pytest.mark.parametrize("case", WRONG.values(), ids=WRONG.keys())
async def test_every_join_the_check_refuses_gives_a_wrong_figure_on_real_data(
    seeded: Stores, owner: psycopg.Connection[Any], case: tuple[str, str]
) -> None:
    joined, truth = case
    assert isinstance(verdict(joined), Refused)
    got = (await rows(seeded, await who(seeded, ANALYST), joined))[0]["v"]
    want = expected(owner, truth, BREW)[0]["v"]
    assert number(want) > 0
    assert number(got) != number(want), "the join did not distort the figure after all"


RIGHT = {
    "order lines joined to order, product, category, outlet": (
        "SELECT SUM(oi.line_total) AS v FROM order_items oi JOIN orders o ON o.id = oi.order_id "
        "JOIN products p ON p.id = oi.product_id JOIN categories c ON c.id = p.category_id "
        f"JOIN outlets t ON t.id = oi.outlet_id WHERE oi.order_date {OCT}",
        "SELECT SUM(line_total) AS v FROM biz.order_items WHERE tenant_id = %s "
        f"AND order_date {OCT}",
    ),
    "orders joined to outlet and customer": (
        "SELECT SUM(o.total_amount - o.tax_amount) AS v FROM orders o "
        "JOIN outlets t ON t.id = o.outlet_id LEFT JOIN customers c ON c.id = o.customer_id "
        f"WHERE o.status = 'completed' AND o.order_date {OCT}",
        "SELECT SUM(total_amount - tax_amount) AS v FROM biz.orders WHERE tenant_id = %s "
        f"AND status = 'completed' AND order_date {OCT}",
    ),
    "orders left-joined to visits on the order id": (
        "SELECT SUM(o.total_amount - o.tax_amount) AS v FROM orders o "
        "LEFT JOIN customer_visits v ON v.order_id = o.id "
        f"WHERE o.status = 'completed' AND o.order_date {OCT}",
        "SELECT SUM(total_amount - tax_amount) AS v FROM biz.orders WHERE tenant_id = %s "
        f"AND status = 'completed' AND order_date {OCT}",
    ),
    "order lines joined to stock on the whole key": (
        "SELECT SUM(oi.line_total) AS v FROM order_items oi JOIN inventory_levels il "
        "ON il.outlet_id = oi.outlet_id AND il.product_id = oi.product_id "
        "WHERE il.quantity_on_hand <= il.reorder_level",
        "SELECT SUM(i.line_total) AS v FROM biz.order_items i WHERE i.tenant_id = %s AND EXISTS ("
        "SELECT 1 FROM biz.inventory_levels l WHERE l.outlet_id = i.outlet_id "
        "AND l.product_id = i.product_id AND l.quantity_on_hand <= l.reorder_level)",
    ),
    "purchase orders joined to supplier and outlet": (
        "SELECT SUM(po.total_amount) AS v FROM purchase_orders po "
        "JOIN suppliers s ON s.id = po.supplier_id JOIN outlets t ON t.id = po.outlet_id "
        "WHERE po.status = 'received'",
        "SELECT SUM(total_amount) AS v FROM biz.purchase_orders WHERE tenant_id = %s "
        "AND status = 'received'",
    ),
    "purchase lines joined to their order and product": (
        "SELECT SUM(i.line_total) AS v FROM purchase_order_items i "
        "JOIN purchase_orders po ON po.id = i.purchase_order_id "
        "JOIN products p ON p.id = i.product_id",
        "SELECT SUM(line_total) AS v FROM biz.purchase_order_items WHERE tenant_id = %s",
    ),
    "expenses joined to category and outlet": (
        "SELECT SUM(e.amount) AS v FROM expenses e "
        "JOIN expense_categories c ON c.id = e.category_id JOIN outlets t ON t.id = e.outlet_id "
        f"WHERE e.expense_date {OCT}",
        f"SELECT SUM(amount) AS v FROM biz.expenses WHERE tenant_id = %s AND expense_date {OCT}",
    ),
    "stock joined to product and outlet": (
        "SELECT SUM(il.quantity_on_hand) AS v FROM inventory_levels il "
        "JOIN products p ON p.id = il.product_id JOIN outlets t ON t.id = il.outlet_id",
        "SELECT SUM(quantity_on_hand) AS v FROM biz.inventory_levels WHERE tenant_id = %s",
    ),
    "visits joined to customer and outlet": (
        "SELECT COUNT(v.id) AS v FROM customer_visits v JOIN customers c ON c.id = v.customer_id "
        f"JOIN outlets t ON t.id = v.outlet_id WHERE v.visit_date {OCT}",
        f"SELECT COUNT(id) AS v FROM biz.customer_visits WHERE tenant_id = %s AND visit_date {OCT}",
    ),
}


@pytest.mark.parametrize("case", RIGHT.values(), ids=RIGHT.keys())
async def test_every_join_the_check_calls_sound_gives_the_right_figure_on_real_data(
    seeded: Stores, owner: psycopg.Connection[Any], case: tuple[str, str]
) -> None:
    joined, truth = case
    result = verdict(joined)
    assert isinstance(result, Passed) and result.uncertain == ()
    got = (await rows(seeded, await who(seeded, ANALYST), joined))[0]["v"]
    want = expected(owner, truth, BREW)[0]["v"]
    assert number(want) > 0 and number(got) == number(want)


# -- 3. in the pipeline -------------------------------------------------------------------

REVENUE_ACROSS_LINES = (
    "SELECT ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue FROM orders o "
    "JOIN order_items oi ON oi.order_id = o.id JOIN inventory_levels il "
    "ON il.outlet_id = oi.outlet_id AND il.product_id = oi.product_id "
    "WHERE o.status = 'completed' AND il.quantity_on_hand <= il.reorder_level"
)
REVENUE_BY_LINES = (
    "SELECT ROUND(SUM(oi.line_total), 2) AS revenue FROM order_items oi "
    "JOIN orders o ON o.id = oi.order_id JOIN inventory_levels il "
    "ON il.outlet_id = oi.outlet_id AND il.product_id = oi.product_id "
    "WHERE o.status = 'completed' AND il.quantity_on_hand <= il.reorder_level"
)
LOW_STOCK_REVENUE = (
    "SELECT ROUND(SUM(i.line_total), 2) AS revenue FROM biz.order_items i "
    "JOIN biz.orders o ON o.id = i.order_id WHERE i.tenant_id = %s AND o.status = 'completed' "
    "AND EXISTS (SELECT 1 FROM biz.inventory_levels l WHERE l.outlet_id = i.outlet_id "
    "AND l.product_id = i.product_id AND l.quantity_on_hand <= l.reorder_level)"
)
OUTLET_REVENUE_AND_VISITORS_JOINED = (
    "SELECT t.name AS outlet, ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue, "
    "COUNT(DISTINCT v.customer_id) AS customers FROM outlets t "
    "JOIN orders o ON o.outlet_id = t.id JOIN customer_visits v ON v.outlet_id = t.id "
    f"WHERE o.status = 'completed' AND o.order_date {OCT} AND v.visit_date {OCT} "
    "GROUP BY t.name ORDER BY revenue DESC"
)
OUTLET_REVENUE_AND_VISITORS_SEPARATELY = (
    "WITH revenue AS (SELECT o.outlet_id, SUM(o.total_amount - o.tax_amount) AS revenue "
    f"FROM orders o WHERE o.status = 'completed' AND o.order_date {OCT} GROUP BY o.outlet_id), "
    "visits AS (SELECT v.outlet_id, COUNT(DISTINCT v.customer_id) AS customers "
    f"FROM customer_visits v WHERE v.visit_date {OCT} GROUP BY v.outlet_id) "
    "SELECT t.name AS outlet, ROUND(r.revenue, 2) AS revenue, v.customers FROM outlets t "
    "LEFT JOIN revenue r ON r.outlet_id = t.id LEFT JOIN visits v ON v.outlet_id = t.id "
    "ORDER BY t.name"
)


def logged(owner: psycopg.Connection[Any], question: str) -> list[tuple[str, str | None]]:
    owner.rollback()  # a fresh look
    found = owner.execute(
        "SELECT status, reason FROM app.query_log WHERE question = %s ORDER BY created_at",
        (question,),
    ).fetchall()
    return [(r["status"], r["reason"]) for r in found]


async def test_a_wrong_sum_is_refused_before_the_database_and_the_repair_answers_correctly(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    question = "How much revenue comes from the products that are currently low on stock?"
    llm = FakeLLM().on("sql", SqlDraft(sql=REVENUE_ACROSS_LINES))
    llm.on("sql_repair", SqlDraft(sql=REVENUE_BY_LINES))
    want = expected(owner, LOW_STOCK_REVENUE, BREW)
    answer, _ = await ask(
        seeded, seeded_settings, None, llm=llm, question=question,
        summary=f"Revenue was {want[0]['revenue']:.2f}.",
    )  # fmt: skip

    assert answer.status == "answered" and answer.data == want and want[0]["revenue"] > 0
    assert answer.sql is not None and "line_total" in answer.sql
    assert logged(owner, question) == [("rejected", "unsafe_aggregation"), ("ok", None)]

    repair = llm.calls_for("sql_repair")[0].user
    assert "orders is joined to inventory_levels, order_items in a way" in repair
    assert f"<<<QUESTION\n{question}\nQUESTION>>>" in repair
    # what the refused query would have said, had it run: a different, larger figure
    wrong = (await rows(seeded, await who(seeded, ANALYST), REVENUE_ACROSS_LINES))[0]["revenue"]
    assert number(wrong) > want[0]["revenue"]


async def test_three_wrong_sums_end_in_the_usual_failure_and_no_figure(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    question = "three fan-outs in a row"
    llm = FakeLLM().on("sql", SqlDraft(sql=REVENUE_ACROSS_LINES))
    llm.on("sql_repair", SqlDraft(sql=REVENUE_ACROSS_LINES), SqlDraft(sql=REVENUE_ACROSS_LINES))
    answer, _ = await ask(seeded, seeded_settings, None, llm=llm, question=question)

    assert answer.status == "failed" and answer.sql is None and answer.data == []
    assert answer.summary.startswith(FAILURE_MESSAGES["failed"])
    assert logged(owner, question) == [("rejected", "unsafe_aggregation")] * 3
    assert len(llm.calls) == 3  # no summary was asked for: there is nothing to explain


async def test_a_sound_join_runs_first_time_as_before(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    question = "a sound join"
    want = expected(owner, LOW_STOCK_REVENUE, BREW)
    answer, llm = await ask(
        seeded, seeded_settings, REVENUE_BY_LINES, question=question,
        summary=f"Revenue was {want[0]['revenue']:.2f}.",
    )  # fmt: skip
    assert answer.status == "answered" and answer.data == want
    assert logged(owner, question) == [("ok", None)]
    assert llm.calls_for("sql_repair") == []


async def test_the_repair_runs_for_the_same_viewer_inside_the_same_outlets(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    question = "Which outlet had the highest revenue last month and how many customers visited?"
    llm = FakeLLM().on("sql", SqlDraft(sql=OUTLET_REVENUE_AND_VISITORS_JOINED))
    llm.on("sql_repair", SqlDraft(sql=OUTLET_REVENUE_AND_VISITORS_SEPARATELY))
    want = expected(
        owner,
        "SELECT t.name AS outlet, (SELECT ROUND(SUM(o.total_amount - o.tax_amount), 2) "
        f"FROM biz.orders o WHERE o.outlet_id = t.id AND o.status = 'completed' "
        f"AND o.order_date {OCT}) AS revenue, (SELECT COUNT(DISTINCT v.customer_id) "
        f"FROM biz.customer_visits v WHERE v.outlet_id = t.id AND v.visit_date {OCT}) AS customers "
        "FROM biz.outlets t WHERE t.tenant_id = %s AND t.name IN ('Indiranagar', 'Koramangala') "
        "ORDER BY t.name",
        BREW,
    )
    figures = " ".join(f"{r['outlet']} {r['revenue']:.2f} {r['customers']}" for r in want)
    answer, _ = await ask(
        seeded, seeded_settings, None, llm=llm, question=question, email=VIEWER, summary=figures
    )

    assert answer.status == "answered" and answer.data == want and len(want) == 2
    assert answer.scope_note == "Limited to your outlets: Indiranagar, Koramangala."
    assert logged(owner, question) == [("rejected", "unsafe_aggregation"), ("ok", None)]
    owner.rollback()
    who_asked = owner.execute(
        "SELECT DISTINCT u.email, q.tenant_id FROM app.query_log q "
        "JOIN app.users u ON u.id = q.user_id WHERE q.question = %s",
        (question,),
    ).fetchall()
    assert who_asked == [{"email": VIEWER, "tenant_id": BREW}]


async def test_the_refused_query_is_in_the_log_as_the_guard_left_it(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    question = "what is logged for a fan-out"
    llm = FakeLLM().on("sql", SqlDraft(sql=REVENUE_ACROSS_LINES))
    llm.on("sql_repair", SqlDraft(sql=REVENUE_BY_LINES))
    await ask(seeded, seeded_settings, None, llm=llm, question=question, summary="ok")
    owner.rollback()
    found = owner.execute(
        "SELECT status, sql, row_count FROM app.query_log WHERE question = %s ORDER BY created_at",
        (question,),
    ).fetchall()
    assert [r["status"] for r in found] == ["rejected", "ok"]
    assert "SUM(o.total_amount - o.tax_amount)" in found[0]["sql"] and found[0]["row_count"] is None
    assert "SUM(oi.line_total)" in found[1]["sql"] and found[1]["row_count"] == 1


async def test_an_attack_is_still_refused_by_the_security_guard_not_by_this_check(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    question = "attack with a fan-out"
    attack = (
        "SELECT SUM(o.total_amount) AS v FROM orders o JOIN order_items oi ON oi.order_id = o.id; "
        "DELETE FROM orders"
    )
    llm = FakeLLM().on("sql", SqlDraft(sql=attack))
    llm.on("sql_repair", SqlDraft(sql=attack), SqlDraft(sql=attack))
    answer, _ = await ask(seeded, seeded_settings, None, llm=llm, question=question)
    assert answer.status == "failed"
    assert logged(owner, question) == [("rejected", "multiple_statements")] * 3
