"""PURE: the business vocabulary and the choice of relevant schema. No database, no model."""

from __future__ import annotations

from datetime import date

import pytest

from app.semantic.model import load_vocabulary
from app.semantic.periods import period_ranges, periods_text
from app.semantic.render import metrics_text, schema_text
from app.semantic.retrieval import relevant

V = load_vocabulary()


def tables_for(question: str) -> set[str]:
    return {t.table for t in relevant(question, V).tables}


def metrics_for(question: str) -> set[str]:
    return {m.metric for m in relevant(question, V).metrics}


@pytest.mark.parametrize(
    ("question", "must_include", "metric"),
    [
        ("What was our total revenue last month?", {"orders"}, "revenue"),
        ("Which products generated the highest sales?", {"products", "order_items", "orders"},
         "product_revenue"),
        ("Which outlet had the lowest revenue?", {"outlets", "orders"}, "revenue"),
        ("How many customers visited this month?", {"customer_visits"}, "visiting_customers"),
        ("Show the top 10 selling products.", {"products", "order_items", "orders"}, "units_sold"),
        ("Which items are below reorder level?", {"inventory_levels", "products"}, "below_reorder"),
        ("How much did we spend on expenses this quarter?", {"expenses"}, "expenses"),
    ],
)  # fmt: skip
def test_a_question_gets_the_tables_and_the_metric_it_needs(
    question: str, must_include: set[str], metric: str
) -> None:
    assert must_include <= tables_for(question)
    assert metric in metrics_for(question)


def test_only_part_of_the_schema_is_sent() -> None:
    every = {t.table for t in V.tables}
    assert len(every) == 14
    for question in [
        "What was our total revenue last month?",
        "How many customers visited this month?",
        "Show the top 10 selling products.",
    ]:
        chosen = tables_for(question)
        assert len(chosen) <= 4, (question, chosen)
        assert chosen < every
    assert tables_for("What was our total revenue last month?") == {"orders"}


def test_tables_that_need_a_bridge_get_it() -> None:
    # categories and outlets only meet through products, order lines and orders
    chosen = tables_for("revenue by category and outlet")
    assert {"categories", "products", "order_items", "outlets"} <= chosen


def test_a_question_with_no_known_word_gets_the_core_tables() -> None:
    assert tables_for("hello there") == {"orders", "order_items", "outlets", "products"}


def test_the_choice_does_not_depend_on_case_or_plurals() -> None:
    assert tables_for("REVENUE BY OUTLETS") == tables_for("revenue by outlet")


def test_the_schema_text_holds_names_types_meanings_values_and_joins() -> None:
    text = schema_text(relevant("Which outlet had the lowest revenue?", V).tables)
    assert "TABLE orders:" in text and "TABLE outlets:" in text
    assert "total_amount (decimal): What the customer paid" in text
    assert "One of: completed, cancelled, refunded" in text
    assert "JOIN orders.outlet_id = outlets.id" in text
    assert "customers" not in text  # the join to a table that was not chosen is not shown


def test_private_columns_and_tenant_id_never_reach_a_prompt() -> None:
    text = schema_text(V.tables)  # even with every table shown
    for word in ("tenant_id", "phone", "email", "contact_name", "password"):
        assert word not in text
    customers = schema_text((V.table("customers"),))
    assert "name" not in customers.replace("TABLE customers", "")


def test_the_metric_text_carries_the_definition_and_its_condition() -> None:
    text = metrics_text(relevant("total revenue", V).metrics)
    assert "revenue (money): SUM(orders.total_amount - orders.tax_amount)" in text
    assert "WHERE orders.status = 'completed'" in text


def test_every_metric_and_join_names_real_tables_and_columns() -> None:
    catalog = V.catalog()
    for table in V.tables:
        for join in table.joins:
            target_table, target_column = join.to.split(".")
            assert join.column in catalog[table.table]
            assert target_column in catalog[target_table]
        if table.date_column:
            assert table.date_column in catalog[table.table]
    for metric in V.metrics:
        assert set(metric.tables) <= set(catalog)


# -- periods ---------------------------------------------------------------------


def test_period_words_become_date_ranges() -> None:
    ranges = period_ranges(date(2026, 11, 10))  # a Tuesday
    assert ranges["last month"] == (date(2026, 10, 1), date(2026, 10, 31))
    assert ranges["this month"] == (date(2026, 11, 1), date(2026, 11, 10))
    assert ranges["this week"] == (date(2026, 11, 9), date(2026, 11, 10))
    assert ranges["last week"] == (date(2026, 11, 2), date(2026, 11, 8))
    assert ranges["this quarter"] == (date(2026, 10, 1), date(2026, 11, 10))
    assert ranges["last quarter"] == (date(2026, 7, 1), date(2026, 9, 30))
    assert ranges["last 7 days"] == (date(2026, 11, 3), date(2026, 11, 9))
    assert ranges["last year"] == (date(2025, 1, 1), date(2025, 12, 31))


def test_last_month_in_january_is_december_of_the_year_before() -> None:
    ranges = period_ranges(date(2027, 1, 15))
    assert ranges["last month"] == (date(2026, 12, 1), date(2026, 12, 31))
    assert ranges["last quarter"] == (date(2026, 10, 1), date(2026, 12, 31))


def test_last_month_after_a_leap_february() -> None:
    assert period_ranges(date(2028, 3, 5))["last month"] == (date(2028, 2, 1), date(2028, 2, 29))


def test_the_period_text_states_today_and_each_range() -> None:
    text = periods_text(date(2026, 11, 10))
    assert text.startswith("Today is 2026-11-10 (Tuesday).")
    assert "- last month: 2026-10-01 to 2026-10-31" in text


def test_the_months_needed_for_a_comparison_are_given_as_ranges() -> None:
    ranges = period_ranges(date(2026, 11, 10))
    assert ranges["the month before last"] == (date(2026, 9, 1), date(2026, 9, 30))
    assert ranges["last two months"] == (date(2026, 9, 1), date(2026, 10, 31))
    assert ranges["the same days of last month"] == (date(2026, 10, 1), date(2026, 10, 10))


def test_the_same_days_of_a_shorter_last_month_stop_at_its_last_day() -> None:
    ranges = period_ranges(date(2027, 3, 31))
    assert ranges["the same days of last month"] == (date(2027, 2, 1), date(2027, 2, 28))
    assert period_ranges(date(2027, 1, 20))["the month before last"] == (
        date(2026, 11, 1),
        date(2026, 11, 30),
    )
