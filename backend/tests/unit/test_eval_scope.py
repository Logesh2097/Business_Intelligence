"""PURE: a verification query must be provably limited to its company and outlets.

Verification queries run as the database owner, whom row security does not limit. So
the limit is checked by parsing the query, and the check fails closed: these are the
ways a filter could look present and not be.
"""

from __future__ import annotations

import pytest

from tests.evals.dataset import bind, load_dataset, problems_in, scope_problems

GOOD = (
    "SELECT SUM(o.total_amount) AS v FROM biz.orders o "
    "WHERE o.tenant_id = :tenant AND o.status = 'completed'"
)
GOOD_LIMITED = GOOD + " AND o.outlet_id = ANY(:outlets)"


def test_a_query_filtered_on_its_company_is_accepted() -> None:
    assert scope_problems(GOOD, limited=False) == []


def test_a_limited_user_also_needs_the_outlet_filter() -> None:
    assert scope_problems(GOOD_LIMITED, limited=True) == []
    assert scope_problems(GOOD, limited=True) == ["orders (o) has no o.outlet_id = ANY(:outlets)"]


def test_outlets_are_limited_by_their_own_id() -> None:
    by_id = "SELECT t.name FROM biz.outlets t WHERE t.tenant_id = :tenant AND t.id = ANY(:outlets)"
    assert scope_problems(by_id, limited=True) == []
    assert scope_problems(by_id.replace("t.id = ANY", "t.code = ANY"), limited=True)


def test_a_table_the_database_does_not_limit_by_outlet_needs_only_the_company() -> None:
    products = "SELECT p.name FROM biz.products p WHERE p.tenant_id = :tenant"
    assert scope_problems(products, limited=True) == []


