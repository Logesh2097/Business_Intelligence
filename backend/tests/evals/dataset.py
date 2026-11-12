"""Loads golden.yaml and refuses a dataset that contradicts itself.

The most important check here is `scope_problems`. A verification query runs as the
database owner, whom row security does not limit, so the company and outlet limits
must be written in the query. That is checked by parsing it, and the check fails
closed: a table whose filter cannot be found, for any reason, is a problem.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import sqlglot
import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import traverse_scope

from app.agents.registry import AGENTS
from app.sqlguard.correctness import CODES

GOLDEN = Path(__file__).parent / "golden.yaml"
GROUPS = {
    "A": "Sales",
    "B": "Inventory",
    "C": "Customers",
    "D": "Purchases",
    "E": "Expenses",
    "F": "Cross-domain",
    "G": "Duplicate aggregation",
    "H": "Missing rows",
    "I": "Metric definition",
    "J": "SUM(DISTINCT)",
    "K": "Date ranges",
    "L": "Scope and security",
    "M": "Routing only",
}
GENERAL = "general"

# from migration 0001: the column row security limits by outlet, per table
OUTLET_COLUMN = {
    "orders": "outlet_id",
    "order_items": "outlet_id",
    "customer_visits": "outlet_id",
    "inventory_levels": "outlet_id",
    "stock_movements": "outlet_id",
    "purchase_orders": "outlet_id",
    "expenses": "outlet_id",
    "outlets": "id",
}
TENANT_ONLY = frozenset(
    {"categories", "products", "customers", "suppliers", "purchase_order_items",
     "expense_categories"}
)  # fmt: skip
BIZ_TABLES = frozenset(OUTLET_COLUMN) | TENANT_ONLY
# filled by the database's clock when rows are loaded: not reproducible
CLOCK_COLUMNS = ("created_at", "updated_at")

_WRITES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.TruncateTable, exp.Command, exp.Into, exp.Lock, exp.Set, exp.Grant, exp.Copy,
)  # fmt: skip


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Column(Frozen):
    name: str
    type: Literal["text", "money", "count", "number", "date", "month"]
    places: int = 2  # for number; money is always 2


class Expected(Frozen):
    order: Literal["any", "enforced"] = "any"
    rank_by: str | None = None  # with order enforced: rows equal on this may swap places
    columns: tuple[Column, ...]
    rows: tuple[dict[str, Any], ...]


class Part(Frozen):
    """What one agent, or the general analyst, must return."""

    agent: str
    verify_sql: str  # run as the owner, scope written out, read-only: the source of truth
    pipeline_sql: str  # a correct query as a model would write it; run under row security
    expected: Expected


class Trap(Frozen):
    """A query a model might write for this question that gives a wrong figure."""

    name: str
    why: str
    sql: str  # returns one row with one column, v
    gives: float  # the wrong figure, verified on bi_eval
    correct: float  # the right figure, for the reader; it is in `expected` too
    # what each check says about this query, each run alone on the guard's output
    aggregation_check: Literal["refused", "passed"]
    correctness_check: Literal["refused", "passed"]
    correctness_code: str = ""  # the refusal code, when refused
    notes: tuple[str, ...] = ()  # the notes the correctness check attaches, when passed

    @property
    def caught_by(self) -> str:
        """The first lock that stops it in the pipeline, or golden_only."""
        if self.aggregation_check == "refused":
            return "aggregation"
        return "correctness" if self.correctness_check == "refused" else "golden_only"


class Alternative(Frozen):
    route: str
    agents: tuple[str, ...] = ()
    why: str


class Route(Frozen):
    expect: Literal["agents", "general", "clarify", "unsupported"]
    agents: tuple[str, ...] = ()
    accept: tuple[Alternative, ...] = ()


class Scope(Frozen):
    company: str
    outlets: str | tuple[str, ...]  # "all", or the outlet names


class Period(Frozen):
    name: str
    start: date | None = Field(default=None, alias="from")
    end: date | None = Field(default=None, alias="to")


class Case(Frozen):
    id: str
    group: str
    status: Literal["verified", "pending"]
    pending_reason: str = ""
    tags: tuple[str, ...] = ()
    user: str = Field(alias="as")
    question: str
    scope: Scope
    period: Period
    metric: str
    route: Route
    compare: Literal["rows", "empty", "refusal"]
    expect_status: tuple[str, ...] = ()  # for refusal: the statuses that are right
    parts: tuple[Part, ...] = ()
    interpretation: str
    answer_must_contain: tuple[float, ...] = ()
    traps: tuple[Trap, ...] = ()

    @property
    def active(self) -> bool:
        return self.status == "verified"

    @property
    def limited(self) -> bool:
        return self.scope.outlets != "all"

    @property
    def route_kind(self) -> str:
        if self.route.expect == "agents":
            return "one agent" if len(self.route.agents) == 1 else "several agents"
        return self.route.expect


class Dataset(Frozen):
    total: int
    reference_date: date
    cases: tuple[Case, ...]

    @property
    def active(self) -> tuple[Case, ...]:
        return tuple(c for c in self.cases if c.active)

    @property
    def pending(self) -> tuple[Case, ...]:
        return tuple(c for c in self.cases if not c.active)

    def case(self, case_id: str) -> Case:
        return next(c for c in self.cases if c.id == case_id)

    def counts(self) -> dict[str, dict[str, dict[str, int]]]:
        """Questions per group and per route: total, active, pending."""

        def table(key: Any, names: list[str]) -> dict[str, dict[str, int]]:
            return {
                name: {
                    "total": sum(1 for c in self.cases if key(c) == name),
                    "active": sum(1 for c in self.active if key(c) == name),
                    "pending": sum(1 for c in self.pending if key(c) == name),
                }
                for name in names
            }

        routes = ["one agent", "several agents", "general", "clarify", "unsupported"]
        return {
            "groups": table(lambda c: c.group, list(GROUPS)),
            "routes": table(lambda c: c.route_kind, routes),
        }


# -- the scope of a verification query ---------------------------------------------------


def _conjuncts(condition: exp.Expr | None) -> list[exp.Expr]:
    if condition is None:
        return []
    condition = condition.unnest()
    if isinstance(condition, exp.And):
        return _conjuncts(condition.left) + _conjuncts(condition.right)
    return [condition]


def _is_column(node: exp.Expr, alias: str, column: str) -> bool:
    node = node.unnest()
    return (
        isinstance(node, exp.Column) and node.table.lower() == alias and node.name.lower() == column
    )


def _is_placeholder(node: exp.Expr, name: str) -> bool:
    node = node.unnest()
    return isinstance(node, exp.Placeholder) and str(node.this) == name


def _filters_company(part: exp.Expr, alias: str) -> bool:
    if not isinstance(part, exp.EQ):
        return False
    return (
        _is_column(part.left, alias, "tenant_id") and _is_placeholder(part.right, "tenant")
    ) or (_is_column(part.right, alias, "tenant_id") and _is_placeholder(part.left, "tenant"))


def _filters_outlets(part: exp.Expr, alias: str, column: str) -> bool:
    if not isinstance(part, exp.EQ) or not _is_column(part.left, alias, column):
        return False
    right = part.right.unnest()
    return isinstance(right, exp.Any) and _is_placeholder(right.this, "outlets")


def scope_problems(query: str, *, limited: bool) -> list[str]:
    """Why this owner-run query is not provably limited to the company (and outlets).

    Every business table named anywhere in the query (in a CTE, a subquery, a UNION
    arm) must carry `<alias>.tenant_id = :tenant` and, for a limited user and a table
    the database limits by outlet, `<alias>.<outlet column> = ANY(:outlets)`. A filter
    counts only as a top-level AND term of the WHERE of the same SELECT, or of the ON
    of the INNER or LEFT join that brings the table in. So a filter under OR, in an
    outer query, in HAVING, on another alias, or in the ON of some other join does
    not count. An empty list means the query is provably scoped.
    """
    try:
        statements = [s for s in sqlglot.parse(query, read="postgres") if s is not None]
    except (SqlglotError, RecursionError):
        return ["the query could not be parsed"]
    if len(statements) != 1:
        return ["exactly one statement is allowed"]
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return ["only a SELECT is allowed"]
    if next(tree.find_all(*_WRITES), None) is not None:
        return ["only reading is allowed"]

    try:
        scopes = traverse_scope(tree)
    except (SqlglotError, RecursionError, KeyError, ValueError, AttributeError, TypeError):
        return ["the query could not be analysed"]

    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    problems: list[str] = []
    checked: set[int] = set()

    for scope in scopes:
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue
        where = select.args.get("where")
        in_where = _conjuncts(where.this if where is not None else None)
        own_on: dict[int, list[exp.Expr]] = {}
        for join in select.args.get("joins") or []:
            side = str(join.args.get("side") or "").upper()
            if side in ("", "LEFT") and isinstance(join.this, exp.Table):
                own_on[id(join.this)] = _conjuncts(join.args.get("on"))

        for table in scope.tables:
            checked.add(id(table))
            name, schema = table.name.lower(), table.db.lower()
            if isinstance(table.this, exp.Func):
                problems.append("a function in FROM cannot be scoped")
                continue
            if not schema and not table.catalog:
                if name in ctes:
                    continue  # a CTE of this query: its own SELECT is checked
                problems.append(f"{name} is not written as biz.{name}")
                continue
            if schema != "biz" or table.catalog or name not in BIZ_TABLES:
                problems.append(f"{table.sql(dialect='postgres')} is not a business table")
                continue
            if name in ctes:
                problems.append(f"a CTE is named like the table {name}")
                continue
            alias = table.alias_or_name.lower()
            usable = in_where + own_on.get(id(table), [])
            if not any(_filters_company(part, alias) for part in usable):
                problems.append(f"{name} ({alias}) has no {alias}.tenant_id = :tenant")
            column = OUTLET_COLUMN.get(name)
            if (
                limited
                and column
                and not any(_filters_outlets(part, alias, column) for part in usable)
            ):
                problems.append(f"{name} ({alias}) has no {alias}.{column} = ANY(:outlets)")

    for table in tree.find_all(exp.Table):
        if id(table) not in checked:
            problems.append(f"{table.sql(dialect='postgres')} is outside every checked SELECT")
    return problems


_PARAMETER = re.compile(r"(?<![:\w]):(tenant|outlets)\b")


def bind(query: str) -> str:
    """`:tenant` and `:outlets` as psycopg writes them. Nothing else is changed."""
    return _PARAMETER.sub(r"%(\1)s", query)


# -- loading -----------------------------------------------------------------------------


def problems_in(dataset: Dataset) -> list[str]:
    """Everything wrong with the dataset as a document. Empty means usable."""
    found: list[str] = []
    if len(dataset.cases) != dataset.total:
        found.append(
            f"the header says {dataset.total} questions, the file has {len(dataset.cases)}"
        )
    for case_id, n in Counter(c.id for c in dataset.cases).items():
        if n > 1:
            found.append(f"{case_id}: the id is used {n} times")
    for (question, user), n in Counter((c.question.strip(), c.user) for c in dataset.cases).items():
        if n > 1:
            found.append(f"asked {n} times by {user}: {question}")

    for case in dataset.cases:
        where = case.id
        if case.group not in GROUPS or not case.id.startswith(case.group):
            found.append(f"{where}: group {case.group!r} is unknown or not the id's first letter")
        for agent in case.route.agents:
            if agent not in AGENTS:
                found.append(f"{where}: unknown agent {agent}")
        if (case.route.expect == "agents") != bool(case.route.agents):
            found.append(f"{where}: agents are listed if and only if the route is agents")

        if not case.active:
            if not case.pending_reason:
                found.append(f"{where}: a pending case must say why")
            if case.parts or case.traps or case.answer_must_contain:
                found.append(f"{where}: a pending case must hold no expected values")
            continue
        if case.pending_reason:
            found.append(f"{where}: a verified case has a pending reason")

        if case.compare == "refusal":
            if case.parts or not case.expect_status:
                found.append(f"{where}: a refusal has statuses and no expected rows")
            continue
        targets = list(case.route.agents) if case.route.expect == "agents" else [GENERAL]
        if [p.agent for p in case.parts] != targets:
            found.append(f"{where}: one part per target is needed, in order: {targets}")
        for part in case.parts:
            at = f"{where}/{part.agent}"
            names = [c.name for c in part.expected.columns]
            if len(set(names)) != len(names) or not names:
                found.append(f"{at}: column names must be present and distinct")
            for n, row in enumerate(part.expected.rows):
                if list(row) != names:
                    found.append(f"{at}: row {n + 1} does not have exactly the columns {names}")
            if (case.compare == "empty") != (len(part.expected.rows) == 0):
                found.append(f"{at}: rows are empty if and only if compare is empty")
            if part.expected.rank_by and part.expected.rank_by not in names:
                found.append(f"{at}: rank_by is not a column")
            if part.expected.rank_by and part.expected.order != "enforced":
                found.append(f"{at}: rank_by needs order: enforced")
            found += [f"{at}: {p}" for p in scope_problems(part.verify_sql, limited=case.limited)]
            for label, query in (
                ("verify_sql", part.verify_sql),
                ("pipeline_sql", part.pipeline_sql),
            ):
                if "%" in query:
                    found.append(f"{at}: {label} must not contain %")
                if any(re.search(rf"\b{c}\b", query) for c in CLOCK_COLUMNS):
                    found.append(f"{at}: {label} reads a column filled by the database clock")
            if ":tenant" in part.pipeline_sql or "tenant_id" in part.pipeline_sql:
                found.append(f"{at}: pipeline_sql must not name the company; row security does")
        for trap in case.traps:
            if trap.gives == trap.correct:
                found.append(f"{where}: trap {trap.name!r} gives the correct figure")
            if (trap.correctness_check == "refused") != bool(trap.correctness_code):
                found.append(f"{where}: trap {trap.name!r} has a code if and only if refused")
            if trap.correctness_code and trap.correctness_code not in CODES:
                found.append(f"{where}: trap {trap.name!r} has an unknown code")
            if trap.notes and trap.correctness_check == "refused":
                found.append(f"{where}: trap {trap.name!r} is refused, so it carries no notes")
    return found


@lru_cache
def load_dataset(path: Path = GOLDEN) -> Dataset:
    raw = yaml.safe_load(path.read_text())
    dataset = Dataset(
        total=raw["dataset"]["total"],
        reference_date=raw["dataset"]["reference_date"],
        cases=raw["cases"],
    )
    found = problems_in(dataset)
    if found:
        raise ValueError("golden.yaml is not usable:\n  " + "\n  ".join(found))
    return dataset
