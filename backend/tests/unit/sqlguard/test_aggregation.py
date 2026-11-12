"""PURE: the aggregation check (docs/design/nl2sql.md §3b). No database, no model.

A case here is a line of SQL and what must happen to it: refused because a join
repeats the rows it adds up, passed because it cannot, or passed because the check
cannot be sure. The same shapes are run against real data, and their wrong figures
measured, in tests/integration/test_aggregation_live.py.
"""

from __future__ import annotations

import pytest
import sqlglot

from app.semantic.model import Table, Vocabulary, load_vocabulary, validate
from app.sqlguard import Catalog, Checked, Passed, Refused, check, check_aggregation
from app.sqlguard.aggregation import CODE

VOCABULARY = load_vocabulary()
CATALOG = Catalog(VOCABULARY.catalog())
UNIQUE = VOCABULARY.unique_keys()


def verdict(sql: str) -> Passed | Refused:
    guarded = check(sql, CATALOG)
    assert isinstance(guarded, Checked), f"the guard must pass it first: {sql} -> {guarded}"
    return check_aggregation(guarded.sql, CATALOG, UNIQUE)


def refused(sql: str) -> str:
    result = verdict(sql)
    assert isinstance(result, Refused), f"should have been refused: {sql}"
    assert result.code == CODE
    return result.reason


def passed(sql: str) -> Passed:
    result = verdict(sql)
    assert isinstance(result, Passed), f"should have passed: {sql} -> {result}"
    return result


def sure(sql: str) -> None:
    """Passed because it was judged sound, not because the check gave up."""
    assert passed(sql).uncertain == (), sql


# -- confirmed fan-out: refused ------------------------------------------------------

