"""The correctness check: safe SQL whose figure would still be wrong.

It runs third: the guard decides whether a query is safe to run, the aggregation
check (aggregation.py) refuses a sum over rows a join has repeated, and this module
refuses five further mistakes that can be proved from the SQL alone:

    sum_distinct            SUM(DISTINCT x) / AVG(DISTINCT x): removes equal VALUES, so
                            two different orders of the same amount count once
    left_join_undone        a LEFT JOIN whose unmatched rows the WHERE, or a later inner
                            join, can never keep
    missing_status_filter   a sum of sales amounts that no order-status condition
                            actually limits
    timestamp_period        a period that ends on a timestamp column with BETWEEN / <=
                            a date, which loses the last day
    count_without_grain     COUNT(*) over a join in which no table keeps its rows

and it adds a plain note to the answer's assumptions where a join leaves rows out by
construction, or where the statuses counted are not the ordinary ones. A note changes
no SQL and no figure.

What it is not:
- It never rewrites SQL. It answers Reviewed or Refused.
- It is not a security check. It runs only on SQL the guard has passed.
- It does not prove a query right. A wrong period, a wrong metric, an inner join that
  drops rows where a match is merely absent: all pass (docs/design/nl2sql.md §3c).
- When it cannot be sure it passes the query and records why (`Reviewed.uncertain`).
  A refusal is issued only when the aliases and joins were resolved and the mistake
  is clear.

Pure, like the guard: no import from the rest of the app, no connection.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.sqlguard.aggregation import Uncertain, _columns, _own, relations
from app.sqlguard.guard import DIALECT, Catalog, Refused

SUM_DISTINCT = "sum_distinct"
LEFT_JOIN_UNDONE = "left_join_undone"
MISSING_STATUS = "missing_status_filter"
TIMESTAMP_PERIOD = "timestamp_period"
COUNT_WITHOUT_GRAIN = "count_without_grain"
CODES = (SUM_DISTINCT, LEFT_JOIN_UNDONE, MISSING_STATUS, TIMESTAMP_PERIOD, COUNT_WITHOUT_GRAIN)

MAX_JOIN_NOTES = 2
_ERRORS = (SqlglotError, RecursionError, KeyError, ValueError, AttributeError, TypeError)


@dataclass(frozen=True)
class Facts:
    """What the check reasons from. Built from tables.yaml (Vocabulary.correctness_facts)."""

    unique: Mapping[str, tuple[frozenset[str], ...]]
    sales: Mapping[str, frozenset[str]] = field(default_factory=dict)  # table -> columns
    status_table: str = ""
    status_column: str = ""
    status_values: tuple[str, ...] = ()
    normal_status: str = ""
    line_links: Mapping[str, str] = field(default_factory=dict)  # line table -> order column
    # table -> column -> (note, note about the other table when the link is one-to-one)
    optional: Mapping[str, Mapping[str, tuple[str, str]]] = field(default_factory=dict)
    # table -> (its timestamp columns, the date column to use for periods)
    timestamps: Mapping[str, tuple[frozenset[str], str | None]] = field(default_factory=dict)


@dataclass(frozen=True)
class Reviewed:
    """Nothing clearly wrong was found."""

    notes: tuple[str, ...] = ()  # sentences for the answer's assumptions
    uncertain: tuple[str, ...] = ()  # what could not be decided, and was passed


class _Unsure(Exception):
    """One check cannot decide about one SELECT. That check passes it."""


def check_correctness(sql: str, catalog: Catalog, facts: Facts) -> Reviewed | Refused:
    """`sql` must already have passed the guard and the aggregation check. Never changed."""
    try:
        tree = sqlglot.parse_one(sql, read=DIALECT)
        tree = qualify(
            tree,
            schema={catalog.schema: catalog.tables},
            db=catalog.schema,
            dialect=DIALECT,
            validate_qualify_columns=True,
            expand_stars=False,
            quote_identifiers=False,
        )
        scopes = traverse_scope(tree)
    except _ERRORS:
        return Reviewed(uncertain=("the query could not be analysed",))

    notes: list[str] = []
    uncertain: list[str] = []
    for scope in scopes:
        if not isinstance(scope.expression, exp.Select):
            continue
        try:
            found = _Select(scope, facts)
        except _ERRORS:
            uncertain.append("a SELECT could not be analysed")
            continue
        for check in (
            _sum_distinct,
            _left_join_undone,
            _status,
            _timestamp_period,
            _count_without_grain,
            _join_notes,
        ):
            try:
                result = check(found)
            except _Unsure as why:
                uncertain.append(str(why))
                continue
            except _ERRORS:
                uncertain.append("a SELECT could not be analysed")
                continue
            if isinstance(result, Refused):
                return result
            notes += [n for n in result if n not in notes]
    return Reviewed(notes=tuple(notes), uncertain=tuple(dict.fromkeys(uncertain)))


# -- one SELECT, read once -----------------------------------------------------------------


@dataclass(frozen=True)
class _Join:
    alias: str
    side: str  # "", LEFT, RIGHT, FULL
    on: tuple[exp.Expr, ...]  # top-level AND terms
    before: frozenset[str]  # the aliases already in the FROM when this join is made
    unclear: bool  # NATURAL, USING, LATERAL: its meaning is not read here


class _Select:
    def __init__(self, scope: Scope, facts: Facts) -> None:
        self.facts = facts
        assert isinstance(scope.expression, exp.Select)
        self.node: exp.Select = scope.expression
        self.scope = scope
        self.tables: dict[str, str] = {}  # alias -> base table name
        self.derived: set[str] = set()  # aliases of subqueries, CTEs, functions
        for alias, (_, source) in scope.selected_sources.items():
            if isinstance(source, exp.Table) and not isinstance(source.this, exp.Func):
                self.tables[alias.lower()] = source.name.lower()
            else:
                self.derived.add(alias.lower())
        where = self.node.args.get("where")
        self.where: exp.Expr | None = where.this if where is not None else None
        self.where_terms = tuple(_terms(self.where))

        self.joins: list[_Join] = []
        from_ = self.node.args.get("from_") or self.node.args.get("from")
        seen: set[str] = set()
        if from_ is not None:
            seen.add(from_.this.alias_or_name.lower())
        for join in self.node.args.get("joins") or []:
            alias = join.this.alias_or_name.lower()
            unclear = bool(
                join.args.get("using")
                or (join.args.get("method") or "").upper() == "NATURAL"
                or isinstance(join.this, exp.Lateral)
            )
            self.joins.append(
                _Join(
                    alias=alias,
                    side=str(join.args.get("side") or "").upper(),
                    on=tuple(_terms(join.args.get("on"))),
                    before=frozenset(seen),
                    unclear=unclear,
                )
            )
            seen.add(alias)

    def table_of(self, column: exp.Column) -> str | None:
        return self.tables.get(column.table.lower())

    def nullable(self, join: _Join) -> frozenset[str]:
        """The aliases this outer join fills with NULLs when there is no match."""
        if join.side == "LEFT":
            return frozenset({join.alias})
        if join.side == "RIGHT":
            return join.before
        if join.side == "FULL":
            return join.before | {join.alias}
        return frozenset()


def _terms(condition: exp.Expr | None) -> list[exp.Expr]:
    """The top-level AND terms of a condition."""
    if condition is None:
        return []
    condition = condition.unnest()
    if isinstance(condition, exp.And):
        return _terms(condition.left) + _terms(condition.right)
    return [condition]


def _is_distinct(aggregate: exp.Expr) -> bool:
    return isinstance(aggregate.this, exp.Distinct) or bool(aggregate.args.get("distinct"))


def _windowed(aggregate: exp.Expr) -> bool:
    parent = aggregate.parent
    if isinstance(parent, exp.Filter):
        parent = parent.parent
    return isinstance(parent, exp.Window)


def _show(node: exp.Expr) -> str:
    return node.sql(dialect=DIALECT)


# -- P1.1  SUM(DISTINCT), AVG(DISTINCT) --------------------------------------------------


def _sum_distinct(select: _Select) -> Refused | list[str]:
    for aggregate in _own(select.node, (exp.Sum, exp.Avg)):
        if not _is_distinct(aggregate):
            continue
        columns = list(_columns(aggregate))
        if not columns:
            continue  # a constant: nothing to get wrong
        aliases = {c.table.lower() for c in columns}
        if not aliases <= set(select.tables):
            raise _Unsure("a DISTINCT sum over a column of a subquery or CTE")
        if len(aliases) == 1:
            alias = next(iter(aliases))
            group = select.node.args.get("group")
            grouped = {
                g.name.lower()
                for g in (group.expressions if group is not None else [])
                if isinstance(g, exp.Column) and g.table.lower() == alias
            }
            keys = select.facts.unique.get(select.tables[alias], ())
            if any(key <= grouped for key in keys):
                continue  # one record per group: DISTINCT removes only a join's repeats
        name = "SUM" if isinstance(aggregate, exp.Sum) else "AVG"
        return Refused(
            SUM_DISTINCT,
            f"{_show(aggregate)} is wrong: DISTINCT removes equal values, not repeated rows, "
            "so two different records that hold the same amount are counted once. "
            f"Write {name}(...) without DISTINCT. If a join repeats the rows, do not hide it "
            "with DISTINCT: add up a column of the table that has one row per line, or work "
            "out each figure in its own subquery and join the subqueries. "
            "COUNT(DISTINCT ...) is fine.",
        )
    return []


# -- P1.2  a LEFT JOIN the WHERE undoes: three-valued logic ------------------------------

T, F, N = "T", "F", "N"
_ANY = frozenset({T, F, N})
_STRICT = (
    exp.Cast, exp.Neg, exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Lower, exp.Upper,
    exp.Trim, exp.Round, exp.Abs, exp.Extract, exp.TimestampTrunc, exp.DateTrunc, exp.Paren,
)  # fmt: skip
_COMPARISONS = (
    exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike, exp.SimilarTo,
    exp.RegexpLike,
)  # fmt: skip


def _is_null(node: exp.Expr, missing: frozenset[str]) -> bool:
    """Is this value certainly NULL when the tables in `missing` have no row?"""
    node = node.unnest()
    if isinstance(node, exp.Null):
        return True
    if isinstance(node, exp.Column):
        return node.table.lower() in missing
    if isinstance(node, _STRICT):
        return any(
            _is_null(child, missing)
            for child in node.iter_expressions()
            if not isinstance(child, exp.DataType)
        )
    return False  # COALESCE, CASE, NULLIF, a subquery, an unlisted function: unknown


def _and(left: frozenset[str], right: frozenset[str]) -> frozenset[str]:
    out = set()
    for a in left:
        for b in right:
            out.add(F if F in (a, b) else N if N in (a, b) else T)
    return frozenset(out)


def _or(left: frozenset[str], right: frozenset[str]) -> frozenset[str]:
    out = set()
    for a in left:
        for b in right:
            out.add(T if T in (a, b) else N if N in (a, b) else F)
    return frozenset(out)


def _not(values: frozenset[str]) -> frozenset[str]:
    return frozenset({T: F, F: T, N: N}[v] for v in values)


def _values(node: exp.Expr, missing: frozenset[str]) -> frozenset[str]:
    """Every truth value this condition could take when `missing` has no row."""
    node = node.unnest()
    if isinstance(node, exp.And):
        return _and(_values(node.left, missing), _values(node.right, missing))
    if isinstance(node, exp.Or):
        return _or(_values(node.left, missing), _values(node.right, missing))
    if isinstance(node, exp.Not):
        return _not(_values(node.this, missing))
    if isinstance(node, exp.Boolean):
        return frozenset({T if node.this else F})
    if isinstance(node, exp.Null):
        return frozenset({N})
    if isinstance(node, exp.Column):  # a bare boolean column
        return frozenset({N}) if node.table.lower() in missing else _ANY
    if isinstance(node, _COMPARISONS):
        null = _is_null(node.left, missing) or _is_null(node.right, missing)
        return frozenset({N}) if null else _ANY
    if isinstance(node, exp.Between):
        if _is_null(node.this, missing):
            return frozenset({N})
        if _is_null(node.args["low"], missing) or _is_null(node.args["high"], missing):
            return frozenset({F, N})
        return _ANY
    if isinstance(node, exp.In):
        if not _is_null(node.this, missing):
            return _ANY
        # NULL IN (a, b) is NULL; NULL IN (an empty subquery) is FALSE
        listed = node.args.get("expressions") and not node.args.get("query")
        return frozenset({N}) if listed else frozenset({F, N})
    if isinstance(node, exp.Is):
        if not _is_null(node.this, missing):
            return _ANY
        other = node.expression.unnest()
        if isinstance(other, exp.Null):
            found = frozenset({T})  # IS NULL
        elif isinstance(other, exp.Boolean):
            found = frozenset({F})  # NULL IS TRUE, NULL IS FALSE
        else:
            return _ANY
        # SQLGlot writes IS NOT NULL either as NOT (x IS NULL) or as IS with `negate`
        return _not(found) if node.args.get("negate") else found
    return _ANY  # IS DISTINCT FROM, EXISTS, COALESCE(...), anything unlisted


def _left_join_undone(select: _Select) -> Refused | list[str]:
    unclear = False
    for index, join in enumerate(select.joins):
        if join.side not in ("LEFT", "RIGHT"):
            unclear = unclear or join.side == "FULL" or join.unclear
            continue
        if join.unclear:
            unclear = True
            continue
        missing = select.nullable(join)
        names = ", ".join(sorted(select.tables.get(a, a) for a in missing))
        kept = "left" if join.side == "LEFT" else "right"
        if select.where is not None and T not in _values(select.where, missing):
            return Refused(
                LEFT_JOIN_UNDONE,
                f"The {join.side} JOIN to {names} has no effect: the WHERE clause can never be "
                f"true for a {kept}-side row that has no match in {names}, so those rows are "
                "removed and the join behaves as an inner join. Decide which is meant. "
                f"If rows without a match in {names} must be kept, move the conditions on "
                f"{names} from WHERE into the join's ON. If only matching rows are wanted, "
                f"write JOIN instead of {join.side} JOIN. To find the rows WITHOUT a match, "
                "keep only an IS NULL test in WHERE.",
            )
        # a LATER inner join whose ON needs a row of the table that may be missing: the
        # same proof, applied to that ON instead of to WHERE
        for later in select.joins[index + 1 :]:
            if later.side != "" or later.unclear or not later.on:
                continue
            if not any(
                c.table.lower() in missing for t in later.on for c in t.find_all(exp.Column)
            ):
                continue  # it joins on another table: it does not touch the outer join
            possible = frozenset({T})
            for term in later.on:
                possible = _and(possible, _values(term, missing))
            if T in possible:
                continue
            after = select.tables.get(later.alias, later.alias)
            return Refused(
                LEFT_JOIN_UNDONE,
                f"The {join.side} JOIN to {names} has no effect: the later JOIN to {after} can "
                f"only match a row that has a match in {names}, so every {kept}-side row "
                f"without one is removed and the {join.side} JOIN behaves as an inner join. "
                f"Decide which is meant. If rows without a match in {names} must be kept, "
                f"make the join to {after} a LEFT JOIN as well. If only matching rows are "
                f"wanted, write JOIN instead of {join.side} JOIN.",
            )
    if unclear and select.where is not None:
        raise _Unsure("a FULL, NATURAL, USING or LATERAL join beside a WHERE clause")
    return []


# -- P1.3  a sales figure no status condition limits -------------------------------------


@dataclass(frozen=True)
class _Decision:
    """What a status condition lets through. `included` is None when it cannot be told."""

    included: frozenset[str] | None = None
    grouped: bool = False


def _status_column(node: exp.Expr, select: _Select, alias: str) -> bool:
    node = node.unnest()
    while isinstance(node, (exp.Lower, exp.Upper, exp.Trim)):
        node = node.this.unnest()
    return (
        isinstance(node, exp.Column)
        and node.table.lower() == alias
        and node.name.lower() == select.facts.status_column
    )


def _literal(node: exp.Expr) -> str | None:
    node = node.unnest()
    return str(node.this) if isinstance(node, exp.Literal) and node.is_string else None


def _restricts(term: exp.Expr, select: _Select, alias: str) -> _Decision | None:
    """Does this condition pin `alias`.status to literal values? What does it let through?"""
    known = {v.lower(): v for v in select.facts.status_values}
    everything = frozenset(select.facts.status_values)

    def named(values: list[str | None]) -> frozenset[str] | None:
        if any(v is None or v.lower() not in known for v in values):
            return None
        return frozenset(known[v.lower()] for v in values if v is not None)

    def read(node: exp.Expr) -> tuple[bool, frozenset[str] | None]:
        """(is it a restriction on this alias's status, the statuses it lets through)."""
        node = node.unnest()
        if isinstance(node, exp.Not):
            inner, included = read(node.this)
            return inner, (everything - included if included is not None else None)
        if isinstance(node, exp.Or):
            left, right = read(node.left), read(node.right)
            if not (left[0] and right[0]):
                return False, None
            both = None if left[1] is None or right[1] is None else left[1] | right[1]
            return True, both
        if isinstance(node, (exp.EQ, exp.NEQ)):
            for column, value in ((node.left, node.right), (node.right, node.left)):
                if _status_column(column, select, alias) and _literal(value) is not None:
                    included = named([_literal(value)])
                    if isinstance(node, exp.NEQ) and included is not None:
                        included = everything - included
                    return True, included
            return False, None
        if isinstance(node, exp.In) and not node.args.get("query"):
            values = [_literal(v) for v in node.expressions]
            if _status_column(node.this, select, alias) and values and None not in values:
                return True, named(values)
        return False, None

    is_restriction, included = read(term)
    return _Decision(included=included) if is_restriction else None


def _mentions_status(node: exp.Expr, select: _Select, alias: str) -> bool:
    return any(
        c.table.lower() == alias and c.name.lower() == select.facts.status_column
        for c in node.find_all(exp.Column)
    )


def _in_subquery(select: _Select, line: str, line_column: str) -> _Decision | None:
    """E6: EXISTS / IN over the orders of the summed lines, as a top-level WHERE term."""
    facts = select.facts
    unsure = False
    for term in select.where_terms:
        negated = isinstance(term, exp.Not)
        inner = term.this.unnest() if negated else term
        subquery: exp.Expr | None = None
        correlated_by_in = False
        if isinstance(inner, exp.Exists):
            subquery = inner.this
        elif isinstance(inner, exp.In) and inner.args.get("query") is not None:
            left = inner.this.unnest()
            if (
                isinstance(left, exp.Column)
                and left.table.lower() == line
                and left.name.lower() == line_column
            ):
                subquery, correlated_by_in = inner.args["query"], True
        if subquery is None:
            if any(
                t.name.lower() == facts.status_table for t in term.find_all(exp.Table)
            ) and _references(term, line):
                unsure = True  # orders is consulted in a form not read here
            continue
        body = subquery.unnest()
        if isinstance(body, exp.Subquery):
            body = body.this
        if not isinstance(body, exp.Select):
            unsure = True
            continue
        orders = _orders_aliases(body, facts.status_table)
        where = body.args.get("where")
        terms = _terms(where.this if where is not None else None)
        for alias in orders:
            if correlated_by_in:
                shown = [e.unalias().unnest() for e in body.expressions]
                linked = (
                    len(shown) == 1
                    and isinstance(shown[0], exp.Column)
                    and shown[0].table.lower() == alias
                    and shown[0].name.lower() == "id"
                )
            else:
                linked = any(_links(t, alias, "id", line, line_column) for t in terms)
            if not linked:
                continue
            inner_select = _Shadow(select, {alias: facts.status_table})
            for candidate in terms:
                decision = _restricts(candidate, inner_select, alias)  # type: ignore[arg-type]
                if decision is None:
                    continue
                if negated and decision.included is not None:
                    return _Decision(frozenset(facts.status_values) - decision.included)
                return _Decision(None) if negated else decision
            if any(_mentions_status(t, inner_select, alias) for t in terms):  # type: ignore[arg-type]
                unsure = True
    if unsure:
        raise _Unsure("an order-status condition inside a subquery in a form not read here")
    return None


class _Shadow:
    """The facts of an outer SELECT with the aliases of an inner one."""

    def __init__(self, outer: _Select, tables: dict[str, str]) -> None:
        self.facts = outer.facts
        self.tables = tables


def _orders_aliases(body: exp.Select, orders: str) -> list[str]:
    found = []
    sources = [body.args.get("from_") or body.args.get("from")] + list(body.args.get("joins") or [])
    for source in sources:
        table = source.this if source is not None else None
        if isinstance(table, exp.Table) and table.name.lower() == orders:
            found.append(table.alias_or_name.lower())
    return found


def _references(node: exp.Expr, alias: str) -> bool:
    return any(c.table.lower() == alias for c in node.find_all(exp.Column))


def _links(term: exp.Expr, a: str, a_column: str, b: str, b_column: str) -> bool:
    """Is this term the plain equality a.a_column = b.b_column?"""
    if not isinstance(term, exp.EQ):
        return False
    left, right = term.left.unnest(), term.right.unnest()
    if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
        return False
    pair = {(left.table.lower(), left.name.lower()), (right.table.lower(), right.name.lower())}
    return pair == {(a, a_column), (b, b_column)}


def _conditional(
    aggregate: exp.Expr, select: _Select, summed: str, orders: str
) -> _Decision | None:
    """E4: SUM(...) FILTER (WHERE status ...) or SUM(CASE WHEN status ... THEN x END)."""
    if isinstance(aggregate.parent, exp.Filter):
        condition = aggregate.parent.expression
        inner = condition.this if isinstance(condition, exp.Where) else condition
        for term in _terms(inner):
            decision = _restricts(term, select, orders)
            if decision is not None:
                return decision
    sales = select.facts.sales[select.tables[summed]]
    amounts = [
        c for c in _columns(aggregate) if c.table.lower() == summed and c.name.lower() in sales
    ]
    decisions: list[_Decision] = []
    outside = 0
    for amount in amounts:
        branch = amount.find_ancestor(exp.If)
        case = amount.find_ancestor(exp.Case)
        if branch is None or case is None or not _inside(case, aggregate):
            outside += 1
            continue
        default = case.args.get("default")
        fallback = default.unnest() if default is not None else None
        harmless = (
            fallback is None
            or isinstance(fallback, exp.Null)
            or (_numeric(fallback) and float(fallback.this) == 0)
        )
        in_then = any(c is amount for c in branch.args["true"].find_all(exp.Column))
        found = None
        if harmless and in_then:
            for term in _terms(branch.this):
                found = found or _restricts(term, select, orders)
        if found is None:
            raise _Unsure("a CASE inside a sales sum in a form not read here")
        decisions.append(found)
    if not decisions:
        return None
    if outside:
        raise _Unsure("a sales sum only partly inside a CASE")
    included = [d.included for d in decisions]
    return decisions[0] if all(i == included[0] for i in included) else _Decision(None)


def _inside(node: exp.Expr, ancestor: exp.Expr) -> bool:
    parent = node.parent
    while parent is not None:
        if parent is ancestor:
            return True
        parent = parent.parent
    return False


def _numeric(node: exp.Expr) -> bool:
    return isinstance(node, exp.Literal) and not node.is_string


def _status(select: _Select) -> Refused | list[str]:
    facts = select.facts
    if not facts.sales or not facts.status_column:
        return []
    notes: list[str] = []
    for aggregate in _own(select.node, (exp.Sum, exp.Avg)):
        if _windowed(aggregate):
            continue
        summed = set()
        for column in _columns(aggregate):
            table = select.table_of(column)
            if table is not None and column.name.lower() in facts.sales.get(table, ()):
                summed.add(column.table.lower())
        for alias in sorted(summed):
            decision = _decision_for(aggregate, select, alias)
            if isinstance(decision, Refused):
                return decision
            note = _status_note(decision, facts)
            if note and note not in notes:
                notes.append(note)
    return notes


def _decision_for(aggregate: exp.Expr, select: _Select, summed: str) -> _Decision | Refused:
    facts = select.facts
    table = select.tables[summed]
    is_line = table != facts.status_table
    if any(j.unclear for j in select.joins):
        raise _Unsure("a NATURAL, USING or LATERAL join around a sales sum")

    all_terms = list(select.where_terms) + [t for j in select.joins for t in j.on]
    if is_line:
        column = facts.line_links[table]
        governing = [
            alias
            for alias, name in select.tables.items()
            if name == facts.status_table
            and any(_links(t, alias, "id", summed, column) for t in all_terms)
        ]
        to_derived = any(
            isinstance(t, exp.EQ)
            and _references(t, summed)
            and any(
                c.table.lower() == summed and c.name.lower() == column
                for c in t.find_all(exp.Column)
            )
            and any(c.table.lower() in select.derived for c in t.find_all(exp.Column))
            for t in all_terms
        )
    else:
        column, governing, to_derived = "", [summed], False

    unsure: str | None = None
    for orders in governing:
        # E5: the result is shown per status
        group = select.node.args.get("group")
        for key in group.expressions if group is not None else []:
            if _status_column(key, select, orders):
                return _Decision(grouped=True)
        # E1: in WHERE
        for term in select.where_terms:
            decision = _restricts(term, select, orders)
            if decision is not None:
                return decision
        # E2, E3: in a join's ON, if a row that fails it is dropped or made NULL
        for join in select.joins:
            for term in join.on:
                decision = _restricts(term, select, orders)
                if decision is None:
                    continue
                # an inner join's ON filters like WHERE. An outer join's ON limits only
                # the side it fills with NULLs: the preserved side keeps every row.
                dropped = join.side == ""
                nulled = join.side in ("LEFT", "RIGHT") and summed in select.nullable(join)
                if dropped or nulled:
                    return decision
        # E4: the aggregate is itself conditional on the status
        conditional = _conditional(aggregate, select, summed, orders)
        if conditional is not None:
            return conditional
        # U3: the status is compared with something that is not a literal
        for term in all_terms:
            if _mentions_status(term, select, orders) and isinstance(
                term.unnest(), (exp.EQ, exp.NEQ, exp.In)
            ):
                literal = [_literal(e) for e in term.unnest().iter_expressions()]
                if not any(v is not None for v in literal) and not _self_equal(term):
                    unsure = "an order status compared with something that is not a literal"

    if is_line:
        # E6: EXISTS / IN over the orders of these lines
        decision = _in_subquery(select, summed, column)
        if decision is not None:
            return decision
        if to_derived:
            raise _Unsure("order lines joined to a subquery or CTE instead of orders")
    if unsure:
        raise _Unsure(unsure)

    shown = _show(aggregate.parent if isinstance(aggregate.parent, exp.Filter) else aggregate)
    normal = facts.normal_status
    if is_line and not governing:
        why = (
            f"{table} holds the lines of every order, cancelled and refunded ones too, and "
            f"this query does not join it to {facts.status_table}"
        )
    else:
        why = _why_not_limited(select, governing, summed)
    return Refused(
        MISSING_STATUS,
        f"{shown} adds up sales figures, but nothing limits them by order status: {why}. "
        f"Write a condition on {facts.status_table}.{facts.status_column} that really limits "
        "the rows: in WHERE, or in the ON of a plain JOIN, or as "
        f"SUM(...) FILTER (WHERE {facts.status_column} = ...). "
        f"Join {table} to {facts.status_table} on the order id if it is not joined. "
        f"Use the statuses the question asks for; for ordinary sales that is '{normal}'.",
    )


def _self_equal(term: exp.Expr) -> bool:
    node = term.unnest()
    return isinstance(node, exp.EQ) and node.left.unnest() == node.right.unnest()


def _why_not_limited(select: _Select, governing: list[str], summed: str) -> str:
    facts = select.facts
    for orders in governing:
        for join in select.joins:
            if any(_restricts(t, select, orders) is not None for t in join.on):
                return (
                    f"the status condition is in the ON of a {join.side} JOIN, which keeps "
                    f"every row of {select.tables[summed]} whether or not it matches"
                )
        for term in list(select.where_terms) + [t for j in select.joins for t in j.on]:
            if isinstance(term.unnest(), exp.Or) and _mentions_status(term, select, orders):
                return "the status condition is under OR, so rows of any status can pass"
            if _mentions_status(term, select, orders):
                return f"the condition {_show(term)} does not pin the status to a value"
    return f"there is no condition on {facts.status_table}.{facts.status_column}"


def _status_note(decision: _Decision, facts: Facts) -> str | None:
    """N3, from the actual condition and the allowed values. Never a guessed status."""
    if decision.grouped:
        return "Results are shown separately for each order status."
    included = decision.included
    if included is None or included == frozenset({facts.normal_status}):
        return None  # the ordinary case, or a condition that cannot be described
    if not included:
        return None
    ordered = [v for v in facts.status_values if v in included]
    if included == frozenset(facts.status_values):
        return f"Includes orders of every status: {', '.join(ordered)}."
    if len(ordered) == 1:
        return f"Counts {ordered[0]} orders only."
    return f"Counts orders with status: {', '.join(ordered)}."


# -- P2.1  a period that ends on a timestamp ----------------------------------------------

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MIDNIGHT = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]00:00(:00(\.0+)?)?([+-]\d{2}(:?\d{2})?|Z)?$")
_MOMENT = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")