BYPASSES = {
    "no filter at all": "SELECT SUM(o.total_amount) FROM biz.orders o",
    "the filter under OR": (
        "SELECT 1 FROM biz.orders o WHERE o.tenant_id = :tenant OR o.status = 'completed'"
    ),
    "OR true": "SELECT 1 FROM biz.orders o WHERE (o.tenant_id = :tenant OR TRUE)",
    "OR inside a bracket beside a real term": (
        "SELECT 1 FROM biz.orders o WHERE o.status = 'completed' "
        "AND (o.tenant_id = :tenant OR o.channel = 'online')"
    ),
    "negated": "SELECT 1 FROM biz.orders o WHERE NOT (o.tenant_id = :tenant)",
    "not equal": "SELECT 1 FROM biz.orders o WHERE o.tenant_id <> :tenant",
    "compared with itself": "SELECT 1 FROM biz.orders o WHERE o.tenant_id = o.tenant_id",
    "compared with a literal": (
        "SELECT 1 FROM biz.orders o WHERE o.tenant_id = '6f1d3c1e-5b7a-4c0e-9f43-2a1b0c9d8e7f'"
    ),
    "the wrong parameter": "SELECT 1 FROM biz.orders o WHERE o.tenant_id = :outlets",
    "another column": "SELECT 1 FROM biz.orders o WHERE o.id = :tenant",
    "the filter on another alias": (
        "SELECT 1 FROM biz.orders o JOIN biz.orders x ON x.id = o.id WHERE x.tenant_id = :tenant"
    ),
    "one of two tables filtered": (
        "SELECT 1 FROM biz.orders o JOIN biz.order_items i ON i.order_id = o.id "
        "WHERE o.tenant_id = :tenant"
    ),
    "the filter only in the ON of some other join": (
        "SELECT 1 FROM biz.orders o LEFT JOIN biz.outlets t ON t.id = o.outlet_id "
        "AND o.tenant_id = :tenant AND t.tenant_id = :tenant"
    ),
    "the filter in the ON of a RIGHT JOIN": (
        "SELECT 1 FROM biz.outlets t RIGHT JOIN biz.orders o ON o.outlet_id = t.id "
        "AND o.tenant_id = :tenant WHERE t.tenant_id = :tenant"
    ),
    "the filter in the ON of a FULL JOIN": (
        "SELECT 1 FROM biz.outlets t FULL JOIN biz.orders o ON o.outlet_id = t.id "
        "AND o.tenant_id = :tenant WHERE t.tenant_id = :tenant"
    ),
    "the filter in HAVING": (
        "SELECT o.tenant_id FROM biz.orders o GROUP BY o.tenant_id HAVING o.tenant_id = :tenant"
    ),
    "the filter in the outer query only": (
        "SELECT x.v FROM (SELECT o.tenant_id, o.total_amount AS v FROM biz.orders o) x "
        "WHERE x.tenant_id = :tenant"
    ),
    "an unfiltered table inside a CTE": (
        "WITH a AS (SELECT o.outlet_id FROM biz.orders o) "
        "SELECT t.name FROM biz.outlets t JOIN a ON a.outlet_id = t.id WHERE t.tenant_id = :tenant"
    ),
    "an unfiltered table inside a subquery in WHERE": (
        "SELECT t.name FROM biz.outlets t WHERE t.tenant_id = :tenant AND t.id IN "
        "(SELECT o.outlet_id FROM biz.orders o)"
    ),
    "an unfiltered table inside a scalar subquery": (
        "SELECT (SELECT COUNT(*) FROM biz.orders o) AS n FROM biz.outlets t "
        "WHERE t.tenant_id = :tenant"
    ),
    "an unfiltered arm of a UNION": (
        "SELECT o.id FROM biz.orders o WHERE o.tenant_id = :tenant "
        "UNION ALL SELECT o.id FROM biz.orders o"
    ),
    "a CTE named like a table, hiding it": (
        "WITH orders AS (SELECT o.id FROM biz.orders o) SELECT orders.id FROM orders"
    ),
    "a CTE given a table's name": (
        "WITH orders AS (SELECT 1 AS id) SELECT o.id FROM biz.orders o WHERE o.tenant_id = :tenant"
    ),
    "a table without the schema": "SELECT 1 FROM orders o WHERE o.tenant_id = :tenant",
    "a table of another schema": "SELECT 1 FROM app.users u WHERE u.tenant_id = :tenant",
    "an unknown table": "SELECT 1 FROM biz.secrets s WHERE s.tenant_id = :tenant",
    "a column with no alias in front": "SELECT 1 FROM biz.orders o WHERE tenant_id = :tenant",
    "an alias reused in an inner query": (
        "SELECT 1 FROM biz.outlets o WHERE o.tenant_id = :tenant AND EXISTS "
        "(SELECT 1 FROM biz.orders o WHERE o.status = 'completed')"
    ),
    "a function in FROM": (
        "SELECT 1 FROM generate_series(1, 2) g, biz.orders o WHERE o.tenant_id = :tenant"
    ),
    "two statements": GOOD + "; SELECT 1 FROM biz.orders o",
    "a write": "DELETE FROM biz.orders o WHERE o.tenant_id = :tenant",
    "a write inside a CTE": (
        "WITH d AS (DELETE FROM biz.orders o WHERE o.tenant_id = :tenant RETURNING o.id) "
        "SELECT 1 FROM d"
    ),
    "not SQL": "SELECT FROM WHERE (((",
    "empty": "",
}


@pytest.mark.parametrize("query", BYPASSES.values(), ids=BYPASSES.keys())
def test_a_query_that_is_not_provably_limited_to_its_company_is_refused(query: str) -> None:
    assert scope_problems(query, limited=False), f"accepted: {query}"


OUTLET_BYPASSES = {
    "under OR": GOOD + " AND (o.outlet_id = ANY(:outlets) OR o.channel = 'online')",
    "equal to the company instead": GOOD + " AND o.outlet_id = :tenant",
    "IN a subquery instead of the parameter": (
        GOOD + " AND o.outlet_id IN (SELECT t.id FROM biz.outlets t WHERE t.tenant_id = :tenant "
        "AND t.id = ANY(:outlets))"
    ),
    "only on the joined outlets table": (
        "SELECT 1 FROM biz.orders o JOIN biz.outlets t ON t.id = o.outlet_id "
        "AND t.tenant_id = :tenant AND t.id = ANY(:outlets) WHERE o.tenant_id = :tenant"
    ),
    "missing inside a correlated subquery": (
        "SELECT t.name, (SELECT COUNT(*) FROM biz.orders o WHERE o.tenant_id = :tenant "
        "AND o.outlet_id = t.id) AS n FROM biz.outlets t "
        "WHERE t.tenant_id = :tenant AND t.id = ANY(:outlets)"
    ),
}


@pytest.mark.parametrize("query", OUTLET_BYPASSES.values(), ids=OUTLET_BYPASSES.keys())
def test_a_query_that_is_not_provably_limited_to_its_outlets_is_refused(query: str) -> None:
    assert scope_problems(query, limited=True), f"accepted: {query}"