FAN_OUT = {
    "an order's total added up across its lines": (
        "SELECT SUM(o.total_amount - o.tax_amount) AS revenue FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id WHERE o.status = 'completed'"
    ),
    "the same, averaged": (
        "SELECT AVG(o.total_amount) AS aov FROM orders o JOIN order_items oi ON oi.order_id = o.id"
    ),
    "the same, counted by column": (
        "SELECT COUNT(o.id) AS orders FROM orders o JOIN order_items oi ON oi.order_id = o.id"
    ),
    "the same, grouped by product": (
        "SELECT p.name AS product, ROUND(SUM(o.total_amount), 2) AS revenue FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id "
        "GROUP BY p.name"
    ),
    "a purchase order's total added up across its lines": (
        "SELECT SUM(po.total_amount) AS purchases FROM purchase_orders po "
        "JOIN purchase_order_items i ON i.purchase_order_id = po.id WHERE po.status = 'received'"
    ),
    "orders joined to visits on the outlet": (
        "SELECT t.name AS outlet, SUM(o.total_amount - o.tax_amount) AS revenue, "
        "COUNT(DISTINCT v.customer_id) AS customers FROM orders o "
        "JOIN customer_visits v ON v.outlet_id = o.outlet_id "
        "JOIN outlets t ON t.id = o.outlet_id GROUP BY t.name"
    ),
    "orders joined to visits on the customer": (
        "SELECT SUM(o.total_amount) AS revenue FROM orders o "
        "JOIN customer_visits v ON v.customer_id = o.customer_id"
    ),
    "expenses joined to orders on the outlet": (
        "SELECT SUM(e.amount) AS spent FROM expenses e JOIN orders o ON o.outlet_id = e.outlet_id"
    ),
    "a customer with both orders and visits": (
        "SELECT c.segment, COUNT(v.id) AS visits, SUM(o.total_amount) AS revenue "
        "FROM customers c JOIN customer_visits v ON v.customer_id = c.id "
        "JOIN orders o ON o.customer_id = c.id GROUP BY c.segment"
    ),
    "stock joined to order lines on the product only": (
        "SELECT SUM(oi.line_total) AS revenue FROM order_items oi "
        "JOIN inventory_levels il ON il.product_id = oi.product_id "
        "WHERE il.quantity_on_hand <= il.reorder_level"
    ),
    "stock units added up across order lines": (
        "SELECT SUM(il.quantity_on_hand) AS units FROM inventory_levels il "
        "JOIN order_items oi ON oi.outlet_id = il.outlet_id AND oi.product_id = il.product_id"
    ),
    "three detail tables through the outlet": (
        "SELECT t.name AS outlet, SUM(o.total_amount) AS revenue, SUM(e.amount) AS expenses, "
        "SUM(po.total_amount) AS purchases FROM outlets t "
        "LEFT JOIN orders o ON o.outlet_id = t.id LEFT JOIN expenses e ON e.outlet_id = t.id "
        "LEFT JOIN purchase_orders po ON po.outlet_id = t.id GROUP BY t.name"
    ),
    "an outlet attribute added up across its orders": (
        "SELECT SUM(il.reorder_level) AS level FROM outlets t "
        "JOIN inventory_levels il ON il.outlet_id = t.id "
        "JOIN stock_movements m ON m.outlet_id = t.id"
    ),
    "a join with no condition at all": (
        "SELECT SUM(o.total_amount) AS revenue FROM orders o CROSS JOIN order_items oi"
    ),
    "the join written in WHERE": (
        "SELECT SUM(o.total_amount) AS revenue FROM orders o, order_items oi "
        "WHERE oi.order_id = o.id"
    ),
    "no aliases and unqualified columns": (
        "SELECT SUM(total_amount) AS revenue FROM orders JOIN order_items ON order_id = orders.id"
    ),
    "inside a CTE": (
        "WITH r AS (SELECT o.outlet_id, SUM(o.total_amount) AS revenue FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id GROUP BY o.outlet_id) "
        "SELECT t.name AS outlet, r.revenue FROM outlets t JOIN r ON r.outlet_id = t.id"
    ),
    "inside a subquery in FROM": (
        "SELECT s.revenue FROM (SELECT SUM(o.total_amount) AS revenue FROM orders o "
        "JOIN customer_visits v ON v.outlet_id = o.outlet_id) AS s"
    ),
    "in one arm of a UNION": (
        "SELECT SUM(e.amount) AS amount FROM expenses e "
        "UNION ALL SELECT SUM(o.total_amount) AS amount FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id"
    ),
    "with a FILTER clause": (
        "SELECT SUM(o.total_amount) FILTER (WHERE o.status = 'completed') AS revenue "
        "FROM orders o JOIN order_items oi ON oi.order_id = o.id"
    ),
    "a self-join on a column that is not a key": (
        "SELECT SUM(a.total_amount) AS revenue FROM orders a "
        "JOIN orders b ON b.customer_id = a.customer_id"
    ),
}


@pytest.mark.parametrize("sql", FAN_OUT.values(), ids=FAN_OUT.keys())
def test_a_sum_over_rows_a_join_repeats_is_refused(sql: str) -> None:
    assert refused(sql)


def test_the_reason_names_the_aggregate_the_table_and_what_repeats_it_and_how_to_fix_it() -> None:
    reason = refused(FAN_OUT["an order's total added up across its lines"])
    assert "SUM(o.total_amount - o.tax_amount)" in reason
    assert "orders is joined to order_items" in reason
    assert "order_items.line_total" in reason and "subquery" in reason and "EXISTS" in reason


def test_the_reason_lists_every_table_that_repeats_the_rows() -> None:
    reason = refused(FAN_OUT["three detail tables through the outlet"])
    assert "orders is joined to expenses, purchase_orders in a way" in reason