def _end_kind(node: exp.Expr) -> str:
    """date | midnight | time_of_day | other"""
    node = node.unnest()
    to_date = False
    while isinstance(node, exp.Cast):
        to_date = to_date or node.to.this == exp.DataType.Type.DATE
        node = node.this.unnest()
    if not (isinstance(node, exp.Literal) and node.is_string):
        return "other"
    text = str(node.this).strip()
    if _DATE.match(text) or (to_date and _MOMENT.match(text)):
        return "date"
    if _MIDNIGHT.match(text):
        return "midnight"
    return "time_of_day" if _MOMENT.match(text) else "other"


def _timestamp_period(select: _Select) -> Refused | list[str]:
    facts = select.facts

    def stamp(node: exp.Expr) -> tuple[str, str, str | None] | None:
        node = node.unnest()
        if not isinstance(node, exp.Column):
            return None  # CAST(ts AS DATE), EXTRACT(...), DATE_TRUNC(...): not judged
        table = select.table_of(node)
        if table is None or table not in facts.timestamps:
            return None
        columns, date_column = facts.timestamps[table]
        return (table, node.name.lower(), date_column) if node.name.lower() in columns else None

    conditions = ([select.where] if select.where is not None else []) + [
        t for j in select.joins for t in j.on
    ]
    unsure = False
    for condition in conditions:
        for node in _own(condition, (exp.Between, exp.LTE, exp.GTE, exp.EQ)):
            if isinstance(node, exp.Between):
                found, end = stamp(node.this), node.args["high"]
            elif isinstance(node, exp.LTE):
                found, end = stamp(node.left), node.right
            elif isinstance(node, exp.GTE):
                found, end = stamp(node.right), node.left
            elif isinstance(node, exp.EQ):
                found = stamp(node.left) or stamp(node.right)
                end = node.right if stamp(node.left) else node.left
            else:
                continue
            if found is None:
                continue
            kind = _end_kind(end)
            if kind in ("date", "midnight"):
                table, column, date_column = found
                exact = "matches only the first instant of that day"
                loses = exact if isinstance(node, exp.EQ) else "stops at the start of that day"
                return Refused(
                    TIMESTAMP_PERIOD,
                    f"{_show(node)} loses data: {table}.{column} is a date and time, and a "
                    f"date compared with it means midnight, so the condition {loses} and "
                    "everything later on the last day is left out. "
                    f"Filter the period on {table}.{date_column} instead "
                    f"({date_column} BETWEEN the first and the last day). If the timestamp "
                    f"must be used, write a half-open range: {column} >= the first day AND "
                    f"{column} < the day after the last day.",
                )
            if not isinstance(node, exp.EQ):
                unsure = True  # an inclusive end with a time of day, or not a literal
    if unsure:
        raise _Unsure("a period ending on a timestamp with an inclusive end that is not a date")
    return []


