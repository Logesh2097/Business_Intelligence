"""LIVE: the correctness check against real PostgreSQL, with a scripted model.

What the unit tests cannot show:
1. the metadata the check stands on is the database's own (optional columns, the
   one-to-one link, the status values, the sales columns);
2. every query the check refuses really gives a wrong figure on real data, and every
   form it accepts really gives the right one. The reference figures are computed
   separately, as the database owner;
3. a note says what the join really leaves out;
4. in the pipeline a refused query never reaches the database, is repaired for the
   same caller inside the same outlets, and the notes reach the API response.

"Today" is pinned to 2026-11-01, so last month is October 2026. No model is called.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.agents.sql_analyst import FAILURE_MESSAGES, SqlDraft
from app.agents.summarizer import Summary
from app.core.config import Settings
from app.llm.fake import FakeLLM
from app.main import create_app
from app.semantic.model import load_vocabulary
from app.sqlguard import (
    Catalog,
    Checked,
    Facts,
    Refused,
    Reviewed,
    check,
    check_aggregation,
    check_correctness,
)
from app.store.connections import Stores
from tests.integration.test_analytics_live import ANALYST, BREW, URL, ask
from tests.integration.test_schema_live import rows, who

VOCABULARY = load_vocabulary()
CATALOG = Catalog(VOCABULARY.catalog())
FACTS = Facts(**VOCABULARY.correctness_facts())
VIEWER = "viewer@brewbite.example"  # limited to Indiranagar and Koramangala
OCT = "BETWEEN DATE '2026-10-01' AND DATE '2026-10-31'"
DONE = "o.status = 'completed'"


def verdict(sql: str) -> Reviewed | Refused:
    """What the correctness check says, after the two earlier locks have passed it."""
    guarded = check(sql, CATALOG)
    assert isinstance(guarded, Checked), guarded
    assert not isinstance(
        check_aggregation(guarded.sql, CATALOG, VOCABULARY.unique_keys()), Refused
    )
    return check_correctness(guarded.sql, CATALOG, FACTS)


def number(value: Any) -> float:
    return round(float(value or 0), 2)


def one(owner: psycopg.Connection[Any], sql: str, *params: Any) -> float:
    row = owner.execute(sql, params or (BREW,)).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return number(next(iter(row.values())))


async def got(stores: Stores, sql: str, email: str = ANALYST) -> float:
    """The figure a query returns as the read-only user under row security."""
    return number((await rows(stores, await who(stores, email), sql))[0]["v"])


# -- 1. the metadata is the database's ----------------------------------------------------


def test_every_column_marked_optional_may_really_be_empty_and_no_other_join_column_may(
    owner: psycopg.Connection[Any],
) -> None:
    nullable = {
        (r["tbl"], r["col"])
        for r in owner.execute(
            "SELECT c.relname AS tbl, a.attname AS col FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'biz' AND c.relkind = 'r' AND a.attnum > 0 "
            "AND NOT a.attisdropped AND NOT a.attnotnull"
        ).fetchall()
    }
    marked = {(t.table, o.column) for t in VOCABULARY.tables for o in t.optional}
    assert marked == {("orders", "customer_id"), ("customer_visits", "order_id")}
    assert marked <= nullable
    # and no column the vocabulary joins on is empty-able without being marked
    joined_on = {(t.table, j.column) for t in VOCABULARY.tables for j in t.joins}
    assert (joined_on & nullable) == marked


def test_the_one_to_one_link_is_unique_in_the_database_and_in_the_data(
    owner: psycopg.Connection[Any],
) -> None:
    """customer_visits.order_id: nullable, and unique per company. Only then is N2 said."""
    constraints = owner.execute(
        "SELECT array_agg(a.attname::text ORDER BY a.attname) AS cols FROM pg_constraint c "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
        "WHERE c.conrelid = 'biz.customer_visits'::regclass AND c.contype IN ('p', 'u') "
        "GROUP BY c.oid"
    ).fetchall()
    assert ["order_id", "tenant_id"] in [r["cols"] for r in constraints]  # tenant-scoped
    repeated = owner.execute(
        "SELECT count(*) AS n FROM (SELECT tenant_id, order_id FROM biz.customer_visits "
        "WHERE order_id IS NOT NULL GROUP BY 1, 2 HAVING count(*) > 1) x"
    ).fetchone()
    assert repeated == {"n": 0}
    empty = owner.execute(
        "SELECT count(*) AS n FROM biz.customer_visits WHERE order_id IS NULL"
    ).fetchone()
    assert empty is not None and empty["n"] > 0  # it really is optional in the data

    visits = VOCABULARY.table("customer_visits")
    assert ("order_id",) in visits.unique and visits.optional[0].other_side_note
    # the other optional column is NOT unique, so it must carry no one-to-one note
    orders = VOCABULARY.table("orders")
    assert ("customer_id",) not in orders.unique and orders.optional[0].other_side_note == ""
    several = owner.execute(
        "SELECT count(*) AS n FROM (SELECT customer_id FROM biz.orders "
        "WHERE customer_id IS NOT NULL GROUP BY tenant_id, customer_id HAVING count(*) > 1) x"
    ).fetchone()
    assert several is not None and several["n"] > 0


def test_the_status_values_and_sales_columns_are_the_databases(
    owner: psycopg.Connection[Any],
) -> None:
    check_clause = owner.execute(
        "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = 'biz.orders'::regclass AND contype = 'c' "
        "AND pg_get_constraintdef(oid) LIKE '%%status%%'"
    ).fetchone()
    assert check_clause is not None
    for value in FACTS.status_values:
        assert f"'{value}'" in check_clause["def"]
    in_data = {
        r["status"] for r in owner.execute("SELECT DISTINCT status FROM biz.orders").fetchall()
    }
    assert in_data == set(FACTS.status_values)  # nothing is invented, nothing is missing

    types = {
        (r["table_name"], r["column_name"]): r["data_type"]
        for r in owner.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'biz' AND table_name IN ('orders', 'order_items')"
        ).fetchall()
    }
    for table, columns in FACTS.sales.items():
        for column in columns:
            assert types[(table, column)] == "numeric", (table, column)
    assert types[("orders", "ordered_at")] == "timestamp with time zone"
    assert types[("orders", "order_date")] == "date"


# -- 2. P1.3: cancelled and refunded lines cannot slip into a completed-orders figure ------


@pytest.fixture
def lines(owner: psycopg.Connection[Any]) -> dict[str, float]:
    """October order-line totals by the status of their order, computed as the owner."""

    def of(statuses: str) -> float:
        return one(
            owner,
            "SELECT sum(i.line_total) FROM biz.order_items i WHERE i.tenant_id = %s "
            f"AND i.order_date {OCT} AND i.order_id IN (SELECT o.id FROM biz.orders o "
            f"WHERE o.status IN ({statuses}))",
        )

    found = {
        "all": one(
            owner,
            "SELECT sum(line_total) FROM biz.order_items WHERE tenant_id = %s "
            f"AND order_date {OCT}",
        ),
        "completed": of("'completed'"),
        "refunded": of("'refunded'"),
        "not_completed": of("'cancelled', 'refunded'"),
    }
    # the data really holds what must be kept out
    assert found["not_completed"] > 0 and found["refunded"] > 0
    assert number(found["completed"] + found["not_completed"]) == found["all"]
    return found


LINE = "SELECT SUM(i.line_total) AS v FROM order_items i "
PERIOD = f"i.order_date {OCT}"
NOT_LIMITED = {
    "order lines alone": LINE + f"WHERE {PERIOD}",
    "LEFT JOIN orders with the status in ON": (
        LINE + f"LEFT JOIN orders o ON o.id = i.order_id AND {DONE} WHERE {PERIOD}"
    ),
    "orders RIGHT JOIN lines with the status in ON": (
        f"SELECT SUM(i.line_total) AS v FROM orders o RIGHT JOIN order_items i "
        f"ON i.order_id = o.id AND {DONE} WHERE {PERIOD}"
    ),
    "FULL JOIN with the status in ON": (
        LINE + f"FULL JOIN orders o ON o.id = i.order_id AND {DONE} WHERE {PERIOD}"
    ),
    "an OR that lets other rows through": (
        LINE + f"JOIN orders o ON o.id = i.order_id WHERE {PERIOD} "
        f"AND ({DONE} OR o.channel IN ('dine_in', 'takeaway', 'delivery', 'online'))"
    ),
    "EXISTS over orders that are not these lines' orders": (
        LINE + f"WHERE {PERIOD} AND EXISTS (SELECT 1 FROM orders o WHERE {DONE})"
    ),
}


@pytest.mark.parametrize("sql", NOT_LIMITED.values(), ids=NOT_LIMITED.keys())
async def test_a_refused_form_really_adds_up_cancelled_and_refunded_order_lines(
    seeded: Stores, lines: dict[str, float], sql: str
) -> None:
    result = verdict(sql)
    assert isinstance(result, Refused) and result.code == "missing_status_filter"
    figure = await got(seeded, sql)
    assert figure == lines["all"] and figure > lines["completed"]
    assert number(figure - lines["completed"]) == lines["not_completed"]  # exactly what slipped in


LIMITED = {
    "INNER JOIN, status in WHERE": (
        LINE + f"JOIN orders o ON o.id = i.order_id WHERE {DONE} AND {PERIOD}"
    ),
    "INNER JOIN, status in ON": (
        LINE + f"JOIN orders o ON o.id = i.order_id AND {DONE} WHERE {PERIOD}"
    ),
    "orders LEFT JOIN lines, status and period in ON": (
        f"SELECT SUM(i.line_total) AS v FROM orders o LEFT JOIN order_items i "
        f"ON i.order_id = o.id AND {DONE} AND {PERIOD}"
    ),
    "FILTER": (
        f"SELECT SUM(i.line_total) FILTER (WHERE {DONE}) AS v FROM order_items i "
        f"LEFT JOIN orders o ON o.id = i.order_id AND {PERIOD}"
        f" WHERE {PERIOD}"
    ),
    "CASE": (
        f"SELECT SUM(CASE WHEN {DONE} THEN i.line_total ELSE 0 END) AS v FROM order_items i "
        f"JOIN orders o ON o.id = i.order_id WHERE {PERIOD}"
    ),
    "EXISTS on these lines' orders": (
        LINE + f"WHERE {PERIOD} AND EXISTS (SELECT 1 FROM orders o "
        f"WHERE o.id = i.order_id AND {DONE})"
    ),
    "IN the completed orders": (
        LINE + f"WHERE {PERIOD} AND i.order_id IN (SELECT o.id FROM orders o WHERE {DONE})"
    ),
}


@pytest.mark.parametrize("sql", LIMITED.values(), ids=LIMITED.keys())
async def test_an_accepted_form_returns_the_completed_orders_figure_exactly(
    seeded: Stores, lines: dict[str, float], sql: str
) -> None:
    result = verdict(sql)
    assert isinstance(result, Reviewed) and result.notes == (), result
    assert await got(seeded, sql) == lines["completed"]


async def test_other_statuses_are_accepted_return_their_own_figures_and_are_named(
    seeded: Stores, lines: dict[str, float]
) -> None:
    join = LINE + f"JOIN orders o ON o.id = i.order_id WHERE {PERIOD} AND "
    asked = {
        "o.status = 'refunded'": ("refunded", "Counts refunded orders only."),
        "o.status IN ('cancelled', 'refunded')": (
            "not_completed",
            "Counts orders with status: cancelled, refunded.",
        ),
        "o.status <> 'completed'": (
            "not_completed",
            "Counts orders with status: cancelled, refunded.",
        ),
        "o.status IN ('completed', 'cancelled', 'refunded')": (
            "all",
            "Includes orders of every status: completed, cancelled, refunded.",
        ),
    }
    for condition, (figure, note) in asked.items():
        result = verdict(join + condition)
        assert isinstance(result, Reviewed) and result.notes == (note,), condition
        assert await got(seeded, join + condition) == lines[figure], condition


async def test_the_same_holds_for_order_totals(
    seeded: Stores, owner: psycopg.Connection[Any]
) -> None:
    revenue = "SELECT SUM(o.total_amount - o.tax_amount) AS v FROM orders o "
    truth = (
        "SELECT sum(total_amount - tax_amount) FROM biz.orders WHERE tenant_id = %s "
        f"AND order_date {OCT}"
    )
    completed = one(owner, truth + " AND status = 'completed'")
    everything = one(owner, truth)
    assert everything > completed

    period = f"o.order_date {OCT}"
    for wrong in (
        revenue + f"WHERE {period}",
        revenue + f"LEFT JOIN customers c ON c.id = o.customer_id AND {DONE} WHERE {period}",
    ):
        assert isinstance(verdict(wrong), Refused)
        assert await got(seeded, wrong) == everything
    for right in (
        revenue + f"WHERE {DONE} AND {period}",
        f"SELECT SUM(o.total_amount - o.tax_amount) AS v FROM outlets t LEFT JOIN orders o "
        f"ON o.outlet_id = t.id AND {DONE} AND {period}",
        f"SELECT SUM(o.total_amount - o.tax_amount) FILTER (WHERE {DONE}) AS v FROM orders o "
        f"WHERE {period}",
    ):
        assert isinstance(verdict(right), Reviewed), right
        assert await got(seeded, right) == completed


# -- 2b. the other refusals give wrong figures too ------------------------------------------


async def test_sum_distinct_under_counts_and_the_plain_sum_does_not(
    seeded: Stores, owner: psycopg.Connection[Any]
) -> None:
    where = f"FROM orders o WHERE {DONE} AND o.order_date {OCT}"
    wrong = f"SELECT SUM(DISTINCT o.total_amount - o.tax_amount) AS v {where}"
    right = f"SELECT SUM(o.total_amount - o.tax_amount) AS v {where}"
    truth = one(
        owner,
        "SELECT sum(total_amount - tax_amount) FROM biz.orders WHERE tenant_id = %s "
        f"AND status = 'completed' AND order_date {OCT}",
    )
    result = verdict(wrong)
    assert isinstance(result, Refused) and result.code == "sum_distinct"
    assert await got(seeded, wrong) < truth  # equal amounts of different orders were lost
    assert isinstance(verdict(right), Reviewed) and await got(seeded, right) == truth


async def test_a_left_join_undone_by_where_lists_no_outlet_and_the_filter_in_on_lists_all(
    seeded: Stores, owner: psycopg.Connection[Any]
) -> None:
    # no expense is dated 10 to 20 October: every outlet is "without expenses" then
    period = "e.expense_date BETWEEN DATE '2026-10-10' AND DATE '2026-10-20'"
    base = (
        "SELECT COUNT(DISTINCT t.id) AS v FROM outlets t LEFT JOIN expenses e ON e.outlet_id = t.id"
    )
    wrong, right = f"{base} WHERE {period}", f"{base} AND {period}"
    outlets = one(owner, "SELECT count(*) FROM biz.outlets WHERE tenant_id = %s")
    result = verdict(wrong)
    assert isinstance(result, Refused) and result.code == "left_join_undone"
    assert await got(seeded, wrong) == 0 and outlets == 5
    assert isinstance(verdict(right), Reviewed) and await got(seeded, right) == outlets
    # and the anti-join that finds them is accepted and right
    anti = f"{base} AND {period} WHERE e.id IS NULL"
    assert isinstance(verdict(anti), Reviewed) and await got(seeded, anti) == outlets


async def test_a_left_join_undone_by_a_later_inner_join_loses_the_outlets_it_was_to_keep(
    seeded: Stores, owner: psycopg.Connection[Any]
) -> None:
    # no expense is dated 10 to 20 October: every outlet is "without expenses" then
    base = (
        "SELECT COUNT(DISTINCT t.id) AS v FROM outlets t LEFT JOIN expenses e "
        "ON e.outlet_id = t.id AND e.expense_date BETWEEN DATE '2026-10-10' AND DATE '2026-10-20' "
    )
    wrong = base + "JOIN expense_categories ec ON ec.id = e.category_id"
    right = base + "LEFT JOIN expense_categories ec ON ec.id = e.category_id"
    outlets = one(owner, "SELECT count(*) FROM biz.outlets WHERE tenant_id = %s")
    result = verdict(wrong)
    assert isinstance(result, Refused) and result.code == "left_join_undone"
    assert "the later JOIN to expense_categories" in result.reason
    assert await got(seeded, wrong) == 0 and outlets == 5
    assert isinstance(verdict(right), Reviewed) and await got(seeded, right) == outlets


async def test_an_inner_join_on_the_preserved_table_is_passed_though_it_can_lose_rows(
    seeded: Stores, owner: psycopg.Connection[Any]
) -> None:
    """The limit that stays: outlets LEFT JOIN orders INNER JOIN expenses is right for
    'outlets that had expenses' and wrong for 'every outlet'. Code cannot tell which was
    asked, so it is not refused. Only the golden set catches it (H3)."""
    lost = (
        "SELECT COUNT(DISTINCT t.id) AS v FROM outlets t LEFT JOIN orders o "
        f"ON o.outlet_id = t.id AND {DONE} JOIN expenses e ON e.outlet_id = t.id "
        "AND e.expense_date BETWEEN DATE '2026-10-10' AND DATE '2026-10-20'"
    )
    review = verdict(lost)
    assert isinstance(review, Reviewed) and review.notes == ()
    assert await got(seeded, lost) == 0  # all five outlets gone, and nothing said so
    in_october = lost.replace(
        "'2026-10-10' AND DATE '2026-10-20'", "'2026-10-01' AND DATE '2026-10-31'"
    )
    assert await got(seeded, in_october) == 5  # the same SQL, right when every outlet matches


async def test_a_period_ending_on_a_timestamp_loses_the_last_day_and_the_half_open_range_does_not(
    seeded: Stores, owner: psycopg.Connection[Any]
) -> None:
    count = f"SELECT COUNT(*) AS v FROM orders o WHERE {DONE} AND "
    truth = one(
        owner,
        "SELECT count(*) FROM biz.orders WHERE tenant_id = %s AND status = 'completed' "
        f"AND order_date {OCT}",
    )
    last_day = one(
        owner,
        "SELECT count(*) FROM biz.orders WHERE tenant_id = %s AND status = 'completed' "
        "AND order_date = DATE '2026-10-31'",
    )
    wrong = count + f"o.ordered_at {OCT}"
    result = verdict(wrong)
    assert isinstance(result, Refused) and result.code == "timestamp_period"
    assert last_day > 0 and await got(seeded, wrong) == truth - last_day  # exactly the 31st
    for right in (
        count + f"o.order_date {OCT}",
        count + "o.ordered_at >= DATE '2026-10-01' AND o.ordered_at < DATE '2026-11-01'",
    ):
        review = verdict(right)
        assert isinstance(review, Reviewed) and review.uncertain == ()
        assert await got(seeded, right) == truth


async def test_count_star_over_unrelated_rows_counts_neither_table_and_a_line_count_is_right(
    seeded: Stores, owner: psycopg.Connection[Any]
) -> None:
    where = f"WHERE o.order_date {OCT}"
    pairs = (
        "SELECT COUNT(*) AS v FROM orders o JOIN customer_visits cv ON cv.outlet_id = o.outlet_id "
        f"AND cv.visit_date {OCT} {where}"
    )
    orders = one(
        owner, f"SELECT count(*) FROM biz.orders WHERE tenant_id = %s AND order_date {OCT}"
    )
    visits = one(
        owner,
        f"SELECT count(*) FROM biz.customer_visits WHERE tenant_id = %s AND visit_date {OCT}",
    )
    result = verdict(pairs)
    assert isinstance(result, Refused) and result.code == "count_without_grain"
    assert await got(seeded, pairs) > max(orders, visits)

    line_count = (
        f"SELECT COUNT(*) AS v FROM orders o JOIN order_items i ON i.order_id = o.id {where}"
    )
    lines_in_october = one(
        owner, f"SELECT count(*) FROM biz.order_items WHERE tenant_id = %s AND order_date {OCT}"
    )
    assert isinstance(verdict(line_count), Reviewed)  # a real count: order lines
    assert await got(seeded, line_count) == lines_in_october


# -- 3. a note says what the join really leaves out -----------------------------------------


async def test_each_note_is_true_of_the_rows_the_join_drops(
    seeded: Stores, owner: psycopg.Connection[Any]
) -> None:
    revenue = "SELECT SUM(o.total_amount - o.tax_amount) AS v FROM orders o "
    where = f"WHERE {DONE} AND o.order_date {OCT}"
    truth = (
        "SELECT sum(o.total_amount - o.tax_amount) FROM biz.orders o WHERE o.tenant_id = %s "
        f"AND o.status = 'completed' AND o.order_date {OCT}"
    )
    total = one(owner, truth)

    with_customer = revenue + f"JOIN customers c ON c.id = o.customer_id {where}"
    review = verdict(with_customer)
    assert isinstance(review, Reviewed)
    assert review.notes == ("Orders with no recorded customer are not included.",)
    kept = one(owner, truth + " AND o.customer_id IS NOT NULL")
    assert await got(seeded, with_customer) == kept < total

    with_visit = revenue + f"JOIN customer_visits cv ON cv.order_id = o.id {where}"
    review = verdict(with_visit)
    assert isinstance(review, Reviewed)
    assert review.notes == ("Orders with no recorded customer visit are not included.",)
    kept = one(
        owner,
        truth + " AND EXISTS (SELECT 1 FROM biz.customer_visits v WHERE v.order_id = o.id)",
    )
    assert await got(seeded, with_visit) == kept < total

    for nothing_left_out in (
        revenue + f"LEFT JOIN customers c ON c.id = o.customer_id {where}",
        revenue + f"LEFT JOIN customer_visits cv ON cv.order_id = o.id {where}",
        revenue + f"JOIN outlets t ON t.id = o.outlet_id {where}",
    ):
        review = verdict(nothing_left_out)
        assert isinstance(review, Reviewed) and review.notes == ()
        assert await got(seeded, nothing_left_out) == total


# -- 4. in the pipeline ------------------------------------------------------------------------

LEFT_ON = (
    "SELECT ROUND(SUM(i.line_total), 2) AS product_sales FROM order_items i "
    f"LEFT JOIN orders o ON o.id = i.order_id AND {DONE} WHERE i.order_date {OCT}"
)
INNER_ON = (
    "SELECT ROUND(SUM(i.line_total), 2) AS product_sales FROM order_items i "
    f"JOIN orders o ON o.id = i.order_id AND {DONE} WHERE i.order_date {OCT}"
)


def logged(owner: psycopg.Connection[Any], question: str) -> list[tuple[str, str | None]]:
    owner.rollback()  # a fresh look
    found = owner.execute(
        "SELECT status, reason FROM app.query_log WHERE question = %s ORDER BY created_at",
        (question,),
    ).fetchall()
    return [(r["status"], r["reason"]) for r in found]


async def test_the_left_join_form_is_refused_before_the_database_and_the_repair_is_right(
    seeded: Stores,
    seeded_settings: Settings,
    owner: psycopg.Connection[Any],
    lines: dict[str, float],
) -> None:
    question = "What was the total sales value of products sold last month?"
    llm = FakeLLM().on("sql", SqlDraft(sql=LEFT_ON)).on("sql_repair", SqlDraft(sql=INNER_ON))
    answer, _ = await ask(
        seeded, seeded_settings, None, llm=llm, question=question,
        summary=f"Product sales were {lines['completed']:.2f}.",
    )  # fmt: skip

    assert answer.status == "answered" and answer.data == [{"product_sales": lines["completed"]}]
    assert answer.sql is not None and "LEFT JOIN" not in answer.sql
    assert logged(owner, question) == [("rejected", "missing_status_filter"), ("ok", None)]
    repair = llm.calls_for("sql_repair")[0].user
    assert "which keeps every row of order_items" in repair
    assert f"<<<QUESTION\n{question}\nQUESTION>>>" in repair
    # had it run, it would have reported the larger figure, cancelled and refunded included
    assert await got(seeded, LEFT_ON.replace("AS product_sales", "AS v")) == lines["all"]


async def test_three_refusals_end_in_the_usual_failure_and_no_figure(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    question = "three left joins in a row"
    llm = FakeLLM().on("sql", SqlDraft(sql=LEFT_ON))
    llm.on("sql_repair", SqlDraft(sql=LEFT_ON), SqlDraft(sql=LEFT_ON))
    answer, _ = await ask(seeded, seeded_settings, None, llm=llm, question=question)
    assert answer.status == "failed" and answer.sql is None and answer.data == []
    assert answer.summary.startswith(FAILURE_MESSAGES["failed"])
    assert logged(owner, question) == [("rejected", "missing_status_filter")] * 3
    assert len(llm.calls) == 3


async def test_a_viewers_repair_stays_inside_their_outlets(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    question = "product sales last month, as a viewer"
    llm = FakeLLM().on("sql", SqlDraft(sql=LEFT_ON)).on("sql_repair", SqlDraft(sql=INNER_ON))
    theirs = one(
        owner,
        "SELECT round(sum(i.line_total), 2) FROM biz.order_items i WHERE i.tenant_id = %s "
        f"AND i.order_date {OCT} AND i.order_id IN (SELECT o.id FROM biz.orders o "
        "WHERE o.status = 'completed') AND i.outlet_id IN (SELECT t.id FROM biz.outlets t "
        "WHERE t.name IN ('Indiranagar', 'Koramangala'))",
    )
    answer, _ = await ask(
        seeded, seeded_settings, None, llm=llm, question=question, email=VIEWER,
        summary=f"Product sales were {theirs:.2f}.",
    )  # fmt: skip
    assert answer.status == "answered" and answer.data == [{"product_sales": theirs}]
    assert answer.scope_note == "Limited to your outlets: Indiranagar, Koramangala."
    assert logged(owner, question) == [("rejected", "missing_status_filter"), ("ok", None)]
    owner.rollback()
    asked_by = owner.execute(
        "SELECT DISTINCT u.email, q.tenant_id FROM app.query_log q "
        "JOIN app.users u ON u.id = q.user_id WHERE q.question = %s",
        (question,),
    ).fetchall()
    assert asked_by == [{"email": VIEWER, "tenant_id": BREW}]


async def test_an_attack_is_still_stopped_by_the_guard_first(
    seeded: Stores, seeded_settings: Settings, owner: psycopg.Connection[Any]
) -> None:
    question = "an attack with a missing status"
    attack = "SELECT SUM(o.total_amount) AS v FROM orders o; DELETE FROM orders"
    llm = FakeLLM().on("sql", SqlDraft(sql=attack))
    llm.on("sql_repair", SqlDraft(sql=attack), SqlDraft(sql=attack))
    answer, _ = await ask(seeded, seeded_settings, None, llm=llm, question=question)
    assert answer.status == "failed"
    assert logged(owner, question) == [("rejected", "multiple_statements")] * 3


@pytest.fixture
def api(seeded_settings: Settings) -> Iterator[tuple[TestClient, FakeLLM]]:
    with TestClient(create_app(seeded_settings)) as client:
        llm = FakeLLM()
        client.app.state.llm = llm  # type: ignore[attr-defined]
        yield client, llm


def test_the_notes_reach_the_api_response_in_the_existing_assumptions_list(
    api: tuple[TestClient, FakeLLM], owner: psycopg.Connection[Any]
) -> None:
    client, llm = api
    refunded = one(
        owner,
        "SELECT round(sum(total_amount), 2) FROM biz.orders WHERE tenant_id = %s "
        "AND status = 'refunded'",
    )
    sql = (
        "SELECT ROUND(SUM(o.total_amount), 2) AS refunded FROM orders o WHERE o.status = 'refunded'"
    )
    llm.on("sql", SqlDraft(sql=sql, assumptions=["All dates on record."]))
    llm.on("summary", Summary(text=f"Refunded orders total {refunded:.2f}."))
    response = client.post(
        URL,
        json={"question": "What is the total value of refunded orders?"},
        headers={"X-Dev-User": ANALYST},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered" and body["data"] == [{"refunded": refunded}]
    assert body["assumptions"] == ["All dates on record.", "Counts refunded orders only."]
    # the response has the fields it had: nothing was added for the notes
    assert set(body) == {
        "question", "status", "summary", "sql", "columns", "data", "row_count", "truncated",
        "assumptions", "scope_note",
    }  # fmt: skip


def test_a_viewer_gets_the_notes_and_still_no_sql(api: tuple[TestClient, FakeLLM]) -> None:
    client, llm = api
    sql = (
        "SELECT c.segment AS segment, COUNT(o.id) AS orders FROM orders o "
        f"JOIN customers c ON c.id = o.customer_id WHERE {DONE} GROUP BY c.segment "
        "ORDER BY c.segment"
    )
    llm.on("sql", SqlDraft(sql=sql)).on("summary", Summary(text="Orders by segment."))
    body = client.post(
        URL, json={"question": "How many orders by customer segment?"},
        headers={"X-Dev-User": VIEWER},
    ).json()  # fmt: skip
    assert body["status"] == "answered" and body["sql"] is None
    assert body["assumptions"] == ["Orders with no recorded customer are not included."]
    assert body["scope_note"] == "Limited to your outlets: Indiranagar, Koramangala."
    assert "SELECT" not in str(body)