@pytest.mark.parametrize(
    "sql",
    [
        "select sum(O.Total_Amount) from Orders O join Order_Items OI on OI.Order_Id = O.Id",
        "SELECT\n  SUM( o.total_amount )\nFROM orders AS o\n  INNER JOIN order_items AS oi\n"
        "    ON ( o.id = oi.order_id )",
        "SELECT SUM(o.total_amount) FROM order_items oi LEFT JOIN orders o ON oi.order_id = o.id",
        "SELECT SUM(o.total_amount) FROM biz.orders o JOIN biz.order_items oi ON oi.order_id=o.id",
        "SELECT ROUND(COALESCE(SUM(orders.total_amount), 0), 2) FROM orders "
        "JOIN order_items ON order_items.order_id = orders.id AND orders.status = 'completed'",
    ],
)
def test_formatting_case_aliases_and_join_order_make_no_difference(sql: str) -> None:
    assert refused(sql)


# -- sound: passed, and judged ---------------------------------------------------------

SOUND = {
    "one table": (
        "SELECT ROUND(SUM(total_amount - tax_amount), 2) AS revenue FROM orders "
        "WHERE status = 'completed'"
    ),
    "one table, grouped": (
        "SELECT order_date, COUNT(id) AS orders, AVG(total_amount) AS aov FROM orders "
        "GROUP BY order_date"
    ),
    "lines added up, joined to their order": (
        "SELECT ROUND(SUM(oi.line_total), 2) AS revenue FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id WHERE o.status = 'completed'"
    ),
    "lines joined to order, product, category and outlet": (
        "SELECT t.name AS outlet, c.name AS category, SUM(oi.quantity) AS units, "
        "ROUND(SUM(oi.line_total - oi.quantity * oi.unit_cost), 2) AS profit FROM order_items oi "
        "JOIN orders o ON o.id = oi.order_id JOIN products p ON p.id = oi.product_id "
        "JOIN categories c ON c.id = p.category_id JOIN outlets t ON t.id = oi.outlet_id "
        "WHERE o.status = 'completed' GROUP BY t.name, c.name"
    ),
    "orders by outlet": (
        "SELECT t.name AS outlet, ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue "
        "FROM orders o JOIN outlets t ON t.id = o.outlet_id GROUP BY t.name"
    ),
    "orders by customer segment": (
        "SELECT c.segment, SUM(o.total_amount - o.tax_amount) AS revenue FROM orders o "
        "JOIN customers c ON c.id = o.customer_id GROUP BY c.segment"
    ),
    "orders joined to visits on the order id, one to one": (
        "SELECT v.source, SUM(o.total_amount) AS revenue, COUNT(v.id) AS visits FROM orders o "
        "LEFT JOIN customer_visits v ON v.order_id = o.id GROUP BY v.source"
    ),
    "lines joined to stock on the whole key": (
        "SELECT ROUND(SUM(oi.line_total), 2) AS revenue FROM order_items AS oi "
        "JOIN orders AS o ON oi.order_id = o.id JOIN inventory_levels AS il "
        "ON oi.outlet_id = il.outlet_id AND oi.product_id = il.product_id "
        "WHERE o.status = 'completed' AND il.quantity_on_hand <= il.reorder_level"
    ),
    "the whole stock key reached through two tables": (
        "SELECT SUM(oi.quantity) AS units FROM order_items oi JOIN orders o ON o.id = oi.order_id "
        "JOIN products p ON p.id = oi.product_id "
        "JOIN inventory_levels il ON il.outlet_id = o.outlet_id AND il.product_id = p.id"
    ),
    "the whole stock key, half in ON and half in WHERE": (
        "SELECT SUM(oi.line_total) AS revenue FROM order_items oi "
        "JOIN inventory_levels il ON il.product_id = oi.product_id "
        "WHERE il.outlet_id = oi.outlet_id"
    ),
    "stock value: a figure built from the stock row and its product": (
        "SELECT t.name AS outlet, ROUND(SUM(il.quantity_on_hand * p.cost_price), 2) AS value "
        "FROM inventory_levels il JOIN products p ON p.id = il.product_id "
        "JOIN outlets t ON t.id = il.outlet_id GROUP BY t.name"
    ),
    "purchase lines joined to order, supplier, product": (
        "SELECT s.name AS supplier, SUM(i.line_total) AS purchases FROM purchase_order_items i "
        "JOIN purchase_orders po ON po.id = i.purchase_order_id "
        "JOIN suppliers s ON s.id = po.supplier_id JOIN products p ON p.id = i.product_id "
        "WHERE po.status = 'received' GROUP BY s.name"
    ),
    "purchase orders by supplier and outlet": (
        "SELECT s.name AS supplier, t.name AS outlet, SUM(po.total_amount) AS purchases "
        "FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id "
        "JOIN outlets t ON t.id = po.outlet_id GROUP BY s.name, t.name"
    ),
    "expenses by category and outlet": (
        "SELECT c.name AS category, t.name AS outlet, ROUND(SUM(e.amount), 2) AS spent "
        "FROM expenses e JOIN expense_categories c ON c.id = e.category_id "
        "JOIN outlets t ON t.id = e.outlet_id GROUP BY c.name, t.name"
    ),
    "movements by product": (
        "SELECT p.name AS product, SUM(m.quantity) AS moved FROM stock_movements m "
        "JOIN products p ON p.id = m.product_id GROUP BY p.name"
    ),
    "the other table pinned to one row by a constant on its key": (
        "SELECT SUM(o.total_amount) AS revenue FROM orders o JOIN outlets t ON t.code = 'IND'"
    ),
    "a filter through EXISTS instead of a join": (
        "SELECT SUM(oi.line_total) AS revenue FROM order_items oi "
        "JOIN orders o ON o.id = oi.order_id WHERE o.status = 'completed' AND EXISTS ("
        "SELECT 1 FROM inventory_levels il WHERE il.product_id = oi.product_id "
        "AND il.quantity_on_hand <= il.reorder_level)"
    ),
    "a filter through IN": (
        "SELECT SUM(o.total_amount) AS revenue FROM orders o WHERE o.customer_id IN ("
        "SELECT v.customer_id FROM customer_visits v)"
    ),
    "a correlated subquery that adds up one table": (
        "SELECT t.name AS outlet, (SELECT SUM(o.total_amount) FROM orders o "
        "WHERE o.outlet_id = t.id) AS revenue, (SELECT COUNT(v.id) FROM customer_visits v "
        "WHERE v.outlet_id = t.id) AS visits FROM outlets t"
    ),
    "a self-join on the key": (
        "SELECT SUM(a.total_amount) AS revenue FROM orders a JOIN orders b ON b.id = a.id"
    ),
}


