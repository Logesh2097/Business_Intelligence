"""check(sql, catalog) -> Checked | Refused.

Works on the syntax tree, never on the text. What runs afterwards is the SQL printed
back from that tree, so nothing the parser did not understand reaches the database.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify

from app.sqlguard.functions import function_name, is_allowed

MAX_LENGTH = 8000
MAX_DEPTH = 6
DIALECT = "postgres"

# sqlglot warns when it falls back to an opaque "command" node; we refuse those anyway
logging.getLogger("sqlglot").setLevel(logging.ERROR)


@dataclass(frozen=True)
class Catalog:
    """What the caller may read: table -> {column: type}. Private columns are absent."""

    tables: dict[str, dict[str, str]]
    schema: str = "biz"


@dataclass(frozen=True)
class Checked:
    sql: str
    tables: frozenset[str] = field(default_factory=frozenset)
    limited_to: int = 0


@dataclass(frozen=True)
class Refused:
    code: str
    reason: str  # a sentence the model can act on; never shown to a user as is


# anything that changes data, structure or the session
_FORBIDDEN_STATEMENTS = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.TruncateTable, exp.Copy, exp.Command, exp.Set, exp.Transaction, exp.Commit,
    exp.Rollback, exp.Grant, exp.Use, exp.Pragma, exp.Describe, exp.Show, exp.Kill,
    exp.Analyze, exp.Comment, exp.LoadData, exp.Refresh,
)  # fmt: skip


def check(sql: str, catalog: Catalog, *, max_rows: int = 1000) -> Checked | Refused:
    if len(sql) > MAX_LENGTH:
        return Refused("too_large", f"The query is longer than {MAX_LENGTH} characters.")

    # 1. it parses, and it is exactly one statement
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except (SqlglotError, RecursionError):
        return Refused("parse_error", "The SQL could not be parsed as PostgreSQL.")
    if not statements:
        return Refused("parse_error", "There is no SQL statement.")
    if len(statements) > 1:
        return Refused("multiple_statements", "Write exactly one SELECT statement.")
    tree = statements[0]

    # 2. a SELECT (or a UNION / INTERSECT / EXCEPT of them), and nothing else anywhere inside
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return Refused("not_select", "Only a SELECT statement is allowed.")
    if next(tree.find_all(*_FORBIDDEN_STATEMENTS), None) is not None:
        return Refused("not_select", "Only reading is allowed: no statement may change data.")

    # 3. no SELECT ... INTO, no row locks
    if tree.find(exp.Into) is not None:
        return Refused("not_read_only", "SELECT ... INTO is not allowed.")
    if tree.find(exp.Lock) is not None:
        return Refused("not_read_only", "FOR UPDATE and FOR SHARE are not allowed.")

    # a query runs with no parameters, so a placeholder can only be a mistake or a trick
    if tree.find(exp.Placeholder, exp.Parameter) is not None:
        return Refused("parse_error", "Write literal values; placeholders are not allowed.")

    if _depth(tree) > MAX_DEPTH:
        return Refused("too_large", f"The query is nested more than {MAX_DEPTH} levels deep.")

    # 4. every table is on the allow-list, or a CTE of this statement
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    used: set[str] = set()
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Func):
            continue  # a set-returning function such as generate_series: checked in step 7
        name, db, cat = table.name.lower(), table.db.lower(), table.catalog.lower()
        if not db and not cat and name in cte_names:
            continue
        if cat or db not in ("", catalog.schema) or name not in catalog.tables:
            return Refused(
                "table_not_allowed",
                f"The table {table.sql(dialect=DIALECT)} is not available. "
                f"Available tables: {', '.join(sorted(catalog.tables))}.",
            )
        used.add(name)

    # 6. no star: it would pull columns nobody asked for and hide what is being read
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            return Refused("star_not_allowed", "Name the columns you need instead of using *.")

    # 7. every function is on the allow-list
    for func in tree.find_all(exp.Func):
        if not is_allowed(func):
            return Refused(
                "function_not_allowed", f"The function {function_name(func)} is not available."
            )

    # 5. every column exists and is one the caller may read (private columns are not
    #    in the catalog, so they resolve to nothing)
    try:
        qualify(
            tree.copy(),
            schema={catalog.schema: catalog.tables},
            db=catalog.schema,
            dialect=DIALECT,
            validate_qualify_columns=True,
            expand_stars=False,
            quote_identifiers=False,
        )
    except OptimizeError as exc:
        return Refused("column_not_allowed", _column_reason(str(exc)))
    except (SqlglotError, RecursionError, KeyError, ValueError, AttributeError, TypeError):
        return Refused("parse_error", "The query's columns could not be checked. Simplify it.")

    # 8. a LIMIT on the outermost query, no larger than the cap
    limited_to = _apply_limit(tree, max_rows)

    return Checked(
        sql=tree.sql(dialect=DIALECT, comments=False),
        tables=frozenset(used),
        limited_to=limited_to,
    )


def _depth(node: exp.Expression, level: int = 0) -> int:
    deepest = level
    for child in node.iter_expressions():
        step = 1 if isinstance(child, (exp.Subquery, exp.CTE)) else 0
        deepest = max(deepest, _depth(child, level + step))
    return deepest


def _apply_limit(tree: exp.Expression, max_rows: int) -> int:
    current = tree.args.get("limit")
    wanted = max_rows
    if isinstance(current, (exp.Limit, exp.Fetch)):
        count = current.args.get("expression") or current.args.get("count")
        if isinstance(count, exp.Literal) and count.is_int:
            wanted = min(int(count.this), max_rows)
    tree.set("limit", exp.Limit(expression=exp.Literal.number(wanted)))
    return wanted


def _column_reason(message: str) -> str:
    # sqlglot: "Unknown column: phone" or "Column 'name' could not be resolved. Line: 1, Col: 8"
    text = message.split(". Line:")[0].strip().rstrip(".")
    return (
        f"{text}. Use only the columns listed in the schema, and put the table's alias "
        "in front of a column when the query has more than one table."
    )
