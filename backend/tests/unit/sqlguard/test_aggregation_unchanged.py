"""PURE: the aggregation check says today exactly what it said before Step 4.

Step 4 moved the check's grain calculation into a public helper (`relations`) so the
correctness check can ask the same question. No rule was to change. This test holds
the verdict the check gave on every query we had, recorded BEFORE that change
(aggregation_verdicts.json: 176 queries), and compares, word for word:

- refused or passed,
- the refusal code and the full refusal sentence,
- how many SELECTs were judged, and why any was left as uncertain.

The queries are the check's own unit cases, the golden set's queries and traps as they
stood then, and the 43 queries a real model had run successfully in earlier phases.
If a rule of the aggregation check is ever changed on purpose, this file must be
regenerated on purpose, and the change explained.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import sqlglot
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from app.semantic.model import load_vocabulary
from app.sqlguard import Catalog, Checked, Refused, check, check_aggregation
from app.sqlguard.aggregation import Relations, Uncertain, relations

RECORDED: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "aggregation_verdicts.json").read_text()
)
VOCABULARY = load_vocabulary()
CATALOG = Catalog(VOCABULARY.catalog())
UNIQUE = VOCABULARY.unique_keys()


def test_the_record_holds_every_kind_of_verdict() -> None:
    assert len(RECORDED) == 176
    assert sum(1 for r in RECORDED if r.get("verdict") == "refused") == 36
    assert sum(1 for r in RECORDED if r.get("verdict") == "passed") == 140
    assert sum(1 for r in RECORDED if r.get("uncertain")) == 7
    assert sum(1 for r in RECORDED if r["name"].startswith("real model")) == 43


@pytest.mark.parametrize("recorded", RECORDED, ids=[r["name"] for r in RECORDED])
def test_the_verdict_is_what_it_was_before_step_4(recorded: dict[str, Any]) -> None:
    guarded = check(recorded["sql"], CATALOG, max_rows=1001)
    assert isinstance(guarded, Checked)
    result = check_aggregation(guarded.sql, CATALOG, UNIQUE)
    if recorded["verdict"] == "refused":
        assert isinstance(result, Refused)
        assert (result.code, result.reason) == (recorded["code"], recorded["reason"])
    else:
        assert not isinstance(result, Refused), result.reason
        assert (result.judged, list(result.uncertain)) == (
            recorded["judged"],
            recorded["uncertain"],
        )


# -- the helper itself ---------------------------------------------------------------------


def found(sql: str) -> Relations:
    tree = qualify(
        sqlglot.parse_one(sql, read="postgres"),
        schema={"biz": CATALOG.tables},
        db="biz",
        dialect="postgres",
        validate_qualify_columns=True,
        expand_stars=False,
        quote_identifiers=False,
    )
    return relations(traverse_scope(tree)[-1], UNIQUE)


def test_order_lines_are_the_grain_of_orders_joined_to_their_lines() -> None:
    result = found("SELECT COUNT(*) FROM orders o JOIN order_items oi ON oi.order_id = o.id")
    assert result.sources == {"o": "orders", "oi": "order_items"}
    assert result.grain == {"oi"}
    assert result.reach == {"o": {"o"}, "oi": {"o", "oi"}}


def test_two_detail_tables_through_the_outlet_have_no_grain() -> None:
    result = found(
        "SELECT COUNT(*) FROM orders o JOIN customer_visits v ON v.outlet_id = o.outlet_id"
    )
    assert result.grain == frozenset()


def test_a_one_to_one_join_keeps_both_tables_at_the_grain() -> None:
    result = found("SELECT 1 FROM orders o JOIN customer_visits v ON v.order_id = o.id")
    assert result.grain == {"o", "v"}


def test_one_table_is_its_own_grain() -> None:
    assert found("SELECT COUNT(*) FROM orders o").grain == {"o"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM orders o JOIN (SELECT v.outlet_id FROM customer_visits v) s "
        "ON s.outlet_id = o.outlet_id",
        "SELECT 1 FROM orders o JOIN customer_visits v "
        "ON v.order_id = o.id OR v.customer_id = o.customer_id",
        "SELECT 1 FROM orders o JOIN customer_visits v ON v.visit_date < o.order_date",
    ],
)
def test_what_cannot_be_decided_is_said_to_be_uncertain(sql: str) -> None:
    with pytest.raises(Uncertain):
        found(sql)