@pytest.mark.parametrize("sql", SOUND.values(), ids=SOUND.keys())
def test_a_join_that_cannot_repeat_the_rows_added_up_is_passed(sql: str) -> None:
    sure(sql)


REPETITION_DOES_NOT_MATTER = {
    "MIN and MAX": (
        "SELECT MIN(o.total_amount) AS low, MAX(o.total_amount) AS high FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id"
    ),
    "COUNT(DISTINCT)": (
        "SELECT COUNT(DISTINCT o.id) AS orders, COUNT(DISTINCT v.customer_id) AS customers "
        "FROM orders o JOIN customer_visits v ON v.outlet_id = o.outlet_id"
    ),
    "SUM(DISTINCT)": (
        "SELECT SUM(DISTINCT o.total_amount) AS amounts FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id"
    ),
    "COUNT(*)": ("SELECT COUNT(*) AS n FROM orders o JOIN order_items oi ON oi.order_id = o.id"),
    "no aggregate at all": (
        "SELECT o.order_no, v.source FROM orders o "
        "JOIN customer_visits v ON v.outlet_id = o.outlet_id"
    ),
    "a window function": (
        "SELECT o.order_no, SUM(o.total_amount) OVER (PARTITION BY o.outlet_id) AS running "
        "FROM orders o JOIN order_items oi ON oi.order_id = o.id"
    ),
}


@pytest.mark.parametrize(
    "sql", REPETITION_DOES_NOT_MATTER.values(), ids=REPETITION_DOES_NOT_MATTER.keys()
)
def test_a_join_alone_is_never_a_reason_to_refuse(sql: str) -> None:
    passed(sql)