# -- P2.3  COUNT(*) where no table keeps its rows ------------------------------------------


def _count_without_grain(select: _Select) -> Refused | list[str]:
    counts = []
    for projection in select.node.expressions:
        for count in _own(projection, exp.Count):
            inner = count.this.unnest() if count.this is not None else None
            star = isinstance(inner, (exp.Star, exp.Literal)) and not _is_distinct(count)
            if star and not _windowed(count):
                counts.append(count)
    if not counts:
        return []
    try:
        found = relations(select.scope, select.facts.unique)
    except Uncertain as why:
        raise _Unsure(f"COUNT(*) over {why}") from None
    if len(found.sources) < 2 or found.grain:
        return []  # one table, or a table whose rows are counted: a real count
    tables = ", ".join(sorted(set(found.sources.values())))
    return Refused(
        COUNT_WITHOUT_GRAIN,
        f"{_show(counts[0])} does not count any one kind of record here: {tables} are joined "
        "in a way that repeats the rows of every one of them, so it counts combinations of "
        "rows. Count the rows of one table instead: COUNT(DISTINCT <that table's id>), or "
        "count each table in its own subquery and join the subqueries. If the number of "
        "combinations really is wanted, count over a subquery that returns them.",
    )


# -- P2.2  notes: rows an inner join leaves out by construction ----------------------------


