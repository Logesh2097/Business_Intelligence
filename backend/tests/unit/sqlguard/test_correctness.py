"""PURE: the correctness check (docs/design/nl2sql.md §3c). No database, no model.

A case is a line of SQL and what must happen to it: refused with a code, passed, or
passed because the check cannot be sure. The same shapes are run against real data,
with the wrong figures measured, in tests/integration/test_correctness_live.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import sqlglot

from app.semantic.model import Vocabulary, load_vocabulary, validate
from app.sqlguard import Catalog, Checked, Facts, Refused, Reviewed, check, check_correctness
from app.sqlguard.correctness import (
    COUNT_WITHOUT_GRAIN,
    LEFT_JOIN_UNDONE,
    MISSING_STATUS,
    SUM_DISTINCT,
    TIMESTAMP_PERIOD,
    F,
    N,
    T,
    _and,
    _not,
    _or,
)

VOCABULARY = load_vocabulary()
CATALOG = Catalog(VOCABULARY.catalog())
FACTS = Facts(**VOCABULARY.correctness_facts())
DONE = "o.status = 'completed'"
OCT = "BETWEEN DATE '2026-10-01' AND DATE '2026-10-31'"


def verdict(sql: str, facts: Facts = FACTS) -> Reviewed | Refused:
    guarded = check(sql, CATALOG)
    assert isinstance(guarded, Checked), f"the guard must pass it first: {sql} -> {guarded}"
    return check_correctness(guarded.sql, CATALOG, facts)


def refused(sql: str, code: str) -> str:
    result = verdict(sql)
    assert isinstance(result, Refused), f"should have been refused: {sql}"
    assert result.code == code, f"{result.code}: {result.reason}"
    return result.reason


def passed(sql: str) -> Reviewed:
    result = verdict(sql)
    assert isinstance(result, Reviewed), f"should have passed: {sql} -> {result}"
    return result


def sure(sql: str) -> Reviewed:
    result = passed(sql)
    assert result.uncertain == (), f"{sql} -> {result.uncertain}"
    return result


def unsure(sql: str, why: str) -> None:
    result = passed(sql)
    assert any(why in reason for reason in result.uncertain), f"{sql} -> {result.uncertain}"


def cases(table: dict[str, str]) -> Any:
    return pytest.mark.parametrize("sql", table.values(), ids=table.keys())


# == P1.1  SUM(DISTINCT), AVG(DISTINCT) ====================================================

DISTINCT_SUMS = {
    "SUM(DISTINCT) on one table": (
        f"SELECT SUM(DISTINCT o.total_amount - o.tax_amount) AS v FROM orders o WHERE {DONE}"
    ),
    "AVG(DISTINCT)": f"SELECT AVG(DISTINCT o.total_amount) AS v FROM orders o WHERE {DONE}",
    "as a fix for a fan-out": (
        "SELECT SUM(DISTINCT o.total_amount) AS v FROM orders o "
        f"JOIN order_items oi ON oi.order_id = o.id WHERE {DONE}"
    ),
    "lower case, no alias": "select sum(distinct total_amount) as v from orders where "
    "status = 'completed'",
    "inside ROUND and COALESCE": (
        f"SELECT ROUND(COALESCE(SUM(DISTINCT o.total_amount), 0), 2) AS v FROM orders o "
        f"WHERE {DONE}"
    ),
    "grouped by something that is not the key": (
        "SELECT o.outlet_id, SUM(DISTINCT o.total_amount) AS v FROM orders o "
        f"WHERE {DONE} GROUP BY o.outlet_id"
    ),
    "inside a CTE": (
        f"WITH r AS (SELECT SUM(DISTINCT o.total_amount) AS v FROM orders o WHERE {DONE}) "
        "SELECT r.v FROM r"
    ),
    "a column that has no status rule": "SELECT SUM(DISTINCT e.amount) AS v FROM expenses e",
    "a figure built from two tables": (
        "SELECT SUM(DISTINCT il.quantity_on_hand * p.cost_price) AS v FROM inventory_levels il "
        "JOIN products p ON p.id = il.product_id"
    ),
}


@cases(DISTINCT_SUMS)
def test_a_distinct_sum_or_average_is_refused(sql: str) -> None:
    reason = refused(sql, SUM_DISTINCT)
    assert "DISTINCT removes equal values" in reason and "COUNT(DISTINCT ...) is fine" in reason


DISTINCT_ALLOWED = {
    "COUNT(DISTINCT)": "SELECT COUNT(DISTINCT v.customer_id) AS n FROM customer_visits v",
    "SELECT DISTINCT": "SELECT DISTINCT c.segment FROM customers c",
    "DISTINCT ON": "SELECT DISTINCT ON (o.outlet_id) o.outlet_id, o.order_no FROM orders o",
    "MIN and MAX(DISTINCT)": "SELECT MIN(DISTINCT e.amount) AS a, MAX(DISTINCT e.amount) "
    "AS b FROM expenses e",
    "SUM without DISTINCT": "SELECT SUM(e.amount) AS v FROM expenses e",
    "grouped by the table's unique key": (
        "SELECT o.id, SUM(DISTINCT o.total_amount) AS v FROM orders o "
        f"JOIN order_items oi ON oi.order_id = o.id WHERE {DONE} GROUP BY o.id"
    ),
    "grouped by another unique key of it": (
        "SELECT o.order_no, SUM(DISTINCT o.total_amount) AS v FROM orders o "
        f"JOIN order_items oi ON oi.order_id = o.id WHERE {DONE} GROUP BY o.order_no"
    ),
    "a constant": "SELECT SUM(DISTINCT 1) AS v FROM outlets t",
}


@cases(DISTINCT_ALLOWED)
def test_other_uses_of_distinct_are_never_touched(sql: str) -> None:
    sure(sql)


def test_a_distinct_sum_over_a_cte_column_is_not_judged() -> None:
    unsure(
        f"WITH r AS (SELECT o.total_amount AS amount FROM orders o WHERE {DONE}) "
        "SELECT SUM(DISTINCT r.amount) AS v FROM r",
        "a DISTINCT sum over a column of a subquery or CTE",
    )


# == P1.2  a LEFT JOIN the WHERE undoes ====================================================


@pytest.mark.parametrize(
    ("a", "b", "both", "either"),
    [
        (T, T, T, T), (T, F, F, T), (T, N, N, T),
        (F, F, F, F), (F, N, F, N), (N, N, N, N),
    ],
)  # fmt: skip
def test_and_or_follow_sqls_three_valued_tables(a: str, b: str, both: str, either: str) -> None:
    one, two = frozenset({a}), frozenset({b})
    assert _and(one, two) == _and(two, one) == {both}
    assert _or(one, two) == _or(two, one) == {either}


def test_not_keeps_null_null() -> None:
    assert _not(frozenset({T})) == {F} and _not(frozenset({F})) == {T}
    assert _not(frozenset({N})) == {N} and _not(frozenset({T, N})) == {F, N}


BASE = "SELECT t.name FROM outlets t LEFT JOIN orders r ON r.outlet_id = t.id WHERE "
UNDONE = {
    "equality": BASE + "r.channel = 'online'",
    "inequality": BASE + "r.total_amount > 100",
    "not equal": BASE + "r.channel <> 'online'",
    "BETWEEN": BASE + f"r.order_date {OCT}",
    "NOT BETWEEN": BASE + "r.total_amount NOT BETWEEN 1 AND 5",
    "IN a list": BASE + "r.channel IN ('online', 'delivery')",
    "NOT IN a list": BASE + "r.channel NOT IN ('online')",
    "IN a subquery": BASE + "r.customer_id IN (SELECT c.id FROM customers c)",
    "LIKE": BASE + "r.order_no LIKE 'A%'",
    "ILIKE": BASE + "r.order_no ILIKE 'a%'",
    "IS NOT NULL": BASE + "r.id IS NOT NULL",
    "inside a cast": BASE + "CAST(r.order_date AS TEXT) = '2026-10-05'",
    "inside arithmetic": BASE + "r.total_amount - r.tax_amount > 0",
    "inside LOWER": BASE + "LOWER(r.channel) = 'online'",
    "with another condition beside it": BASE + "r.channel = 'online' AND t.city = 'Mysuru'",
    "NOT of a comparison": BASE + "NOT (r.channel = 'online')",
    "an OR whose every branch needs the row": BASE + "r.channel = 'online' OR r.total_amount > 5",
    "IS NULL together with a comparison (always empty)": (
        BASE + "r.id IS NULL AND r.order_date = DATE '2026-10-05'"
    ),
    "NOT over an OR with another table": BASE + "NOT (r.status = 'completed' OR t.name = 'A')",
    "upper case and brackets": (
        "SELECT T.NAME FROM OUTLETS T LEFT OUTER JOIN ORDERS R ON (R.OUTLET_ID = T.ID) "
        "WHERE ((R.CHANNEL = 'online'))"
    ),
    "no aliases": (
        "SELECT outlets.name FROM outlets LEFT JOIN orders ON orders.outlet_id = outlets.id "
        "WHERE orders.channel = 'online'"
    ),
    "a RIGHT JOIN, mirrored": (
        "SELECT t.name FROM orders r RIGHT JOIN outlets t ON r.outlet_id = t.id "
        "WHERE r.channel = 'online'"
    ),
    "a LEFT JOIN to a CTE": (
        "WITH s AS (SELECT e.outlet_id, SUM(e.amount) AS spent FROM expenses e GROUP BY "
        "e.outlet_id) "
        "SELECT t.name FROM outlets t LEFT JOIN s ON s.outlet_id = t.id WHERE s.spent > 0"
    ),
    "inside a subquery": (
        "SELECT x.name FROM (SELECT t.name FROM outlets t LEFT JOIN orders r "
        "ON r.outlet_id = t.id WHERE r.channel = 'online') x"
    ),
}


@cases(UNDONE)
def test_a_left_join_whose_unmatched_rows_can_never_pass_where_is_refused(sql: str) -> None:
    reason = refused(sql, LEFT_JOIN_UNDONE)
    assert "has no effect" in reason
    assert "into the join's ON" in reason and "write JOIN instead" in reason  # both, no choice


KEPT = {
    "IS NULL, the anti-join": BASE + "r.id IS NULL",
    "IS NULL with a condition on the other table": BASE + "r.id IS NULL AND t.city = 'Mysuru'",
    "OR with IS NULL": BASE + "r.channel = 'online' OR r.id IS NULL",
    "OR with a condition on the other table": BASE + "r.channel = 'online' OR t.name = 'A'",
    "NOT over an AND with another table": BASE + "NOT (r.status = 'completed' AND t.name = 'A')",
    "COALESCE": BASE + "COALESCE(r.total_amount, 0) = 0",
    "CASE": BASE + "CASE WHEN r.id IS NULL THEN 0 ELSE 1 END = 0",
    "NULLIF": BASE + "NULLIF(r.channel, 'x') IS NULL",
    "IS DISTINCT FROM": BASE + "r.channel IS DISTINCT FROM 'online'",
    "IS NOT DISTINCT FROM": BASE + "r.channel IS NOT DISTINCT FROM 'online'",
    "NOT IN a subquery (true when the subquery is empty)": (
        BASE + "r.customer_id NOT IN (SELECT c.id FROM customers c WHERE c.city = 'Nowhere')"
    ),
    "the filter written in ON": (
        "SELECT t.name FROM outlets t LEFT JOIN orders r ON r.outlet_id = t.id "
        "AND r.channel = 'online'"
    ),
    "a condition only on the left table": BASE + "t.city = 'Mysuru'",
    "the condition in HAVING": (
        "SELECT t.name FROM outlets t LEFT JOIN orders r ON r.outlet_id = t.id "
        "GROUP BY t.name HAVING COUNT(r.id) > 0"
    ),
    "no WHERE at all": "SELECT t.name FROM outlets t LEFT JOIN orders r ON r.outlet_id = t.id",
    "an inner join": (
        "SELECT t.name FROM outlets t JOIN orders r ON r.outlet_id = t.id WHERE r.channel "
        "= 'online'"
    ),
}


@cases(KEPT)
def test_a_left_join_that_can_still_keep_its_unmatched_rows_is_passed(sql: str) -> None:
    sure(sql)


def test_two_left_joins_are_judged_one_by_one() -> None:
    two = (
        "SELECT t.name FROM outlets t LEFT JOIN orders r ON r.outlet_id = t.id "
        "LEFT JOIN expenses e ON e.outlet_id = t.id WHERE "
    )
    sure(two + "r.id IS NULL AND e.id IS NULL")
    reason = refused(two + "r.id IS NULL AND e.amount > 0", LEFT_JOIN_UNDONE)
    assert "LEFT JOIN to expenses" in reason and "orders" not in reason.split("has no effect")[0]


def test_a_self_join_is_told_apart_by_alias() -> None:
    twice = (
        "SELECT a.order_no FROM orders a LEFT JOIN orders b ON b.customer_id = a.customer_id WHERE "
    )
    sure(twice + "a.channel = 'online'")
    assert "LEFT JOIN to orders" in refused(twice + "b.channel = 'online'", LEFT_JOIN_UNDONE)


def test_a_chain_names_the_join_that_is_undone() -> None:
    chain = (
        "SELECT t.name FROM outlets t LEFT JOIN orders r ON r.outlet_id = t.id "
        "LEFT JOIN customers c ON c.id = r.customer_id WHERE c.segment = 'vip'"
    )
    assert "LEFT JOIN to customers" in refused(chain, LEFT_JOIN_UNDONE)


def test_a_full_join_beside_a_where_is_passed_as_uncertain() -> None:
    unsure(
        "SELECT t.name FROM outlets t FULL JOIN orders r ON r.outlet_id = t.id "
        "WHERE r.channel = 'online'",
        "FULL, NATURAL, USING or LATERAL",
    )


# -- ... or a later INNER JOIN undoes (Step 5) -----------------------------------------------

T_R = "SELECT t.name FROM outlets t LEFT JOIN orders r ON r.outlet_id = t.id "
UNDONE_LATER = {
    "a later join through the left-joined table": T_R + "JOIN customers c ON c.id = r.customer_id",
    "written INNER JOIN, with another condition": (
        T_R + "INNER JOIN customers c ON c.id = r.customer_id AND c.segment = 'vip'"
    ),
    "the columns the other way round": T_R + "JOIN customers c ON r.customer_id = c.id",
    "a chain: the third table hangs on the second": (
        "SELECT t.name FROM outlets t LEFT JOIN expenses e ON e.outlet_id = t.id "
        "JOIN expense_categories ec ON ec.id = e.category_id"
    ),
    "a join in between that is harmless": (
        T_R + "JOIN categories g ON g.name = 'Coffee' JOIN customers c ON c.id = r.customer_id"
    ),
    "the second of two LEFT JOINs is the one undone": (
        "SELECT t.name FROM outlets t LEFT JOIN expenses e ON e.outlet_id = t.id "
        "LEFT JOIN orders r ON r.outlet_id = t.id JOIN customers c ON c.id = r.customer_id"
    ),
    "upper case, brackets": (
        "SELECT T.NAME FROM OUTLETS T LEFT OUTER JOIN ORDERS R ON (R.OUTLET_ID = T.ID) "
        "INNER JOIN CUSTOMERS C ON (C.ID = R.CUSTOMER_ID)"
    ),
    "a RIGHT JOIN, mirrored": (
        "SELECT t.name FROM orders r RIGHT JOIN outlets t ON r.outlet_id = t.id "
        "JOIN customers c ON c.id = r.customer_id"
    ),
    "inside a CTE": (
        "WITH x AS (SELECT t.name FROM outlets t LEFT JOIN orders r ON r.outlet_id = t.id "
        "JOIN customers c ON c.id = r.customer_id) SELECT x.name FROM x"
    ),
    "a later join that needs the row to be there (IS NOT NULL)": (
        T_R + "JOIN customers c ON r.id IS NOT NULL AND c.city = t.city"
    ),
}


@cases(UNDONE_LATER)
def test_a_left_join_whose_unmatched_rows_a_later_inner_join_drops_is_refused(sql: str) -> None:
    reason = refused(sql, LEFT_JOIN_UNDONE)
    assert "has no effect: the later JOIN to" in reason
    assert "a LEFT JOIN as well" in reason and "write JOIN instead" in reason  # both, no choice


def test_the_reason_names_both_joins() -> None:
    reason = refused(UNDONE_LATER["a later join through the left-joined table"], LEFT_JOIN_UNDONE)
    assert "The LEFT JOIN to orders has no effect: the later JOIN to customers" in reason
    chain = refused(
        UNDONE_LATER["the second of two LEFT JOINs is the one undone"], LEFT_JOIN_UNDONE
    )
    assert "The LEFT JOIN to orders has no effect" in chain and "expenses" not in chain


KEPT_LATER = {
    "the later join is on the preserved table, not the left-joined one": (
        "SELECT t.name FROM outlets t LEFT JOIN orders r ON r.outlet_id = t.id "
        "INNER JOIN expenses e ON e.outlet_id = t.id"
    ),
    "the later join is a LEFT JOIN too": T_R + "LEFT JOIN customers c ON c.id = r.customer_id",
    "the later join can still be true without the row (OR IS NULL)": (
        T_R + "JOIN customers c ON c.id = r.customer_id OR r.id IS NULL"
    ),
    "the later join goes through COALESCE": (
        T_R + "JOIN categories g ON g.name = COALESCE(r.channel, 'none')"
    ),
    "the inner join comes BEFORE the left join": (
        "SELECT t.name FROM orders r JOIN customers c ON c.id = r.customer_id "
        "LEFT JOIN outlets t ON t.id = r.outlet_id"
    ),
    "a detail table with its parents beside an optional extra": (
        "SELECT p.name, COUNT(v.id) AS visits FROM order_items i "
        "LEFT JOIN customer_visits v ON v.order_id = i.order_id "
        "JOIN products p ON p.id = i.product_id GROUP BY p.name"
    ),
    "a later join whose ON is always false is not this mistake": (
        T_R + "JOIN categories g ON g.name = 'x' AND 1 = 0"
    ),
}


@cases(KEPT_LATER)
def test_a_later_inner_join_that_does_not_need_the_left_joined_table_is_passed(sql: str) -> None:
    sure(sql)


def test_a_left_join_and_an_inner_join_together_are_never_refused_for_that_alone() -> None:
    """outlets LEFT JOIN orders INNER JOIN expenses: the LEFT JOIN still works (an outlet
    with expenses and no orders is kept). Outlets with no expenses are lost, and whether
    that is wrong depends on the question. It is a limit, recorded, not a refusal."""
    sure(KEPT_LATER["the later join is on the preserved table, not the left-joined one"])


# == P1.3  a sales figure no status condition limits ====================================

LINES = "SELECT SUM(i.line_total) AS v FROM order_items i "
NOT_LIMITED = {
    "orders with no condition": "SELECT SUM(o.total_amount - o.tax_amount) AS v FROM orders o",
    "an average": "SELECT AVG(o.total_amount) AS v FROM orders o",
    "only a date condition": f"SELECT SUM(o.subtotal) AS v FROM orders o WHERE o.order_date {OCT}",
    "order lines alone (R1)": LINES,
    "units from order lines alone": "SELECT SUM(i.quantity) AS v FROM order_items i",
    "lines joined to products only": (
        "SELECT p.name, SUM(i.line_total) AS v FROM order_items i "
        "JOIN products p ON p.id = i.product_id GROUP BY p.name"
    ),
    "lines LEFT JOIN orders, status in ON (R2)": (
        LINES + "LEFT JOIN orders o ON o.id = i.order_id AND o.status = 'completed'"
    ),
    "the same, other aliases, brackets, upper case": (
        "SELECT SUM(L.LINE_TOTAL) AS V FROM ORDER_ITEMS L LEFT OUTER JOIN ORDERS B "
        "ON (B.ID = L.ORDER_ID AND (B.STATUS = 'completed'))"
    ),
    "the same with more joins": (
        "SELECT p.name, SUM(i.line_total) AS v FROM order_items i "
        "JOIN products p ON p.id = i.product_id "
        "LEFT JOIN orders o ON o.id = i.order_id AND o.status = 'completed' GROUP BY p.name"
    ),
    "orders RIGHT JOIN lines, status in ON (R2)": (
        "SELECT SUM(i.line_total) AS v FROM orders o RIGHT JOIN order_items i "
        "ON i.order_id = o.id AND o.status = 'completed'"
    ),
    "FULL JOIN, status in ON (R2)": (
        LINES + "FULL JOIN orders o ON o.id = i.order_id AND o.status = 'completed'"
    ),
    "orders LEFT JOIN customers, its own status in that ON (R2)": (
        "SELECT SUM(o.total_amount) AS v FROM orders o LEFT JOIN customers c "
        "ON c.id = o.customer_id AND o.status = 'completed'"
    ),
    "an OR that lets other rows through, in WHERE (R3)": (
        "SELECT SUM(o.total_amount) AS v FROM orders o "
        "WHERE o.status = 'completed' OR o.channel = 'online'"
    ),
    "the same in an inner join's ON (R3)": (
        LINES + "JOIN orders o ON o.id = i.order_id AND (o.status = 'completed' OR o.channel = 'x')"
    ),
    "status only selected and ordered by (R4)": (
        "SELECT MAX(o.status) AS s, SUM(o.total_amount) AS v FROM orders o ORDER BY s"
    ),
    "IS NOT NULL (R5)": "SELECT SUM(o.total_amount) AS v FROM orders o WHERE o.status IS NOT NULL",
    "equal to itself (R5)": "SELECT SUM(o.total_amount) AS v FROM orders o WHERE o.status "
    "= o.status",
    "LIKE anything (R5)": "SELECT SUM(o.total_amount) AS v FROM orders o WHERE o.status LIKE '%'",
    "the restriction on the other alias of a self-join (R6)": (
        "SELECT SUM(a.total_amount) AS v FROM orders a JOIN orders b ON b.id = a.id "
        "WHERE b.status = 'completed'"
    ),
    "EXISTS over orders that are not these lines' orders (R6)": (
        LINES + "WHERE EXISTS (SELECT 1 FROM orders o WHERE o.status = 'completed')"
    ),
    "EXISTS tied on the outlet, not the order (R6)": (
        LINES + "WHERE EXISTS (SELECT 1 FROM orders o WHERE o.outlet_id = i.outlet_id "
        "AND o.status = 'completed')"
    ),
    "EXISTS tied to another set of order lines (R6)": (
        "SELECT SUM(i.line_total) AS v FROM order_items i JOIN order_items j ON j.id = i.id "
        "WHERE EXISTS (SELECT 1 FROM orders o WHERE o.id = j.order_id AND o.status = 'completed')"
    ),
    "the restriction only in the outer query (R7)": (
        "SELECT x.v FROM (SELECT o.status, SUM(o.total_amount) AS v FROM orders o "
        "GROUP BY o.outlet_id, o.status) x WHERE x.status = 'completed' AND 1 = 0 "
        "UNION ALL SELECT SUM(o2.total_amount) FROM orders o2"
    ),
    "the restriction only in another CTE (R7)": (
        "WITH done AS (SELECT o.id FROM orders o WHERE o.status = 'completed') "
        "SELECT SUM(o2.total_amount) AS v FROM orders o2"
    ),
    "inside a CTE": (
        "WITH r AS (SELECT o.outlet_id, SUM(o.total_amount) AS v FROM orders o GROUP BY "
        "o.outlet_id) "
        "SELECT t.name, r.v FROM outlets t JOIN r ON r.outlet_id = t.id"
    ),
}


@cases(NOT_LIMITED)
def test_a_sales_sum_that_no_status_condition_limits_is_refused(sql: str) -> None:
    reason = refused(sql, MISSING_STATUS)
    assert "nothing limits them by order status" in reason
    assert "Use the statuses the question asks for" in reason and "'completed'" in reason


def test_the_reason_says_why_the_left_join_condition_does_not_count() -> None:
    reason = refused(NOT_LIMITED["lines LEFT JOIN orders, status in ON (R2)"], MISSING_STATUS)
    assert "SUM(i.line_total)" in reason
    assert "in the ON of a LEFT JOIN, which keeps every row of order_items" in reason
    assert "in WHERE, or in the ON of a plain JOIN" in reason and "FILTER" in reason


def test_the_reason_for_lines_alone_says_to_join_orders() -> None:
    reason = refused(LINES, MISSING_STATUS)
    assert "does not join it to orders" in reason and "Join order_items to orders" in reason


LIMITED = {
    "WHERE": f"SELECT SUM(o.total_amount) AS v FROM orders o WHERE {DONE}",
    "WHERE, no alias": "SELECT SUM(total_amount) AS v FROM orders WHERE status = 'completed'",
    "WHERE with IN of one value": (
        "SELECT SUM(o.total_amount) AS v FROM orders o WHERE o.status IN ('completed')"
    ),
    "WHERE on LOWER(status)": (
        "SELECT SUM(o.total_amount) AS v FROM orders o WHERE LOWER(o.status) = 'completed'"
    ),
    "the literal written first": (
        "SELECT SUM(o.total_amount) AS v FROM orders o WHERE 'completed' = o.status"
    ),
    "lines INNER JOIN orders, WHERE (E1)": LINES
    + f"JOIN orders o ON o.id = i.order_id WHERE {DONE}",
    "lines INNER JOIN orders, status in ON (E2)": (
        LINES + f"JOIN orders o ON o.id = i.order_id AND {DONE}"
    ),
    "the link in WHERE, the status in ON (E2)": (
        f"SELECT SUM(i.line_total) AS v FROM order_items i JOIN orders o ON {DONE} "
        "WHERE o.id = i.order_id"
    ),
    "orders LEFT JOIN lines, status in ON: failing lines become NULL (E3)": (
        f"SELECT SUM(i.line_total) AS v FROM orders o LEFT JOIN order_items i "
        f"ON i.order_id = o.id AND {DONE}"
    ),
    "outlets LEFT JOIN orders, status in ON (E3)": (
        f"SELECT t.name, SUM(o.total_amount) AS v FROM outlets t LEFT JOIN orders o "
        f"ON o.outlet_id = t.id AND {DONE} GROUP BY t.name"
    ),
    "FILTER (E4)": f"SELECT SUM(o.total_amount) FILTER (WHERE {DONE}) AS v FROM orders o",
    "FILTER on joined lines (E4)": (
        f"SELECT SUM(i.line_total) FILTER (WHERE {DONE}) AS v FROM order_items i "
        "LEFT JOIN orders o ON o.id = i.order_id"
    ),
    "CASE with no ELSE (E4)": (
        f"SELECT SUM(CASE WHEN {DONE} THEN o.total_amount END) AS v FROM orders o"
    ),
    "CASE ELSE 0 (E4)": (
        f"SELECT SUM(CASE WHEN {DONE} THEN o.total_amount - o.tax_amount ELSE 0 END) AS v "
        "FROM orders o"
    ),
    "EXISTS on these lines' orders (E6)": (
        LINES + f"WHERE EXISTS (SELECT 1 FROM orders o WHERE o.id = i.order_id AND {DONE})"
    ),
    "IN the completed orders (E6)": (
        LINES + f"WHERE i.order_id IN (SELECT o.id FROM orders o WHERE {DONE})"
    ),
    "inside each CTE": (
        f"WITH r AS (SELECT o.outlet_id, SUM(o.total_amount) AS v FROM orders o WHERE {DONE} "
        "GROUP BY o.outlet_id) SELECT t.name, r.v FROM outlets t JOIN r ON r.outlet_id = t.id"
    ),
    "two sums, each with its own orders": (
        "SELECT (SELECT SUM(a.total_amount) FROM orders a WHERE a.status = 'completed') AS x, "
        "(SELECT SUM(b.total_amount) FROM orders b WHERE b.status = 'completed') AS y"
    ),
    "a sum that is not a sales figure": "SELECT SUM(e.amount) AS v FROM expenses e",
    "a count of orders (not judged)": "SELECT COUNT(o.id) AS n FROM orders o",
    "a window sum (not judged)": (
        "SELECT o.order_no, SUM(o.total_amount) OVER (PARTITION BY o.outlet_id) AS v FROM orders o"
    ),
    "MAX of an amount (not a sum)": "SELECT MAX(o.total_amount) AS v FROM orders o",
}


@cases(LIMITED)
def test_a_sales_sum_that_a_status_condition_really_limits_is_passed(sql: str) -> None:
    assert sure(sql).notes == ()  # completed: the ordinary case, nothing to say


def test_a_status_in_where_after_a_left_join_is_sent_back_as_a_left_join_undone() -> None:
    """Two rules meet here. For the status rule the condition is effective (WHERE removes
    the row). But the same WHERE makes the LEFT JOIN an inner join, so the LEFT JOIN rule
    refuses it first and asks which is meant. No figure is lost either way."""
    for sql in (
        LINES + f"LEFT JOIN orders o ON o.id = i.order_id WHERE {DONE}",
        "SELECT SUM(i.line_total) AS v FROM orders o RIGHT JOIN order_items i "
        f"ON i.order_id = o.id WHERE {DONE}",
    ):
        assert "write JOIN instead" in refused(sql, LEFT_JOIN_UNDONE)


def test_is_not_null_is_read_in_both_forms_sqlglot_writes() -> None:
    for where in ("r.id IS NOT NULL", "NOT (r.id IS NULL)", "NOT r.id IS NULL"):
        refused(BASE + where, LEFT_JOIN_UNDONE)
    for where in ("r.id IS NULL", "NOT (r.id IS NOT NULL)"):
        sure(BASE + where)


OTHER_STATUSES = [
    ("o.status = 'refunded'", "Counts refunded orders only."),
    ("o.status = 'cancelled'", "Counts cancelled orders only."),
    ("o.status IN ('cancelled', 'refunded')", "Counts orders with status: cancelled, refunded."),
    ("o.status <> 'cancelled'", "Counts orders with status: completed, refunded."),
    ("o.status NOT IN ('cancelled', 'refunded')", None),  # that is completed: nothing to say
    ("NOT (o.status = 'completed')", "Counts orders with status: cancelled, refunded."),
    (
        "o.status = 'cancelled' OR o.status = 'refunded'",
        "Counts orders with status: cancelled, refunded.",
    ),
    (
        "o.status IN ('completed', 'cancelled', 'refunded')",
        "Includes orders of every status: completed, cancelled, refunded.",
    ),
    ("UPPER(o.status) = 'REFUNDED'", "Counts refunded orders only."),
]


@pytest.mark.parametrize(("condition", "note"), OTHER_STATUSES, ids=[c for c, _ in OTHER_STATUSES])
def test_another_status_is_allowed_and_the_note_says_which(
    condition: str, note: str | None
) -> None:
    result = sure(f"SELECT SUM(o.total_amount) AS v FROM orders o WHERE {condition}")
    assert result.notes == ((note,) if note else ())


def test_revenue_by_status_is_allowed_and_said_to_be_split() -> None:
    result = sure("SELECT o.status, SUM(o.total_amount) AS v FROM orders o GROUP BY o.status")
    assert result.notes == ("Results are shown separately for each order status.",)


def test_not_exists_and_not_in_are_explicit_exclusions() -> None:
    gone = "(SELECT 1 FROM orders o WHERE o.id = i.order_id AND o.status = 'cancelled')"
    assert sure(LINES + f"WHERE NOT EXISTS {gone}").notes == (
        "Counts orders with status: completed, refunded.",
    )
    kept = "(SELECT o.id FROM orders o WHERE o.status IN ('cancelled', 'refunded'))"
    assert sure(LINES + f"WHERE i.order_id NOT IN {kept}").notes == ()


def test_a_status_the_database_does_not_allow_is_never_named_in_a_note() -> None:
    result = passed("SELECT SUM(o.total_amount) AS v FROM orders o WHERE o.status = 'shipped'")
    assert result.notes == ()  # the condition is a decision; 'shipped' is not ours to describe


def test_two_aggregates_with_different_conditions_each_get_their_note() -> None:
    result = sure(
        "SELECT SUM(o.total_amount) FILTER (WHERE o.status = 'completed') AS sold, "
        "SUM(o.total_amount) FILTER (WHERE o.status = 'refunded') AS refunded FROM orders o"
    )
    assert result.notes == ("Counts refunded orders only.",)


STATUS_UNSURE = {
    "lines joined to a subquery of orders (U1)": (
        LINES + f"JOIN (SELECT o.id FROM orders o WHERE {DONE}) c ON c.id = i.order_id",
        "order lines joined to a subquery or CTE",
    ),
    "lines joined to a CTE of orders (U1)": (
        f"WITH done AS (SELECT o.id FROM orders o WHERE {DONE}) "
        "SELECT SUM(i.line_total) AS v FROM order_items i JOIN done d ON d.id = i.order_id",
        "order lines joined to a subquery or CTE",
    ),
    "status compared with another column (U3)": (
        "SELECT SUM(o.total_amount) AS v FROM orders o WHERE o.status = o.channel",
        "compared with something that is not a literal",
    ),
    "a CASE in another form (U4)": (
        "SELECT SUM(CASE WHEN o.channel = 'online' THEN o.total_amount ELSE o.subtotal END) AS v "
        "FROM orders o",
        "a CASE inside a sales sum",
    ),
    "EXISTS nested under OR (U4)": (
        LINES + "WHERE i.quantity > 1 OR EXISTS (SELECT 1 FROM orders o "
        f"WHERE o.id = i.order_id AND {DONE})",
        "inside a subquery in a form not read here",
    ),
}


@pytest.mark.parametrize("case", STATUS_UNSURE.values(), ids=STATUS_UNSURE.keys())
def test_a_status_decision_the_check_cannot_establish_is_passed_and_said_so(
    case: tuple[str, str],
) -> None:
    unsure(*case)


def test_a_sum_over_raw_rows_passed_through_a_cte_is_not_judged() -> None:
    passed("WITH o AS (SELECT x.total_amount FROM orders x) SELECT SUM(o.total_amount) AS v FROM o")


def test_an_agent_that_cannot_read_orders_is_not_judged_on_status() -> None:
    no_orders = VOCABULARY.restricted_to(frozenset({"outlets", "products", "inventory_levels"}))
    facts = Facts(**no_orders.correctness_facts())
    assert facts.sales == {} and facts.status_column == ""
    result = check_correctness(
        "SELECT SUM(il.quantity_on_hand) AS v FROM inventory_levels il",
        Catalog(no_orders.catalog()),
        facts,
    )
    assert result == Reviewed()


# == P2.1  a period that ends on a timestamp ==============================================

COUNT = f"SELECT COUNT(*) AS n FROM orders o WHERE {DONE} AND "
CUT_SHORT = {
    "BETWEEN two dates": COUNT + f"o.ordered_at {OCT}",
    "BETWEEN date strings": COUNT + "o.ordered_at BETWEEN '2026-10-01' AND '2026-10-31'",
    "BETWEEN ending at midnight": (
        COUNT
        + "o.ordered_at BETWEEN TIMESTAMP '2026-10-01 00:00:00' AND TIMESTAMP '2026-10-31 00:00:00'"
    ),
    "<= a date": COUNT + "o.ordered_at >= DATE '2026-10-01' AND o.ordered_at <= DATE '2026-10-31'",
    "the date written first": COUNT + "DATE '2026-10-31' >= o.ordered_at",
    "= a date": COUNT + "o.ordered_at = DATE '2026-10-15'",
    "a cast to a date": COUNT + "o.ordered_at <= CAST('2026-10-31' AS DATE)",
    "on visits": "SELECT COUNT(*) AS n FROM customer_visits v WHERE v.visited_at <= DATE "
    "'2026-10-31'",
    "on stock movements": (
        "SELECT SUM(m.quantity) AS q FROM stock_movements m "
        "WHERE m.moved_at BETWEEN DATE '2026-10-01' AND DATE '2026-10-31'"
    ),
    "in a join's ON": (
        "SELECT t.name, COUNT(o.id) AS n FROM outlets t LEFT JOIN orders o ON o.outlet_id = t.id "
        "AND o.ordered_at BETWEEN DATE '2026-10-01' AND DATE '2026-10-31' GROUP BY t.name"
    ),
    "under OR": COUNT + "(o.ordered_at <= DATE '2026-10-31' OR o.channel = 'online')",
}


@cases(CUT_SHORT)
def test_a_period_ending_on_a_timestamp_with_a_date_is_refused(sql: str) -> None:
    reason = refused(sql, TIMESTAMP_PERIOD)
    assert "half-open range" in reason and "the day after the last day" in reason


def test_the_reason_names_the_date_column_to_use() -> None:
    reason = refused(CUT_SHORT["BETWEEN two dates"], TIMESTAMP_PERIOD)
    assert "orders.ordered_at is a date and time" in reason
    assert "Filter the period on orders.order_date" in reason


WHOLE_PERIOD = {
    "the half-open range": (
        COUNT + "o.ordered_at >= DATE '2026-10-01' AND o.ordered_at < DATE '2026-11-01'"
    ),
    "a start alone": COUNT + "o.ordered_at >= DATE '2026-10-01'",
    "an exclusive end alone": COUNT + "o.ordered_at < DATE '2026-11-01'",
    "the date column": COUNT + f"o.order_date {OCT}",
    "CAST(ts AS DATE) BETWEEN": COUNT + f"CAST(o.ordered_at AS DATE) {OCT}",
    "DATE_TRUNC": COUNT + "DATE_TRUNC('month', o.ordered_at) = DATE '2026-10-01'",
    "hour of day, EXTRACT": COUNT + "EXTRACT(HOUR FROM o.ordered_at) >= 18",
    "hour of day, DATE_PART": COUNT + "DATE_PART('hour', o.ordered_at) BETWEEN 12 AND 14",
    "a table with no timestamp": "SELECT SUM(e.amount) AS v FROM expenses e WHERE "
    "e.expense_date <= DATE '2026-10-31'",
}


@cases(WHOLE_PERIOD)
def test_correct_date_and_hour_filtering_is_passed(sql: str) -> None:
    sure(sql)


INCLUSIVE_END = {
    "<= 23:59:59": COUNT + "o.ordered_at <= TIMESTAMP '2026-10-31 23:59:59'",
    "BETWEEN ... 23:59:59.999": (
        COUNT + "o.ordered_at BETWEEN '2026-10-01' AND '2026-10-31 23:59:59.999'"
    ),
    "<= the current date": COUNT + "o.ordered_at <= CURRENT_DATE",
}


@cases(INCLUSIVE_END)
def test_an_inclusive_end_with_a_time_of_day_is_uncertain_never_called_complete(sql: str) -> None:
    unsure(sql, "a period ending on a timestamp with an inclusive end that is not a date")


# == P2.3  COUNT(*) where no table keeps its rows =========================================

NO_GRAIN = {
    "orders and visits through the outlet": (
        "SELECT COUNT(*) AS n FROM orders o JOIN customer_visits v ON v.outlet_id = o.outlet_id"
    ),
    "orders and visits through the customer": (
        "SELECT COUNT(*) AS n FROM orders o JOIN customer_visits v ON v.customer_id = o.customer_id"
    ),
    "a customer's orders and visits": (
        "SELECT c.segment, COUNT(*) AS n FROM customers c JOIN orders o ON o.customer_id = c.id "
        "JOIN customer_visits v ON v.customer_id = c.id GROUP BY c.segment"
    ),
    "COUNT(1)": "SELECT COUNT(1) AS n FROM expenses e JOIN orders o ON o.outlet_id = e.outlet_id",
    "a cross join": "SELECT COUNT(*) AS n FROM outlets t CROSS JOIN products p",
    "inside ROUND": (
        "SELECT ROUND(COUNT(*) / 2.0, 1) AS n FROM orders o "
        "JOIN customer_visits v ON v.outlet_id = o.outlet_id"
    ),
}


@cases(NO_GRAIN)
def test_count_star_over_combinations_of_unrelated_rows_is_refused(sql: str) -> None:
    reason = refused(sql, COUNT_WITHOUT_GRAIN)
    assert "counts combinations" in reason and "count over a subquery" in reason


A_REAL_COUNT = {
    "one table": "SELECT COUNT(*) AS n FROM orders o WHERE o.status = 'completed'",
    "order lines (orders joined to their lines)": (
        "SELECT COUNT(*) AS n FROM orders o JOIN order_items i ON i.order_id = o.id"
    ),
    "orders joined to outlet and customer": (
        "SELECT t.name, COUNT(*) AS n FROM orders o JOIN outlets t ON t.id = o.outlet_id "
        "LEFT JOIN customers c ON c.id = o.customer_id GROUP BY t.name"
    ),
    "lines joined to stock on the whole key": (
        "SELECT COUNT(*) AS n FROM order_items i JOIN inventory_levels il "
        "ON il.outlet_id = i.outlet_id AND il.product_id = i.product_id"
    ),
    "orders and visits one to one": (
        "SELECT COUNT(*) AS n FROM orders o LEFT JOIN customer_visits v ON v.order_id = o.id"
    ),
    "COUNT(*) only in HAVING": (
        "SELECT t.name FROM outlets t JOIN orders o ON o.outlet_id = t.id "
        "JOIN customer_visits v ON v.outlet_id = t.id GROUP BY t.name HAVING COUNT(*) > 0"
    ),
    "COUNT(*) only in ORDER BY": (
        "SELECT t.name FROM outlets t JOIN orders o ON o.outlet_id = t.id "
        "JOIN customer_visits v ON v.outlet_id = t.id GROUP BY t.name ORDER BY COUNT(*) DESC"
    ),
    "a window count": (
        "SELECT o.order_no, COUNT(*) OVER (PARTITION BY o.outlet_id) AS n FROM orders o "
        "JOIN customer_visits v ON v.outlet_id = o.outlet_id"
    ),
    "COUNT(DISTINCT)": (
        "SELECT COUNT(DISTINCT o.id) AS n FROM orders o "
        "JOIN customer_visits v ON v.outlet_id = o.outlet_id"
    ),
}


@cases(A_REAL_COUNT)
def test_a_count_of_real_records_is_never_refused(sql: str) -> None:
    result = passed(sql)
    assert not any("COUNT(*)" in why for why in result.uncertain)


COUNT_UNSURE = {
    "a subquery in FROM": (
        "SELECT COUNT(*) AS n FROM orders o JOIN (SELECT v.outlet_id FROM customer_visits v) s "
        "ON s.outlet_id = o.outlet_id"
    ),
    "a join on OR": (
        "SELECT COUNT(*) AS n FROM orders o JOIN customer_visits v "
        "ON v.order_id = o.id OR v.customer_id = o.customer_id"
    ),
    "a join on an inequality": (
        "SELECT COUNT(*) AS n FROM orders o JOIN customer_visits v ON v.visit_date < o.order_date"
    ),
}


@cases(COUNT_UNSURE)
def test_when_the_grain_cannot_be_decided_count_star_is_passed(sql: str) -> None:
    unsure(sql, "COUNT(*) over ")


# == P2.2  notes ============================================================================

NO_CUSTOMER = "Orders with no recorded customer are not included."
NO_VISIT = "Orders with no recorded customer visit are not included."
NO_ORDER = "Customer visits with no order are not included."


def test_n1_an_inner_join_through_an_optional_column_says_what_is_left_out() -> None:
    result = sure(
        "SELECT c.segment, SUM(o.total_amount) AS v FROM orders o "
        f"JOIN customers c ON c.id = o.customer_id WHERE {DONE} GROUP BY c.segment"
    )
    assert result.notes == (NO_CUSTOMER,)


def test_n1_also_when_the_join_is_written_in_where_and_the_rows_are_counted() -> None:
    result = sure(
        "SELECT c.segment, COUNT(*) AS n FROM orders o, customers c "
        "WHERE c.id = o.customer_id GROUP BY c.segment"
    )
    assert result.notes == (NO_CUSTOMER,)


def test_n2_an_inner_join_to_an_optional_one_to_one_record_names_the_other_table() -> None:
    result = sure(
        "SELECT SUM(o.total_amount - o.tax_amount) AS v FROM orders o "
        f"JOIN customer_visits cv ON cv.order_id = o.id WHERE {DONE}"
    )
    assert result.notes == (NO_VISIT,)


def test_both_sides_counted_gives_both_notes_and_never_more_than_two() -> None:
    result = sure(
        "SELECT COUNT(cv.id) AS visits, SUM(o.total_amount) AS v FROM orders o "
        f"JOIN customer_visits cv ON cv.order_id = o.id JOIN customers c ON c.id = o.customer_id "
        f"WHERE {DONE}"
    )
    assert set(result.notes) <= {NO_ORDER, NO_VISIT, NO_CUSTOMER} and len(result.notes) == 2


NO_NOTE = {
    "a LEFT JOIN leaves nothing out": (
        "SELECT c.segment, SUM(o.total_amount) AS v FROM orders o "
        f"LEFT JOIN customers c ON c.id = o.customer_id WHERE {DONE} GROUP BY c.segment"
    ),
    "a LEFT JOIN to the one-to-one record": (
        f"SELECT SUM(o.total_amount) AS v FROM orders o LEFT JOIN customer_visits cv "
        f"ON cv.order_id = o.id WHERE {DONE}"
    ),
    "an ordinary join, column never empty": (
        f"SELECT t.name, SUM(o.total_amount) AS v FROM orders o JOIN outlets t "
        f"ON t.id = o.outlet_id WHERE {DONE} GROUP BY t.name"
    ),
    "lines to their order and product": (
        "SELECT p.name, SUM(i.line_total) AS v FROM order_items i JOIN orders o ON o.id = "
        "i.order_id "
        f"JOIN products p ON p.id = i.product_id WHERE {DONE} GROUP BY p.name"
    ),
    "nothing is added up or counted": (
        "SELECT o.order_no, c.segment FROM orders o JOIN customers c ON c.id = o.customer_id"
    ),
    "only the other table is counted": (
        "SELECT COUNT(DISTINCT c.id) AS n FROM orders o JOIN customers c ON c.id = o.customer_id"
    ),
    "the optional column joined to something that is not a key": (
        "SELECT COUNT(o.id) AS n FROM orders o JOIN customer_visits v "
        "ON v.customer_id = o.customer_id GROUP BY v.source"
    ),
}


@cases(NO_NOTE)
def test_no_note_where_the_join_leaves_nothing_out_or_cannot_be_classified(sql: str) -> None:
    assert passed(sql).notes == ()


def test_n2_is_withheld_when_the_metadata_does_not_say_the_link_is_one_to_one() -> None:
    sql = (
        "SELECT SUM(o.total_amount) AS v FROM orders o "
        f"JOIN customer_visits cv ON cv.order_id = o.id WHERE {DONE}"
    )
    guarded = check(sql, CATALOG)
    assert isinstance(guarded, Checked)
    facts = VOCABULARY.correctness_facts()
    # not unique any more: one order could have several visits, so it is not one-to-one
    not_unique = {**facts, "unique": {**facts["unique"], "customer_visits": (frozenset({"id"}),)}}
    assert check_correctness(guarded.sql, CATALOG, Facts(**not_unique)) == Reviewed()
    # not marked optional: nothing is known to be left out
    not_optional = {**facts, "optional": {"orders": facts["optional"]["orders"]}}
    assert check_correctness(guarded.sql, CATALOG, Facts(**not_optional)) == Reviewed()
    assert check_correctness(guarded.sql, CATALOG, FACTS) == Reviewed(notes=(NO_VISIT,))


def test_notes_hold_no_figure_and_do_not_call_the_join_wrong() -> None:
    for note in (NO_CUSTOMER, NO_VISIT, NO_ORDER):
        assert not any(ch.isdigit() for ch in note)
        assert "wrong" not in note and "incorrect" not in note and note.endswith("not included.")


# == the whole check =========================================================================

EVERYTHING = [
    *DISTINCT_SUMS.values(), *DISTINCT_ALLOWED.values(), *UNDONE.values(), *KEPT.values(),
    *NOT_LIMITED.values(), *LIMITED.values(), *CUT_SHORT.values(), *WHOLE_PERIOD.values(),
    *NO_GRAIN.values(), *A_REAL_COUNT.values(), *NO_NOTE.values(),
    *UNDONE_LATER.values(), *KEPT_LATER.values(),
]  # fmt: skip


@pytest.mark.parametrize("sql", EVERYTHING)
def test_the_check_never_changes_the_query(sql: str) -> None:
    guarded = check(sql, CATALOG)
    assert isinstance(guarded, Checked)
    before = sqlglot.parse_one(guarded.sql, read="postgres")
    check_correctness(guarded.sql, CATALOG, FACTS)
    assert sqlglot.parse_one(guarded.sql, read="postgres") == before


def test_a_refusal_carries_a_code_a_reason_and_no_sql() -> None:
    result = verdict(LINES)
    assert isinstance(result, Refused) and result.reason and not hasattr(result, "sql")


def test_sql_that_cannot_be_analysed_is_passed_not_crashed_on() -> None:
    for broken in ("SELECT FROM WHERE (((", "SELECT SUM(o.nope) FROM orders o"):
        result = check_correctness(broken, CATALOG, FACTS)
        assert isinstance(result, Reviewed) and result.uncertain


def test_with_no_facts_nothing_that_needs_them_is_judged() -> None:
    bare = Facts(unique={})
    for sql in (LINES, CUT_SHORT["BETWEEN two dates"], NO_GRAIN["a cross join"]):
        guarded = check(sql, CATALOG)
        assert isinstance(guarded, Checked)
        assert isinstance(check_correctness(guarded.sql, CATALOG, bare), Reviewed)


RECORDED: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "aggregation_verdicts.json").read_text()
)
CORRECT = [r for r in RECORDED if r["name"].startswith(("golden: ", "real model"))]


@pytest.mark.parametrize("recorded", CORRECT, ids=[r["name"] for r in CORRECT])
def test_no_correct_query_we_hold_is_refused(recorded: dict[str, Any]) -> None:
    """The 60 golden queries as approved in Step 3 and the 43 a real model ran."""
    assert len(CORRECT) == 103
    passed(recorded["sql"])


# == the metadata ==============================================================================


def test_the_facts_come_from_the_vocabulary_files() -> None:
    assert FACTS.status_table == "orders" and FACTS.status_column == "status"
    assert FACTS.status_values == ("completed", "cancelled", "refunded")
    assert FACTS.normal_status == "completed"
    assert set(FACTS.sales) == {"orders", "order_items"} and FACTS.line_links == {
        "order_items": "order_id"
    }
    assert set(FACTS.optional) == {"orders", "customer_visits"}
    assert FACTS.timestamps["orders"] == (frozenset({"ordered_at"}), "order_date")


def _with(table: str, **changes: Any) -> Vocabulary:
    tables = tuple(
        t.model_copy(update=changes) if t.table == table else t for t in VOCABULARY.tables
    )
    return Vocabulary(tables=tables, metrics=VOCABULARY.metrics)


def test_metadata_that_contradicts_the_tables_stops_the_app_at_start_up() -> None:
    orders = VOCABULARY.table("orders")
    lines = VOCABULARY.table("order_items")
    visits = VOCABULARY.table("customer_visits")
    assert orders.sales and lines.sales and visits.optional
    broken = [
        (
            "orders",
            {"sales": orders.sales.model_copy(update={"columns": ("nope",)})},
            "unknown sales columns",
        ),
        (
            "orders",
            {"sales": orders.sales.model_copy(update={"normal": "shipped"})},
            "not an allowed value",
        ),
        (
            "orders",
            {"sales": orders.sales.model_copy(update={"status": "channel", "normal": "x"})},
            "not an allowed value",
        ),
        (
            "order_items",
            {"sales": lines.sales.model_copy(update={"via": "nope"})},
            "has no column nope",
        ),
        (
            "order_items",
            {"sales": lines.sales.model_copy(update={"of": "outlets"})},
            "declares none",
        ),
        (
            "orders",
            {"optional": (orders.optional[0].model_copy(update={"column": "nope"}),)},
            "unknown column optional",
        ),
        (
            "orders",
            {"optional": (orders.optional[0].model_copy(update={"other_side_note": "x"}),)},
            "not a unique key by itself",
        ),
        ("customer_visits", {"unique": (("id",),)}, "not a unique key by itself"),
    ]
    for table, changes, message in broken:
        with pytest.raises(AssertionError, match=message):
            validate(_with(table, **changes))
    validate(VOCABULARY)