def test_each_figure_in_its_own_subquery_is_the_form_the_prompt_asks_for_and_passes() -> None:
    sure(
        "WITH revenue AS (SELECT o.outlet_id, SUM(o.total_amount - o.tax_amount) AS revenue "
        "FROM orders o WHERE o.status = 'completed' GROUP BY o.outlet_id), "
        "visits AS (SELECT v.outlet_id, COUNT(DISTINCT v.customer_id) AS customers "
        "FROM customer_visits v GROUP BY v.outlet_id), "
        "spent AS (SELECT e.outlet_id, SUM(e.amount) AS expenses FROM expenses e "
        "GROUP BY e.outlet_id) "
        "SELECT t.name AS outlet, r.revenue, v.customers, s.expenses FROM outlets t "
        "LEFT JOIN revenue r ON r.outlet_id = t.id LEFT JOIN visits v ON v.outlet_id = t.id "
        "LEFT JOIN spent s ON s.outlet_id = t.id ORDER BY r.revenue DESC"
    )


# -- cannot be sure: passed, and said so -------------------------------------------------

UNCERTAIN = {
    "a CTE joined and then added up": (
        "WITH low AS (SELECT il.outlet_id, il.product_id FROM inventory_levels il "
        "WHERE il.quantity_on_hand <= il.reorder_level) "
        "SELECT SUM(oi.line_total) AS revenue FROM order_items oi "
        "JOIN low ON low.product_id = oi.product_id",
        "subquery, CTE or function",
    ),
    "a subquery joined and then added up": (
        "SELECT SUM(o.total_amount) AS revenue FROM orders o JOIN ("
        "SELECT v.outlet_id FROM customer_visits v) AS s ON s.outlet_id = o.outlet_id",
        "subquery, CTE or function",
    ),
    "a join on OR": (
        "SELECT SUM(o.total_amount) AS revenue FROM orders o JOIN customer_visits v "
        "ON v.order_id = o.id OR v.customer_id = o.customer_id",
        "OR, an inequality or an expression",
    ),
    "a join on an inequality": (
        "SELECT SUM(o.total_amount) AS revenue FROM orders o JOIN customers c "
        "ON c.id = o.customer_id AND o.order_date >= c.joined_on "
        "JOIN customer_visits v ON v.visit_date < o.order_date",
        "OR, an inequality or an expression",
    ),
    "a join on an expression": (
        "SELECT SUM(o.total_amount) AS revenue FROM orders o JOIN customer_visits v "
        "ON CAST(v.order_id AS TEXT) = CAST(o.id AS TEXT)",
        "OR, an inequality or an expression",
    ),
    "tables tied together by an inequality in WHERE": (
        "SELECT SUM(e.amount) AS spent FROM expenses e JOIN orders o "
        "ON o.outlet_id = e.outlet_id WHERE o.order_date > e.expense_date",
        "OR, an inequality or an expression",
    ),
    "a set-returning function": (
        "SELECT SUM(o.total_amount) AS revenue FROM orders o "
        "JOIN generate_series(1, 3) AS g(n) ON true",
        "subquery, CTE or function",
    ),
}


@pytest.mark.parametrize("case", UNCERTAIN.values(), ids=UNCERTAIN.keys())
def test_a_relationship_the_check_cannot_reason_about_is_passed_and_said_so(
    case: tuple[str, str],
) -> None:
    sql, why = case
    guarded = check(sql, CATALOG)
    if not isinstance(guarded, Checked):
        pytest.skip(f"the guard itself refuses this: {guarded.code}")
    result = check_aggregation(guarded.sql, CATALOG, UNIQUE)
    assert isinstance(result, Passed)
    assert any(why in reason for reason in result.uncertain), result.uncertain


