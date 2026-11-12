"""The aggregation check: does a join repeat the rows a query adds up?

The guard (guard.py) decides whether a query is safe to run. It cannot tell whether
its figures are right. This module catches one kind of wrong figure, the commonest:
a SUM, AVG or COUNT(column) over rows that a join has repeated.

    SELECT SUM(o.total_amount) FROM orders o JOIN order_items i ON i.order_id = o.id

Every order appears once per line, so its total is added once per line.

How it decides. For each SELECT with two or more tables it reads the equalities that
join them and the tables' unique keys (tables.yaml, `unique`). Table B is "at most one
row" for a row of table A when all the columns of one of B's unique keys are equal to
columns of A (or of a table already settled that way, or to a constant). The *grain*
of the SELECT is the set of tables from which every other table is reached like that:
their rows are never repeated. An aggregate is sound when it reads at least one grain
table. If it reads none, the rows it adds up may be repeated, and the query is refused
with a reason the model can act on.

What it is not:
- It never rewrites SQL. It answers Passed or Refused, nothing else.
- It is not a security check and replaces none: it runs after the guard has passed.
- It does not prove a query right. A wrong period, filter or metric, rows lost to an
  inner join, or a join that answers a different question all pass.
- When it cannot be sure (a subquery or CTE in FROM, a join on OR, an inequality or an
  expression, a NATURAL join, a table with no key listed), it passes the SELECT and
  says so.

Pure, like the guard: no import from the rest of the app, no connection.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.sqlguard.guard import DIALECT, Catalog, Refused

CODE = "unsafe_aggregation"

UniqueKeys = Mapping[str, tuple[frozenset[str], ...]]

# the aggregates a repeated row changes. MIN and MAX are not changed by repetition,
# and neither is anything DISTINCT, so they are never judged.
_JUDGED = (exp.Sum, exp.Avg, exp.Count)


@dataclass(frozen=True)
class Passed:
    """Nothing unsafe was found. `judged` SELECTs were decided; `uncertain` were not."""

    judged: int = 0
    uncertain: tuple[str, ...] = ()  # why a SELECT was left alone, one entry each


class _Uncertain(Exception):
    """This SELECT cannot be decided. It is passed, never refused."""


Uncertain = _Uncertain  # the public name, for callers of `relations`


def check_aggregation(sql: str, catalog: Catalog, unique: UniqueKeys) -> Passed | Refused:
    """`sql` must already have passed the guard. It is read, never changed."""
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
    except (SqlglotError, RecursionError, KeyError, ValueError, AttributeError, TypeError):
        return Passed(uncertain=("the query could not be analysed",))

    judged, uncertain = 0, []
    for scope in scopes:
        if not isinstance(scope.expression, exp.Select):
            continue
        try:
            refusal = _judge(scope, unique)
        except _Uncertain as why:
            uncertain.append(str(why))
            continue
        except (SqlglotError, RecursionError, KeyError, ValueError, AttributeError, TypeError):
            uncertain.append("a SELECT could not be analysed")
            continue
        if refusal is not None:
            return refusal
        judged += 1
    return Passed(judged=judged, uncertain=tuple(uncertain))


@dataclass(frozen=True)
class Relations:
    """How the tables of one SELECT relate, by their unique keys."""

    sources: dict[str, str]  # alias -> table name
    reach: dict[str, frozenset[str]]  # alias -> the aliases with at most one row per its row
    grain: frozenset[str]  # the aliases whose rows the joins never repeat; empty if none


def relations(scope: Scope, unique: UniqueKeys) -> Relations:
    """The tables of this SELECT, what each one settles, and the grain.

    Raises `Uncertain` when it cannot be decided (a subquery, CTE or function in FROM,
    a table with no key listed, USING or NATURAL, tables tied by OR, an inequality or
    an expression). This is the analysis `check_aggregation` itself uses; it is public
    so that another check can ask the same question without a second copy of it.
    """
    select = scope.expression
    # alias -> table name, for the tables in this SELECT's FROM and JOINs
    sources: dict[str, str] = {}
    for alias, (_, source) in scope.selected_sources.items():
        if not isinstance(source, exp.Table) or isinstance(source.this, exp.Func):
            raise _Uncertain("a subquery, CTE or function in FROM")
        name = source.name.lower()
        if not unique.get(name):
            raise _Uncertain(f"no unique key is listed for {name}")
        sources[alias.lower()] = name
    if len(sources) < 2:
        return Relations(sources, {a: frozenset({a}) for a in sources}, frozenset(sources))

    equal = _Equalities()
    constant: set[tuple[str, str]] = set()
    for join in select.args.get("joins") or []:
        if join.args.get("using") or (join.args.get("method") or "").upper() == "NATURAL":
            raise _Uncertain("a join written with USING or NATURAL")
        _read(join.args.get("on"), sources, equal, constant)
    where = select.args.get("where")
    _read(where.this if where is not None else None, sources, equal, constant)

    def settled_from(start: str) -> set[str]:
        """The tables with at most one row for each row of `start`."""
        settled = {start}
        grew = True
        while grew:
            grew = False
            for alias, table in sources.items():
                if alias in settled:
                    continue
                for key in unique[table]:
                    if all(
                        (alias, column) in constant or equal.reaches((alias, column), settled)
                        for column in key
                    ):
                        settled.add(alias)
                        grew = True
                        break
        return settled

    reach = {alias: frozenset(settled_from(alias)) for alias in sources}
    grain = frozenset(a for a, settled in reach.items() if len(settled) == len(sources))
    return Relations(sources, reach, grain)


def _judge(scope: Scope, unique: UniqueKeys) -> Refused | None:
    select = scope.expression
    aggregates = [a for a in _own(select, _JUDGED) if _repetition_changes(a)]
    if not aggregates:
        return None

    found = relations(scope, unique)
    sources, reach, grain = found.sources, found.reach, found.grain
    if len(sources) < 2:
        return None

    for aggregate in aggregates:
        tables = {column.table.lower() for column in _columns(aggregate)}
        if not tables or not tables <= set(sources):
            continue  # no column of this SELECT, or one from an outer query: not judged
        if tables & grain:
            continue
        summed = sorted(tables)[0]
        repeating = sorted({sources[a] for a in sources if a not in reach[summed]})
        return Refused(CODE, _reason(aggregate, sources[summed], repeating))
    return None


def _repetition_changes(aggregate: exp.Expr) -> bool:
    if isinstance(aggregate.parent, exp.Window):
        return False  # a window function: not judged
    inner = aggregate.this
    if isinstance(inner, exp.Distinct) or aggregate.args.get("distinct"):
        return False
    return not isinstance(inner, exp.Star)  # COUNT(*) reads no column: not judged


def _own(node: exp.Expr, kinds: type | tuple[type, ...]) -> Iterator[exp.Expr]:
    """Nodes of these kinds inside `node`, not looking into nested SELECTs."""

    def nested(n: exp.Expr) -> bool:
        return n is not node and isinstance(n, (exp.Select, exp.SetOperation))

    for found in node.walk(prune=nested):
        if isinstance(found, kinds) and not nested(found):
            yield found


def _columns(node: exp.Expr) -> Iterator[exp.Column]:
    for found in _own(node, exp.Column):
        if isinstance(found, exp.Column):
            yield found


class _Equalities:
    """Columns known to be equal to each other (a small union-find)."""

    def __init__(self) -> None:
        self._parent: dict[tuple[str, str], tuple[str, str]] = {}

    def _root(self, item: tuple[str, str]) -> tuple[str, str]:
        self._parent.setdefault(item, item)
        while self._parent[item] != item:
            item = self._parent[item]
        return item

    def join(self, a: tuple[str, str], b: tuple[str, str]) -> None:
        self._parent[self._root(a)] = self._root(b)

    def reaches(self, item: tuple[str, str], aliases: set[str]) -> bool:
        """Is `item` equal to a column of one of these tables?"""
        if item not in self._parent:
            return False
        root = self._root(item)
        return any(
            other[0] in aliases and self._root(other) == root
            for other in list(self._parent)
            if other != item
        )


def _read(
    condition: exp.Expr | None,
    sources: dict[str, str],
    equal: _Equalities,
    constant: set[tuple[str, str]],
) -> None:
    """Record what an ON or WHERE condition says about how the tables are joined."""
    if condition is None:
        return
    for part in _conjuncts(condition):
        left, right = (
            (_side(part.left, sources), _side(part.right, sources))
            if isinstance(part, exp.EQ)
            else (None, None)
        )
        if isinstance(left, tuple) and isinstance(right, tuple):
            if left[0] != right[0]:
                equal.join(left, right)
            continue
        if isinstance(left, tuple) and right == "constant":
            constant.add(left)
            continue
        if isinstance(right, tuple) and left == "constant":
            constant.add(right)
            continue
        # anything else that ties two tables together is a relationship this check
        # cannot reason about: OR, an inequality, an expression, a cast
        touched = {c.table.lower() for c in _columns(part)} & set(sources)
        if len(touched) >= 2:
            raise _Uncertain("tables joined by OR, an inequality or an expression")


def _conjuncts(condition: exp.Expr) -> Iterator[exp.Expr]:
    condition = condition.unnest()
    if isinstance(condition, exp.And):
        yield from _conjuncts(condition.left)
        yield from _conjuncts(condition.right)
    else:
        yield condition


def _side(node: exp.Expr, sources: dict[str, str]) -> tuple[str, str] | str:
    """One side of an equality: a column of this SELECT, a constant, or something else."""
    node = node.unnest()
    if isinstance(node, exp.Column):
        alias = node.table.lower()
        # a column of an outer query has one value for the whole of this SELECT
        return (alias, node.name.lower()) if alias in sources else "constant"
    if any(c.table.lower() in sources for c in _columns(node)):
        return "expression"
    return "constant"


def _reason(aggregate: exp.Expr, summed: str, repeating: list[str]) -> str:
    written = aggregate.sql(dialect=DIALECT)
    others = ", ".join(repeating)
    return (
        f"{written} adds up rows of {summed} that this query repeats: {summed} is joined to "
        f"{others} in a way that can match several rows for one row of {summed}, so each value "
        "is counted more than once and the figure is wrong. Fix it in one of these ways: "
        "(1) add up a column of the table that has one row per line instead "
        "(for example order_items.line_total rather than orders.total_amount); "
        "(2) work out each figure in its own subquery (WITH a AS (...), b AS (...)), grouped "
        "by the shared column, and join the subqueries; "
        "(3) if the other table only limits which rows count, use EXISTS or IN instead of a "
        "join; (4) if the join should match one row, join on the other table's whole unique key."
    )
