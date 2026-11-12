"""Compares a result with the expected rows: the whole result set, not a sample.

A result passes only if it has exactly the expected rows: the same number, every
expected column of every row equal, none missing, none extra, none repeated, and in
the expected order when the question asks for one. Column names are the model's to
choose, so columns are matched by content; extra columns in the result are allowed
and reported.
"""

from __future__ import annotations

import itertools
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from tests.evals.dataset import Column, Expected

Cell = Any
MISSING = "<not a value of this type>"
MAX_ASSIGNMENTS = 5000
_MONTH = re.compile(r"\d{4}-\d{2}(?:$|-\d{2})")


@dataclass
class Comparison:
    ok: bool
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # true, but not a failure


def normal(value: Any, column: Column) -> Cell:
    """The value as this column's type compares it, or MISSING if it is not one."""
    if value is None:
        return None
    if column.type == "text":
        return str(value).strip().casefold()
    if column.type == "date":
        return str(value)[:10]
    if column.type == "month":  # 2026-10, however it is written: a date, a timestamp, text
        text = str(value).strip()
        return text[:7] if _MONTH.match(text) else MISSING
    if isinstance(value, bool):
        return MISSING
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return MISSING
    if not number.is_finite():
        return MISSING
    if column.type == "count":
        return int(number) if number == number.to_integral_value() else MISSING
    places = 2 if column.type == "money" else column.places
    return number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _show(row: Sequence[Cell], columns: Sequence[Column]) -> str:
    return "{" + ", ".join(f"{c.name}: {v}" for c, v in zip(columns, row, strict=True)) + "}"


def _ordered_equal(
    want: list[tuple[Cell, ...]], got: list[tuple[Cell, ...]], rank: int | None
) -> bool:
    if rank is None:
        return want == got
    # rows that tie on the ranking value may swap places; nothing else may
    position = 0
    for _, group in itertools.groupby(want, key=lambda row: row[rank]):
        tied = list(group)
        if Counter(tied) != Counter(got[position : position + len(tied)]):
            return False
        position += len(tied)
    return True


def compare_rows(expected: Expected, actual: Sequence[dict[str, Any]]) -> Comparison:
    columns = expected.columns
    want = [tuple(normal(row[c.name], c) for c in columns) for row in expected.rows]
    result = Comparison(ok=False)

    if len(actual) != len(want):
        result.problems.append(f"row count: expected {len(want)}, got {len(actual)}")
    if not want and not actual:
        return Comparison(ok=True)
    names = list(actual[0]) if actual else []
    if actual and len(names) < len(columns):
        result.problems.append(
            f"columns: expected {len(columns)} ({', '.join(c.name for c in columns)}), "
            f"got {len(names)} ({', '.join(names)})"
        )
        return result

    def column_values(name: str, column: Column) -> list[Cell]:
        return [normal(row.get(name), column) for row in actual]

    # which result columns could be each expected column: same values, as a multiset
    candidates: list[list[str]] = []
    for index, column in enumerate(columns):
        wanted = Counter(row[index] for row in want)
        matching = [n for n in names if Counter(column_values(n, column)) == wanted]
        matching.sort(key=lambda n: n.casefold() != column.name.casefold())  # same name first
        candidates.append(matching)

    rank = None
    if expected.order == "enforced" and expected.rank_by:
        rank = [c.name for c in columns].index(expected.rank_by)

    def rows_under(assignment: Sequence[str]) -> list[tuple[Cell, ...]]:
        return [
            tuple(
                normal(row.get(name), column)
                for name, column in zip(assignment, columns, strict=True)
            )
            for row in actual
        ]

    if len(actual) == len(want) and all(candidates):
        tried = 0
        for assignment in itertools.product(*candidates):
            if len(set(assignment)) != len(assignment):
                continue
            tried += 1
            if tried > MAX_ASSIGNMENTS:
                break
            got = rows_under(assignment)
            if Counter(got) != Counter(want):
                continue
            if expected.order == "enforced" and not _ordered_equal(want, got, rank):
                result.problems.append("order: the rows are right but not in the expected order")
                return result
            extra = [n for n in names if n not in assignment]
            notes = [f"extra columns ignored: {', '.join(extra)}"] if extra else []
            return Comparison(ok=True, notes=notes)

    # no assignment fits: say what is wrong, using the best guess at the columns
    guess: list[str] = []
    for index, column in enumerate(columns):
        by_name = [n for n in names if n.casefold() == column.name.casefold()]
        free = [n for n in candidates[index] + by_name + names if n not in guess]
        if not free:
            break
        guess.append(free[0])
        if not candidates[index]:
            result.problems.append(
                f"column {column.name}: no column of the result holds its expected values"
            )
    if len(guess) == len(columns):
        got_count, want_count = Counter(rows_under(guess)), Counter(want)
        for row, n in want_count.items():
            if got_count[row] < n:
                result.problems.append(f"missing row: {_show(row, columns)}")
        for row, n in got_count.items():
            if want_count[row] == 0:
                result.problems.append(f"unexpected row: {_show(row, columns)}")
            elif n > want_count[row]:
                result.problems.append(f"duplicate row ({n} times): {_show(row, columns)}")
    if not result.problems:
        result.problems.append("the rows do not match under any assignment of columns")
    return result


# -- the written answer ------------------------------------------------------------------

_BETWEEN_DIGITS = re.compile(r"(?<=\d)[,   ](?=\d)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def numbers_in(text: str) -> list[Decimal]:
    """Every figure a text states, read through thousands separators of any style."""
    return [Decimal(n) for n in _NUMBER.findall(_BETWEEN_DIGITS.sub("", text))]


def not_mentioned(text: str, figures: Iterable[float]) -> list[float]:
    """The expected figures the text does not state (to the cent, or as a whole number)."""
    stated = set(numbers_in(text))
    missing = []
    for figure in figures:
        exact = Decimal(str(figure))
        if not any(abs(s - exact) < Decimal("0.005") for s in stated):
            missing.append(figure)
    return missing
