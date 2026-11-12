"""PURE: how the golden set compares a result with the expected rows, and judges an answer.

The whole result set is compared: one right figure among wrong ones is a failure.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.workflows.assistant import AssistantAnswer
from app.workflows.assistant import Part as ResultPart
from tests.evals.compare import compare_rows, not_mentioned, numbers_in
from tests.evals.dataset import Case, Expected
from tests.evals.runner import judge

BY_OUTLET = Expected(
    columns=[{"name": "outlet", "type": "text"}, {"name": "revenue", "type": "money"}],  # type: ignore[list-item]
    rows=[
        {"outlet": "Indiranagar", "revenue": 270541.00},
        {"outlet": "Koramangala", "revenue": 201877.50},
        {"outlet": "Whitefield", "revenue": 180000.00},
    ],  # type: ignore[arg-type]
)
RIGHT = [
    {"outlet": "Indiranagar", "revenue": 270541.00},
    {"outlet": "Koramangala", "revenue": 201877.50},
    {"outlet": "Whitefield", "revenue": 180000.00},
]


def expected(order: str = "any", rank_by: str | None = None, **columns: str) -> Any:
    def build(*rows: tuple[Any, ...]) -> Expected:
        return Expected(
            order=order,  # type: ignore[arg-type]
            rank_by=rank_by,
            columns=[{"name": n, "type": t} for n, t in columns.items()],  # type: ignore[misc]
            rows=[dict(zip(columns, row, strict=True)) for row in rows],  # type: ignore[arg-type]
        )

    return build


# -- the whole result set ---------------------------------------------------------------


def test_the_same_rows_pass() -> None:
    assert compare_rows(BY_OUTLET, RIGHT).ok


def test_row_order_is_ignored_when_the_question_asks_for_none() -> None:
    assert compare_rows(BY_OUTLET, list(reversed(RIGHT))).ok


def test_the_models_own_column_names_do_not_matter() -> None:
    renamed = [{"branch": r["outlet"], "total_sales": r["revenue"]} for r in RIGHT]
    assert compare_rows(BY_OUTLET, renamed).ok


def test_one_right_figure_among_the_rows_is_not_enough() -> None:
    one = [RIGHT[0]]
    result = compare_rows(BY_OUTLET, one)
    assert not result.ok and "row count: expected 3, got 1" in result.problems
    assert sum("missing row" in p for p in result.problems) == 2


def test_a_missing_row_is_named() -> None:
    result = compare_rows(BY_OUTLET, RIGHT[:2])
    assert not result.ok
    assert any("missing row" in p and "whitefield" in p for p in result.problems)


def test_an_unexpected_row_is_named() -> None:
    result = compare_rows(BY_OUTLET, [*RIGHT, {"outlet": "Mysuru Central", "revenue": 1.0}])
    assert not result.ok and "row count: expected 3, got 4" in result.problems
    assert any("unexpected row" in p and "mysuru central" in p for p in result.problems)


def test_a_duplicate_row_is_named() -> None:
    result = compare_rows(BY_OUTLET, [*RIGHT, RIGHT[0]])
    assert not result.ok
    assert any("duplicate row (2 times)" in p and "indiranagar" in p for p in result.problems)


def test_the_right_figures_against_the_wrong_outlets_fail() -> None:
    swapped = [
        {"outlet": "Indiranagar", "revenue": 201877.50},
        {"outlet": "Koramangala", "revenue": 270541.00},
        {"outlet": "Whitefield", "revenue": 180000.00},
    ]
    result = compare_rows(BY_OUTLET, swapped)
    assert not result.ok and any("missing row" in p for p in result.problems)


def test_a_wrong_figure_in_one_row_fails_and_shows_both_rows() -> None:
    wrong = [RIGHT[0], RIGHT[1], {"outlet": "Whitefield", "revenue": 180000.01}]
    result = compare_rows(BY_OUTLET, wrong)
    assert not result.ok
    assert any("missing row" in p and "180000.00" in p for p in result.problems)
    assert any("unexpected row" in p and "180000.01" in p for p in result.problems)


def test_an_extra_column_is_allowed_and_reported() -> None:
    with_code = [{**r, "code": "X"} for r in RIGHT]
    result = compare_rows(BY_OUTLET, with_code)
    assert result.ok and result.notes == ["extra columns ignored: code"]


def test_a_missing_column_fails() -> None:
    result = compare_rows(BY_OUTLET, [{"outlet": r["outlet"]} for r in RIGHT])
    assert not result.ok and "columns: expected 2" in result.problems[0]


def test_two_columns_with_the_same_values_are_told_apart_by_the_rows() -> None:
    want = expected(a="count", b="count")((1, 2), (2, 1))
    assert compare_rows(want, [{"x": 2, "y": 1}, {"x": 1, "y": 2}]).ok
    assert not compare_rows(want, [{"x": 1, "y": 1}, {"x": 2, "y": 2}]).ok


# -- types -----------------------------------------------------------------------------


@pytest.mark.parametrize("value", [902538, 902538.0, Decimal("902538.00"), "902538.00", 902538.004])
def test_money_is_equal_to_the_cent_however_it_is_written(value: Any) -> None:
    assert compare_rows(expected(revenue="money")((902538.00,)), [{"revenue": value}]).ok


@pytest.mark.parametrize("value", [902538.01, 902537.99, 902538.006, None, "a lot", True])
def test_money_one_cent_out_or_not_a_number_fails(value: Any) -> None:
    assert not compare_rows(expected(revenue="money")((902538.00,)), [{"revenue": value}]).ok


@pytest.mark.parametrize("value", [296, 296.0, Decimal("296"), "296"])
def test_a_count_is_exact(value: Any) -> None:
    assert compare_rows(expected(customers="count")((296,)), [{"customers": value}]).ok


@pytest.mark.parametrize("value", [295, 297, 296.4, 296.0001])
def test_a_count_is_never_rounded_into_place(value: Any) -> None:
    assert not compare_rows(expected(customers="count")((296,)), [{"customers": value}]).ok


def test_text_ignores_case_and_outer_spaces_only() -> None:
    want = expected(outlet="text")(("Indiranagar",))
    assert compare_rows(want, [{"outlet": "  indiranagar "}]).ok
    assert not compare_rows(want, [{"outlet": "Indira nagar"}]).ok


@pytest.mark.parametrize("value", ["2026-10", "2026-10-01", "2026-10-01 00:00:00+05:30"])
def test_a_month_is_the_same_month_as_a_date_a_timestamp_or_text(value: str) -> None:
    assert compare_rows(expected(month="month")(("2026-10",)), [{"month": value}]).ok


@pytest.mark.parametrize("value", ["2026-09", "October 2026", "10/2026", None])
def test_another_month_or_another_way_of_writing_it_fails(value: Any) -> None:
    assert not compare_rows(expected(month="month")(("2026-10",)), [{"month": value}]).ok


# -- empty results and order --------------------------------------------------------------


def test_an_empty_result_matches_only_an_empty_expectation() -> None:
    nothing = expected(outlet="text", product="text")()
    assert compare_rows(nothing, []).ok
    result = compare_rows(nothing, [{"outlet": "Indiranagar", "product": "Latte"}])
    assert not result.ok and "row count: expected 0, got 1" in result.problems
    assert not compare_rows(BY_OUTLET, []).ok


TOP = expected("enforced", "units", product="text", units="number")(
    ("Cappuccino", 519), ("Latte", 481), ("Filter Coffee", 451)
)


def test_a_ranking_must_be_in_order() -> None:
    rows = [
        {"product": p, "units": u}
        for p, u in (("Cappuccino", 519), ("Latte", 481), ("Filter Coffee", 451))
    ]
    assert compare_rows(TOP, rows).ok
    result = compare_rows(TOP, [rows[1], rows[0], rows[2]])
    assert not result.ok and "order" in result.problems[0]


def test_a_top_n_must_have_exactly_n_rows() -> None:
    rows = [{"product": p, "units": u} for p, u in (("Cappuccino", 519), ("Latte", 481))]
    assert not compare_rows(TOP, rows).ok
    assert not compare_rows(
        TOP, [*rows, {"product": "Filter Coffee", "units": 451}, {"product": "Tea", "units": 1}]
    ).ok


def test_rows_that_tie_on_the_ranking_value_may_swap_and_nothing_else_may() -> None:
    tied = expected("enforced", "units", product="text", units="number")(
        ("A", 9), ("B", 5), ("C", 5), ("D", 1)
    )
    units = {"A": 9, "B": 5, "C": 5, "D": 1}

    def rows(*order: str) -> list[dict[str, Any]]:
        return [{"product": p, "units": units[p]} for p in order]

    assert compare_rows(tied, rows("A", "C", "B", "D")).ok
    assert not compare_rows(tied, rows("B", "A", "C", "D")).ok
    assert not compare_rows(tied, rows("A", "B", "D", "C")).ok


def test_without_a_ranking_column_enforced_order_is_strict() -> None:
    months = expected("enforced", month="month", expenses="money")(
        ("2026-09", 1.0), ("2026-10", 2.0)
    )
    assert compare_rows(
        months, [{"month": "2026-09-01", "expenses": 1}, {"month": "2026-10-01", "expenses": 2}]
    ).ok
    assert not compare_rows(
        months, [{"month": "2026-10-01", "expenses": 2}, {"month": "2026-09-01", "expenses": 1}]
    ).ok


# -- the written answer ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Revenue was 902,538.00.",
        "Revenue was ₹9,02,538 last month.",
        "Revenue was 902538.",
        "Revenue: INR 902 538.00",
    ],
)
def test_a_figure_is_found_whatever_the_thousands_separator(text: str) -> None:
    assert not_mentioned(text, [902538.00]) == []


@pytest.mark.parametrize("text", ["Revenue was about 0.9 million.", "Revenue was 902,539.", ""])
def test_a_figure_that_is_not_stated_is_reported(text: str) -> None:
    assert not_mentioned(text, [902538.00]) == [902538.00]


def test_dates_are_read_as_numbers_but_do_not_hide_a_missing_figure() -> None:
    assert Decimal("2026") in numbers_in("In October 2026 revenue rose.")
    assert not_mentioned("In October 2026 revenue rose.", [296]) == [296]


# -- judging an answer: one cause per failure -------------------------------------------------


def a_case(**changes: Any) -> Case:
    base: dict[str, Any] = {
        "id": "A1-x", "group": "A", "status": "verified", "as": "analyst@brewbite.example",
        "question": "What was our total revenue last month?",
        "scope": {"company": "Brew & Bite Cafés", "outlets": "all"},
        "period": {"name": "last month"}, "metric": "revenue",
        "route": {"expect": "agents", "agents": ["sales_agent"]},
        "compare": "rows", "interpretation": "x", "answer_must_contain": [902538.00],
        "parts": [{
            "agent": "sales_agent", "verify_sql": "SELECT 1", "pipeline_sql": "SELECT 1",
            "expected": {"columns": [{"name": "revenue", "type": "money"}],
                         "rows": [{"revenue": 902538.00}]},
        }],
    }  # fmt: skip
    return Case(**{**base, **changes})


def an_answer(**changes: Any) -> AssistantAnswer:
    part = ResultPart(
        agent=changes.pop("agent", "sales_agent"), question="q",
        status=changes.pop("part_status", "answered"), summary=changes.pop("part_summary", ""),
        data=changes.pop("data", [{"revenue": 902538.0}]),
    )  # fmt: skip
    base: dict[str, Any] = {
        "question": "q", "status": "answered", "route": "agents", "reason": "",
        "summary": "Revenue last month was 902,538.00.", "results": [part],
    }  # fmt: skip
    return AssistantAnswer(**{**base, **changes})


def test_a_right_answer_passes() -> None:
    outcome = judge(a_case(), an_answer())
    assert outcome.passed and outcome.cause is None and outcome.details == {}


def test_the_wrong_agent_is_a_routing_failure() -> None:
    outcome = judge(a_case(), an_answer(agent="customer_agent"))
    assert outcome.cause == "routing"


def test_the_general_route_instead_of_an_agent_is_a_routing_failure_unless_accepted() -> None:
    general = an_answer(agent="general", route="general")
    assert judge(a_case(), general).cause == "routing"
    accepting = a_case(
        route={"expect": "agents", "agents": ["sales_agent"],
               "accept": [{"route": "general", "why": "defensible"}]}
    )  # fmt: skip
    outcome = judge(accepting, general)
    assert outcome.passed and "accepted alternative" in outcome.notes[0]


def test_a_part_that_did_not_run_is_an_execution_failure() -> None:
    outcome = judge(a_case(), an_answer(part_status="failed", data=[]))
    assert outcome.cause == "execution" and "status failed" in str(outcome.details)


def test_a_wrong_figure_is_a_calculation_failure() -> None:
    outcome = judge(a_case(), an_answer(data=[{"revenue": 2303229.5}]))
    assert outcome.cause == "calculation"


def test_right_rows_with_the_figure_left_out_of_the_text_is_an_interpretation_failure() -> None:
    outcome = judge(a_case(), an_answer(summary="Revenue was strong last month."))
    assert outcome.cause == "interpretation" and "902538" in str(outcome.details)


def test_a_limited_viewer_must_be_told_so() -> None:
    limited = a_case(scope={"company": "Brew & Bite Cafés", "outlets": ["Indiranagar"]})
    assert judge(limited, an_answer()).cause == "interpretation"
    assert judge(limited, an_answer(scope_note="Limited to your outlets: Indiranagar.")).passed


def test_routing_is_reported_before_a_wrong_figure() -> None:
    outcome = judge(a_case(), an_answer(agent="customer_agent", data=[{"revenue": 1}]))
    assert outcome.cause == "routing" and "calculation" in outcome.details


def test_a_refusal_passes_only_with_the_right_status_and_no_rows() -> None:
    refusal = a_case(
        route={"expect": "unsupported"}, compare="refusal", expect_status=["unsupported"],
        parts=[], answer_must_contain=[],
    )  # fmt: skip
    refused = AssistantAnswer(
        question="q", status="unsupported", route="unsupported", reason="", summary="No."
    )
    assert judge(refusal, refused).passed
    answered = an_answer(route="unsupported", status="answered")
    outcome = judge(refusal, answered)
    assert outcome.cause == "routing" or outcome.cause == "execution"
    assert "returned 1 rows" in str(outcome.details)


def test_an_empty_result_is_expected_to_say_no_data() -> None:
    empty = a_case(
        compare="empty", answer_must_contain=[],
        parts=[{"agent": "sales_agent", "verify_sql": "SELECT 1", "pipeline_sql": "SELECT 1",
                "expected": {"columns": [{"name": "product", "type": "text"}], "rows": []}}],
    )  # fmt: skip
    assert judge(empty, an_answer(part_status="no_data", data=[], status="no_data")).passed
    listed = judge(empty, an_answer(data=[{"product": "Latte"}]))
    assert listed.cause == "execution"