def _join_notes(select: _Select) -> Refused | list[str]:
    facts = select.facts
    if not facts.optional:
        return []
    counted: set[str] = set()
    for aggregate in _own(select.node, (exp.Sum, exp.Avg, exp.Count)):
        if _windowed(aggregate):
            continue
        inner = aggregate.this.unnest() if aggregate.this is not None else None
        if isinstance(aggregate, exp.Count) and isinstance(inner, (exp.Star, exp.Literal)):
            counted |= set(select.tables)  # COUNT(*) counts the rows of the whole join
        counted |= {c.table.lower() for c in _columns(aggregate)}
    if not counted:
        return []

    # an outer join excludes nothing: only tables that no outer join touches are read
    outer = {a for j in select.joins if j.side for a in select.nullable(j) | j.before | {j.alias}}
    if any(j.unclear for j in select.joins):
        return []
    terms = list(select.where_terms) + [t for j in select.joins if not j.side for t in j.on]
    notes: list[str] = []
    for term in terms:
        if not isinstance(term, exp.EQ):
            continue
        left, right = term.left.unnest(), term.right.unnest()
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue
        for column, other in ((left, right), (right, left)):
            alias, far = column.table.lower(), other.table.lower()
            table, far_table = select.tables.get(alias), select.tables.get(far)
            if table is None or far_table is None or alias == far or {alias, far} & outer:
                continue
            marked = facts.optional.get(table, {}).get(column.name.lower())
            # the other side must be matched on a whole unique key: a real link
            if marked is None or frozenset({other.name.lower()}) not in facts.unique.get(
                far_table, ()
            ):
                continue
            note, other_side = marked
            if alias in counted:
                notes.append(note)  # N1
            one_to_one = frozenset({column.name.lower()}) in facts.unique.get(table, ())
            if one_to_one and other_side and far in counted:
                notes.append(other_side)  # N2
    return list(dict.fromkeys(notes))[:MAX_JOIN_NOTES]
