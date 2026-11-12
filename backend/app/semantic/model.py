"""The business vocabulary as data: tables, columns, joins and metrics.

Loaded once from tables.yaml and metrics.yaml. It is separate from the code that
writes SQL on purpose: the same definitions feed the prompt, the guard's catalog
and the tests.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

HERE = Path(__file__).parent


class Column(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    type: str
    meaning: str = ""
    values: tuple[str, ...] = ()


class Join(BaseModel):
    model_config = ConfigDict(frozen=True)
    column: str
    to: str  # "table.column"

    @property
    def table(self) -> str:
        return self.to.split(".")[0]


class Sales(BaseModel):
    """Which columns are sales figures, and the status that decides if a row counts."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    columns: tuple[str, ...]
    status: str | None = None  # on the table that holds the status
    normal: str | None = None  # the status ordinary sales figures count
    via: str | None = None  # on a line table: the column pointing at the order
    of: str | None = None  # ... and the table that order is in


class OptionalColumn(BaseModel):
    """A column that may be empty, and what an inner join through it leaves out."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    column: str
    note: str
    other_side_note: str = ""  # only meaningful when the column alone is a unique key


class Table(BaseModel):
    model_config = ConfigDict(frozen=True)
    table: str
    purpose: str
    # the table's unique keys, each a tuple of columns (see tables.yaml)
    unique: tuple[tuple[str, ...], ...] = ()
    sales: Sales | None = None
    optional: tuple[OptionalColumn, ...] = ()
    keywords: tuple[str, ...] = ()
    date_column: str | None = None
    joins: tuple[Join, ...] = ()
    columns: tuple[Column, ...]


class Metric(BaseModel):
    model_config = ConfigDict(frozen=True)
    metric: str
    names: tuple[str, ...]
    unit: str
    tables: tuple[str, ...]
    sql: str
    where: str = ""
    note: str = ""


class Vocabulary(BaseModel):
    model_config = ConfigDict(frozen=True)
    tables: tuple[Table, ...]
    metrics: tuple[Metric, ...]

    def table(self, name: str) -> Table:
        return next(t for t in self.tables if t.table == name)

    def restricted_to(self, names: frozenset[str]) -> Vocabulary:
        """Only these tables, the joins between them, and the metrics they can compute.

        An agent is given this instead of the whole vocabulary, so both its prompt and
        the guard's allow-list hold its own tables and nothing else.
        """
        unknown = names - {t.table for t in self.tables}
        assert not unknown, f"unknown tables: {sorted(unknown)}"
        tables = tuple(
            t.model_copy(update={"joins": tuple(j for j in t.joins if j.table in names)})
            for t in self.tables
            if t.table in names
        )
        metrics = tuple(m for m in self.metrics if set(m.tables) <= names)
        return Vocabulary(tables=tables, metrics=metrics)

    def catalog(self) -> dict[str, dict[str, str]]:
        """table -> {column: type}: what the SQL guard allows."""
        return {t.table: {c.name: c.type for c in t.columns} for t in self.tables}

    def correctness_facts(self) -> dict[str, Any]:
        """What the correctness check reasons from, as plain values (sqlguard stays pure).

        A table that is not in this vocabulary takes its rules with it: an agent that
        cannot read orders is simply not judged on order status.
        """
        names = {t.table for t in self.tables}
        holder = next((t for t in self.tables if t.sales and t.sales.status), None)
        sales: dict[str, frozenset[str]] = {}
        links: dict[str, str] = {}
        values: tuple[str, ...] = ()
        if holder is not None and holder.sales is not None:
            sales[holder.table] = frozenset(holder.sales.columns)
            status = next(c for c in holder.columns if c.name == holder.sales.status)
            values = status.values
            for t in self.tables:
                if t.sales and t.sales.via and t.sales.of == holder.table:
                    sales[t.table] = frozenset(t.sales.columns)
                    links[t.table] = t.sales.via
        return {
            "unique": self.unique_keys(),
            "sales": sales,
            "status_table": holder.table if holder else "",
            "status_column": (holder.sales.status or "") if holder and holder.sales else "",
            "status_values": values,
            "normal_status": (holder.sales.normal or "") if holder and holder.sales else "",
            "line_links": links,
            "optional": {
                t.table: {o.column: (o.note, o.other_side_note) for o in t.optional}
                for t in self.tables
                if t.optional and t.table in names
            },
            "timestamps": {
                t.table: (
                    frozenset(c.name for c in t.columns if c.type == "timestamptz"),
                    t.date_column,
                )
                for t in self.tables
                if t.date_column and any(c.type == "timestamptz" for c in t.columns)
            },
        }

    def unique_keys(self) -> dict[str, tuple[frozenset[str], ...]]:
        """table -> its unique keys: what the aggregation check reasons from."""
        return {t.table: tuple(frozenset(key) for key in t.unique) for t in self.tables}


def validate(vocabulary: Vocabulary) -> None:
    """Fail at start-up, not in a query, when the files contradict themselves."""
    names = {t.table for t in vocabulary.tables}
    for table in vocabulary.tables:
        columns = {c.name for c in table.columns}
        for key in table.unique:
            assert key, f"{table.table} has an empty unique key"
            assert len(set(key)) == len(key), f"{table.table} repeats a column in a unique key"
            unknown = set(key) - columns
            where = f"{table.table} has a unique key on unknown columns"
            assert not unknown, f"{where} {sorted(unknown)}"
        for join in table.joins:
            assert join.table in names, f"{table.table} joins to unknown table {join.table}"
        for optional in table.optional:
            assert optional.column in columns, (
                f"{table.table} marks an unknown column optional: {optional.column}"
            )
            assert optional.note.strip(), f"{table.table}.{optional.column} has no note"
            alone = (optional.column,) in table.unique
            assert alone or not optional.other_side_note, (
                f"{table.table}.{optional.column} is not a unique key by itself, "
                "so it cannot have an other_side_note (it is not a one-to-one link)"
            )
        if table.sales is not None:
            sales = table.sales
            unknown = set(sales.columns) - columns
            assert sales.columns and not unknown, (
                f"{table.table} lists unknown sales columns {sorted(unknown)}"
            )
            assert bool(sales.status) != bool(sales.via), (
                f"{table.table}: sales needs either its own status or a link (via, of)"
            )
            if sales.status:
                status = next((c for c in table.columns if c.name == sales.status), None)
                assert status is not None, f"{table.table} has no status column {sales.status}"
                assert status.values, f"{table.table}.{sales.status} lists no allowed values"
                assert sales.normal in status.values, (
                    f"{table.table}: the normal status {sales.normal!r} is not an allowed value"
                )
            else:
                assert sales.via in columns, f"{table.table} has no column {sales.via}"
                holder = next((t for t in vocabulary.tables if t.table == sales.of), None)
                assert holder is not None and holder.sales and holder.sales.status, (
                    f"{table.table}: sales status comes from {sales.of!r}, which declares none"
                )
    holders = [t.table for t in vocabulary.tables if t.sales and t.sales.status]
    assert len(holders) <= 1, f"more than one table holds the sales status: {holders}"
    for metric in vocabulary.metrics:
        for needed in metric.tables:
            assert needed in names, f"{metric.metric} needs unknown table {needed}"


@lru_cache
def load_vocabulary() -> Vocabulary:
    tables = yaml.safe_load((HERE / "tables.yaml").read_text())
    metrics = yaml.safe_load((HERE / "metrics.yaml").read_text())
    vocabulary = Vocabulary(tables=tables, metrics=metrics)
    validate(vocabulary)
    return vocabulary