def test_sql_that_does_not_parse_is_passed_not_crashed_on() -> None:
    result = check_aggregation("SELECT FROM WHERE (((", CATALOG, UNIQUE)
    assert isinstance(result, Passed) and result.uncertain


def test_a_column_the_catalog_does_not_know_is_passed_not_crashed_on() -> None:
    result = check_aggregation(
        "SELECT SUM(o.nope) FROM orders o JOIN order_items oi ON oi.order_id = o.id",
        CATALOG,
        UNIQUE,
    )
    assert isinstance(result, Passed) and result.uncertain


# -- it reads, it never writes ---------------------------------------------------------


@pytest.mark.parametrize("sql", [*FAN_OUT.values(), *SOUND.values()])
def test_the_check_never_changes_the_query(sql: str) -> None:
    guarded = check(sql, CATALOG)
    assert isinstance(guarded, Checked)
    before = sqlglot.parse_one(guarded.sql, read="postgres")
    check_aggregation(guarded.sql, CATALOG, UNIQUE)
    assert sqlglot.parse_one(guarded.sql, read="postgres") == before


def test_a_refusal_carries_no_sql_to_run() -> None:
    result = verdict(FAN_OUT["an order's total added up across its lines"])
    assert isinstance(result, Refused) and not hasattr(result, "sql")


# -- the metadata ---------------------------------------------------------------------------


def test_every_table_lists_a_unique_key() -> None:
    assert all(UNIQUE[t.table] for t in VOCABULARY.tables)
    assert UNIQUE["inventory_levels"] == (frozenset({"outlet_id", "product_id"}),)
    assert frozenset({"order_id"}) in UNIQUE["customer_visits"]


def test_a_table_with_no_key_listed_is_never_judged() -> None:
    missing = {**UNIQUE, "order_items": ()}
    sql = FAN_OUT["an order's total added up across its lines"]
    result = check_aggregation(sql, CATALOG, missing)
    assert isinstance(result, Passed)
    assert "no unique key is listed for order_items" in result.uncertain


def test_a_table_absent_from_the_metadata_is_never_judged() -> None:
    absent = {k: v for k, v in UNIQUE.items() if k != "orders"}
    result = check_aggregation(FAN_OUT["expenses joined to orders on the outlet"], CATALOG, absent)
    assert isinstance(result, Passed) and result.uncertain


def test_with_half_of_the_stock_key_missing_from_the_metadata_the_join_is_not_trusted() -> None:
    # were the key wrongly listed as product_id alone, the product-only join would pass:
    # the metadata is what the check stands on, hence the live test against the database
    wrong = {**UNIQUE, "inventory_levels": (frozenset({"product_id"}),)}
    sql = FAN_OUT["stock joined to order lines on the product only"]
    assert isinstance(check_aggregation(sql, CATALOG, wrong), Passed)
    assert isinstance(check_aggregation(sql, CATALOG, UNIQUE), Refused)


def test_an_agents_restricted_vocabulary_keeps_the_keys_of_its_tables() -> None:
    sales = VOCABULARY.restricted_to(frozenset({"orders", "order_items", "outlets"}))
    assert set(sales.unique_keys()) == {"orders", "order_items", "outlets"}
    assert sales.unique_keys()["orders"] == UNIQUE["orders"]


def _with(table: Table) -> Vocabulary:
    others = tuple(t for t in VOCABULARY.tables if t.table != table.table)
    return Vocabulary(tables=(*others, table), metrics=VOCABULARY.metrics)


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ((("nope",),), "unique key on unknown columns"),
        (((),), "empty unique key"),
        ((("id", "id"),), "repeats a column"),
    ],
)
def test_a_unique_key_that_contradicts_the_table_stops_the_app_at_start_up(
    key: tuple[tuple[str, ...], ...], message: str
) -> None:
    broken = VOCABULARY.table("orders").model_copy(update={"unique": key})
    with pytest.raises(AssertionError, match=message):
        validate(_with(broken))


def test_the_real_vocabulary_is_valid() -> None:
    validate(VOCABULARY)