ACCEPTED = {
    "the filter in the ON of the join that brings the table in": (
        "SELECT 1 FROM biz.orders o JOIN biz.outlets t ON t.id = o.outlet_id "
        "AND t.tenant_id = :tenant WHERE o.tenant_id = :tenant"
    ),
    "the same on a LEFT JOIN": (
        "SELECT 1 FROM biz.outlets t LEFT JOIN biz.orders o ON o.outlet_id = t.id "
        "AND o.tenant_id = :tenant WHERE t.tenant_id = :tenant"
    ),
    "the parameter written first, in brackets": (
        "SELECT 1 FROM biz.orders o WHERE (:tenant = o.tenant_id) AND o.status = 'completed'"
    ),
    "every level filtered": (
        "WITH a AS (SELECT o.outlet_id FROM biz.orders o WHERE o.tenant_id = :tenant) "
        "SELECT t.name, (SELECT COUNT(*) FROM biz.expenses e WHERE e.tenant_id = :tenant "
        "AND e.outlet_id = t.id) AS n FROM biz.outlets t JOIN a ON a.outlet_id = t.id "
        "WHERE t.tenant_id = :tenant AND EXISTS (SELECT 1 FROM biz.products p "
        "WHERE p.tenant_id = :tenant)"
    ),
    "both arms of a UNION filtered": (
        "SELECT o.id FROM biz.orders o WHERE o.tenant_id = :tenant UNION ALL "
        "SELECT v.id FROM biz.customer_visits v WHERE v.tenant_id = :tenant"
    ),
    "no table at all": "SELECT 1 AS one",
}


@pytest.mark.parametrize("query", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_a_query_filtered_at_every_level_is_accepted(query: str) -> None:
    assert scope_problems(query, limited=False) == []


def test_binding_changes_only_the_two_parameters() -> None:
    query = "SELECT v::text FROM biz.x x WHERE x.tenant_id = :tenant AND x.id = ANY(:outlets)"
    bound = bind(query)
    assert "x.tenant_id = %(tenant)s" in bound and "ANY(%(outlets)s)" in bound
    assert "v::text" in bound


# -- the dataset as a document ------------------------------------------------------------------


def test_the_real_dataset_is_usable_and_every_verification_query_is_scoped() -> None:
    dataset = load_dataset()
    assert problems_in(dataset) == []
    for case in dataset.active:
        for part in case.parts:
            assert scope_problems(part.verify_sql, limited=case.limited) == [], case.id
            assert ":tenant" in part.verify_sql and "tenant" not in part.pipeline_sql


def changed(case_id: str, **changes: object) -> list[str]:
    dataset = load_dataset()
    cases = tuple(c.model_copy(update=changes) if c.id == case_id else c for c in dataset.cases)
    return problems_in(dataset.model_copy(update={"cases": cases}))


def test_a_dataset_that_contradicts_itself_is_refused() -> None:
    dataset = load_dataset()
    first = dataset.cases[0]
    assert "the header says 57" in problems_in(dataset.model_copy(update={"total": 57}))[0]
    twice = dataset.model_copy(update={"cases": (*dataset.cases[:-1], first)})
    assert any("the id is used 2 times" in p for p in problems_in(twice))
    assert any("asked 2 times" in p for p in problems_in(twice))

    assert any("must say why" in p for p in changed(first.id, status="pending"))
    assert any("must hold no expected values" in p for p in changed(first.id, status="pending"))
    no_filter = first.parts[0].model_copy(update={"verify_sql": "SELECT 1 FROM biz.orders o"})
    assert any("tenant_id = :tenant" in p for p in changed(first.id, parts=(no_filter,)))
    clock = first.parts[0].model_copy(
        update={"pipeline_sql": "SELECT MAX(p.created_at) AS revenue FROM products p"}
    )
    assert any("database clock" in p for p in changed(first.id, parts=(clock,)))
    assert any("one part per target" in p for p in changed(first.id, parts=()))


def test_a_limited_users_case_cannot_drop_its_outlet_filter() -> None:
    viewer = load_dataset().case("L1-viewer-revenue-by-outlet")
    assert viewer.limited
    loose = viewer.parts[0].model_copy(
        update={
            "verify_sql": viewer.parts[0].verify_sql.replace(" AND o.outlet_id = ANY(:outlets)", "")
        }
    )
    assert any("o.outlet_id = ANY(:outlets)" in p for p in changed(viewer.id, parts=(loose,)))
