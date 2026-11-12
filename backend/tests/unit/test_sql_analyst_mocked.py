"""MOCKED: the SQL analyst with a scripted model and a fake database.

The guard and the vocabulary are real. Live equivalents: tests/integration/test_analytics_live.py.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.agents.sql_analyst import SqlDraft, SqlOutcome, answer_with_sql
from app.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.llm.fake import FakeLLM
from app.semantic.model import load_vocabulary
from app.store.analytics import QueryFailedError, QueryTimeoutError
from tests.unit.analytics_fakes import TENANT, FakeDatabase, caller, result

QUESTION = "What was our total revenue last month?"
GOOD = (
    "SELECT ROUND(SUM(total_amount - tax_amount), 2) AS revenue FROM orders "
    "WHERE status = 'completed' AND order_date BETWEEN DATE '2026-10-01' AND DATE '2026-10-31'"
)
REVENUE = result([{"revenue": 912345.5}])


def draft(sql: str | None, explanation: str = "", assumptions: list[str] | None = None) -> SqlDraft:
    return SqlDraft(sql=sql, explanation=explanation, assumptions=assumptions or [])


async def run(llm: FakeLLM, question: str = QUESTION, **kw: Any) -> SqlOutcome:
    return await answer_with_sql(
        question=question,
        caller=kw.get("who", caller()),
        llm=llm,
        pg=None,  # type: ignore[arg-type]  # the database functions are replaced
        vocabulary=load_vocabulary(),
        today=date(2026, 11, 10),
        max_rows=kw.get("max_rows", 1000),
    )


# -- processing the question, generating SQL -------------------------------------


async def test_a_question_becomes_a_checked_query_and_a_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(GOOD, "Revenue in October.", ["Sales means revenue."]))
    outcome = await run(llm)

    assert outcome.status == "answered" and outcome.attempts == 1
    assert outcome.result is REVENUE
    assert outcome.assumptions == ["Sales means revenue."]
    assert outcome.sql == db.executed[0]
    assert outcome.sql.endswith("LIMIT 1001")  # the cap plus one, to detect a cut result
    assert db.statuses == ["ok"]
    assert db.logged[0]["question"] == QUESTION and db.logged[0]["row_count"] == 1


async def test_the_model_is_given_only_the_relevant_schema_the_metric_and_the_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeDatabase(REVENUE).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(GOOD))
    outcome = await run(llm)

    prompt = llm.calls[0].user
    assert outcome.tables_shown == ("orders",)
    assert "TABLE orders:" in prompt
    for other in ("TABLE products", "TABLE customers", "TABLE expenses", "TABLE inventory_levels"):
        assert other not in prompt
    assert "revenue (money): SUM(orders.total_amount - orders.tax_amount)" in prompt
    assert "- last month: 2026-10-01 to 2026-10-31" in prompt
    assert f"<<<QUESTION\n{QUESTION}\nQUESTION>>>" in prompt


async def test_nothing_about_who_is_asking_reaches_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeDatabase(REVENUE).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(GOOD))
    who = caller("viewer", limited=True)
    await run(llm, who=who)

    text = llm.calls[0].text
    for secret in (str(TENANT), str(who.user_id), str(who.outlet_ids[0]), "Indiranagar",
                   "Brew & Bite", "tenant_id"):  # fmt: skip
        assert secret not in text, secret


async def test_the_query_runs_as_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    who = caller("viewer", limited=True)
    await run(FakeLLM().on("sql", draft(GOOD)), who=who)
    assert db.callers == [who]


async def test_the_row_cap_comes_from_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    await run(FakeLLM().on("sql", draft("SELECT id FROM orders")), max_rows=50)
    assert db.executed == ["SELECT id FROM orders LIMIT 51"]


# -- invalid SQL: refused, explained to the model, repaired ----------------------


@pytest.mark.parametrize(
    ("bad_sql", "code"),
    [
        ("DELETE FROM orders", "not_select"),
        ("SELECT id FROM orders; DROP TABLE orders", "multiple_statements"),
        ("SELECT email FROM app.users", "table_not_allowed"),
        ("SELECT c.phone FROM customers c", "column_not_allowed"),
        ("SELECT * FROM orders", "star_not_allowed"),
        ("SELECT set_config('app.tenant_id', 'x', true)", "function_not_allowed"),
        ("SELEC revenue FRM orders", "parse_error"),
    ],
)
async def test_unsafe_sql_never_runs_and_the_model_gets_one_more_try(
    monkeypatch: pytest.MonkeyPatch, bad_sql: str, code: str
) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(bad_sql)).on("sql_repair", draft(GOOD))
    outcome = await run(llm)

    assert outcome.status == "answered" and outcome.attempts == 2
    assert len(db.executed) == 1 and bad_sql not in db.executed  # only the good one ran
    assert db.statuses == ["rejected", "ok"]
    assert db.logged[0]["sql"] == bad_sql and db.logged[0]["reason"] == code
    repair_prompt = llm.calls_for("sql_repair")[0].user
    assert "PREVIOUS ATTEMPT FAILED" in repair_prompt and bad_sql in repair_prompt


async def test_three_unsafe_attempts_end_in_failed_and_nothing_ever_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase().install(monkeypatch)
    llm = (
        FakeLLM()
        .on("sql", draft("DROP TABLE orders"))
        .on("sql_repair", draft("TRUNCATE orders"), draft("SELECT email FROM app.users"))
    )
    outcome = await run(llm)

    assert outcome.status == "failed" and outcome.attempts == 3
    assert outcome.sql is None and outcome.result is None
    assert db.executed == []
    assert db.statuses == ["rejected", "rejected", "rejected"]
    assert len(llm.calls) == 3  # it stops: no endless loop


# -- invalid or incomplete model answers -----------------------------------------


async def test_a_malformed_model_answer_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    llm = FakeLLM().on("sql", LLMInvalidResponseError("sql")).on("sql_repair", draft(GOOD))
    outcome = await run(llm)
    assert outcome.status == "answered" and outcome.attempts == 2
    assert db.statuses == ["ok"]
    assert "not in the requested form" in llm.calls_for("sql_repair")[0].user


async def test_malformed_answers_three_times_end_in_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDatabase().install(monkeypatch)
    bad = LLMInvalidResponseError("sql")
    outcome = await run(FakeLLM().on("sql", bad).on("sql_repair", bad, bad))
    assert outcome.status == "failed" and outcome.attempts == 3


@pytest.mark.parametrize("empty", [None, "", "   "])
async def test_no_sql_means_the_question_cannot_be_answered(
    monkeypatch: pytest.MonkeyPatch, empty: str | None
) -> None:
    db = FakeDatabase().install(monkeypatch)
    llm = FakeLLM().on("sql", draft(empty, "There is no data about staff."))
    outcome = await run(llm, "Who is our best employee?")
    assert outcome.status == "unanswerable"
    assert outcome.explanation == "There is no data about staff."
    assert db.executed == [] and db.logged == []


async def test_a_model_outage_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDatabase().install(monkeypatch)
    with pytest.raises(LLMUnavailableError):
        await run(FakeLLM().on("sql", LLMUnavailableError("RateLimitError")))
    assert db.executed == []


# -- database errors and timeouts ------------------------------------------------


async def test_a_database_error_goes_back_to_the_model_and_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = QueryFailedError("22P02", 'invalid input syntax for type integer: "Indiranagar"')
    db = FakeDatabase(error, REVENUE).install(monkeypatch)
    llm = FakeLLM().on("sql", draft("SELECT CAST(o.name AS INT) AS n FROM outlets o"))
    llm.on("sql_repair", draft(GOOD))
    outcome = await run(llm)

    assert outcome.status == "answered" and outcome.attempts == 2
    assert db.statuses == ["error", "ok"]
    assert db.logged[0]["reason"] == "22P02"
    assert "PostgreSQL said: invalid input syntax" in llm.calls_for("sql_repair")[0].user


async def test_a_database_error_three_times_ends_in_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    error = QueryFailedError("42883", "operator does not exist")
    db = FakeDatabase(error, error, error).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(GOOD)).on("sql_repair", draft(GOOD), draft(GOOD))
    outcome = await run(llm)
    assert outcome.status == "failed" and outcome.result is None
    assert db.statuses == ["error", "error", "error"]


async def test_a_timeout_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDatabase(QueryTimeoutError()).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(GOOD))
    outcome = await run(llm)
    assert outcome.status == "timeout" and outcome.result is None
    assert db.statuses == ["timeout"]
    assert len(llm.calls) == 1  # asking again would only wait again


# -- the aggregation check: safe to run, but would the figure be right? -------------

FAN_OUT = (
    "SELECT ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue FROM orders o "
    "JOIN order_items oi ON oi.order_id = o.id WHERE o.status = 'completed'"
)
BY_LINES = (
    "SELECT ROUND(SUM(oi.line_total), 2) AS revenue FROM order_items oi "
    "JOIN orders o ON o.id = oi.order_id WHERE o.status = 'completed'"
)


async def test_a_sum_over_repeated_rows_never_runs_and_goes_back_to_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(FAN_OUT)).on("sql_repair", draft(BY_LINES))
    outcome = await run(llm)

    assert outcome.status == "answered" and outcome.attempts == 2
    assert len(db.executed) == 1 and "line_total" in db.executed[0]  # the wrong one never ran
    assert db.statuses == ["rejected", "ok"]
    assert db.logged[0]["reason"] == "unsafe_aggregation"
    assert "total_amount" in db.logged[0]["sql"]
    repair = llm.calls_for("sql_repair")[0].user
    assert "PREVIOUS ATTEMPT FAILED" in repair and "orders is joined to order_items" in repair
    assert f"<<<QUESTION\n{QUESTION}\nQUESTION>>>" in repair  # the user's own words, unchanged


async def test_three_sums_over_repeated_rows_end_in_failed_and_nothing_ever_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase().install(monkeypatch)
    llm = FakeLLM().on("sql", draft(FAN_OUT)).on("sql_repair", draft(FAN_OUT), draft(FAN_OUT))
    outcome = await run(llm)

    assert outcome.status == "failed" and outcome.attempts == 3
    assert outcome.sql is None and outcome.result is None
    assert db.executed == []
    assert [e["reason"] for e in db.logged] == ["unsafe_aggregation"] * 3
    assert len(llm.calls) == 3


async def test_the_security_guard_still_decides_first(monkeypatch: pytest.MonkeyPatch) -> None:
    # unsafe to run AND a fan-out: it is refused for the security reason, as before
    db = FakeDatabase().install(monkeypatch)
    both = "SELECT SUM(o.total_amount), o.* FROM orders o JOIN order_items oi ON oi.order_id = o.id"
    llm = FakeLLM().on("sql", draft(both)).on("sql_repair", draft(both), draft(both))
    await run(llm)
    assert [e["reason"] for e in db.logged] == ["star_not_allowed"] * 3


async def test_a_sound_join_runs_exactly_as_the_guard_left_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    outcome = await run(FakeLLM().on("sql", draft(BY_LINES)))
    assert outcome.status == "answered" and outcome.attempts == 1
    assert db.executed == [outcome.sql] and db.statuses == ["ok"]
    assert outcome.sql is not None and outcome.sql.endswith("LIMIT 1001")


async def test_the_aggregation_check_is_run_as_the_same_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(FAN_OUT)).on("sql_repair", draft(BY_LINES))
    await run(llm)
    assert [c.tenant_id for c in db.callers] == [TENANT]
    assert all("tenant" not in call.user.lower() for call in llm.calls)


# -- the correctness check: five more mistakes, and notes ------------------------------

LEFT_ON = (
    "SELECT ROUND(SUM(i.line_total), 2) AS revenue FROM order_items i "
    "LEFT JOIN orders o ON o.id = i.order_id AND o.status = 'completed'"
)
INNER_ON = (
    "SELECT ROUND(SUM(i.line_total), 2) AS revenue FROM order_items i "
    "JOIN orders o ON o.id = i.order_id AND o.status = 'completed'"
)


async def test_a_status_condition_in_a_left_joins_on_never_runs_and_the_inner_join_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(LEFT_ON)).on("sql_repair", draft(INNER_ON))
    outcome = await run(llm)

    assert outcome.status == "answered" and outcome.attempts == 2
    assert len(db.executed) == 1 and "LEFT JOIN" not in db.executed[0]  # the wrong one never ran
    assert db.statuses == ["rejected", "ok"]
    assert db.logged[0]["reason"] == "missing_status_filter" and "LEFT JOIN" in db.logged[0]["sql"]
    repair = llm.calls_for("sql_repair")[0].user
    assert "PREVIOUS ATTEMPT FAILED" in repair
    assert "in the ON of a LEFT JOIN, which keeps every row of order_items" in repair
    assert f"<<<QUESTION\n{QUESTION}\nQUESTION>>>" in repair  # the user's own words, unchanged


async def test_a_status_condition_in_an_inner_joins_on_runs_first_time_as_the_guard_left_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    outcome = await run(FakeLLM().on("sql", draft(INNER_ON)))
    assert outcome.status == "answered" and outcome.attempts == 1
    assert db.executed == [outcome.sql] and db.statuses == ["ok"]
    assert outcome.assumptions == []  # completed orders: nothing to add


async def test_three_left_join_attempts_end_in_failed_and_nothing_ever_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase().install(monkeypatch)
    llm = FakeLLM().on("sql", draft(LEFT_ON)).on("sql_repair", draft(LEFT_ON), draft(LEFT_ON))
    outcome = await run(llm)
    assert outcome.status == "failed" and outcome.attempts == 3
    assert outcome.sql is None and outcome.result is None and db.executed == []
    assert [e["reason"] for e in db.logged] == ["missing_status_filter"] * 3


CLEARLY_WRONG = {
    "sum_distinct": (
        "SELECT SUM(DISTINCT o.total_amount) AS revenue FROM orders o WHERE o.status = 'completed'"
    ),
    "left_join_undone": (
        "SELECT t.name AS outlet FROM outlets t LEFT JOIN orders o ON o.outlet_id = t.id "
        "WHERE o.channel = 'online'"
    ),
    "missing_status_filter": "SELECT SUM(o.total_amount) AS revenue FROM orders o",
    "timestamp_period": (
        "SELECT COUNT(*) AS orders FROM orders o WHERE o.status = 'completed' "
        "AND o.ordered_at BETWEEN DATE '2026-10-01' AND DATE '2026-10-31'"
    ),
    "count_without_grain": (
        "SELECT COUNT(*) AS n FROM orders o JOIN customer_visits v ON v.outlet_id = o.outlet_id"
    ),
}


@pytest.mark.parametrize("code", CLEARLY_WRONG)
async def test_each_kind_of_clearly_wrong_query_is_refused_logged_and_repaired(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(CLEARLY_WRONG[code])).on("sql_repair", draft(GOOD))
    outcome = await run(llm)
    assert outcome.status == "answered" and outcome.attempts == 2
    assert db.statuses == ["rejected", "ok"] and db.logged[0]["reason"] == code
    assert len(db.executed) == 1 and db.executed[0] == outcome.sql


async def test_the_earlier_locks_decide_before_the_correctness_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase().install(monkeypatch)
    # unsafe to run, a fan-out, and no status: the guard's reason is the one given
    unsafe = "SELECT SUM(DISTINCT o.total_amount), o.* FROM orders o"
    # safe to run, a fan-out, and no status: the aggregation check's reason is the one given
    fan_out = (
        "SELECT SUM(o.total_amount) AS v FROM orders o JOIN order_items i ON i.order_id = o.id"
    )
    for sql, reason in ((unsafe, "star_not_allowed"), (fan_out, "unsafe_aggregation")):
        db.logged.clear()
        llm = FakeLLM().on("sql", draft(sql)).on("sql_repair", draft(sql), draft(sql))
        await run(llm)
        assert [e["reason"] for e in db.logged] == [reason] * 3
    assert db.executed == []


async def test_a_note_about_the_statuses_counted_follows_the_models_own_assumptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeDatabase(REVENUE).install(monkeypatch)
    refunded = (
        "SELECT ROUND(SUM(o.total_amount), 2) AS refunded FROM orders o WHERE o.status = 'refunded'"
    )
    llm = FakeLLM().on("sql", draft(refunded, "Refunds.", ["All dates on record."]))
    outcome = await run(llm, question="What is the total value of refunded orders?")
    assert outcome.status == "answered" and outcome.attempts == 1
    assert outcome.assumptions == ["All dates on record.", "Counts refunded orders only."]


async def test_a_note_about_rows_a_join_leaves_out_reaches_the_assumptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeDatabase(REVENUE).install(monkeypatch)
    by_segment = (
        "SELECT c.segment AS segment, ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue "
        "FROM orders o JOIN customers c ON c.id = o.customer_id "
        "WHERE o.status = 'completed' GROUP BY c.segment"
    )
    outcome = await run(FakeLLM().on("sql", draft(by_segment)))
    assert outcome.assumptions == ["Orders with no recorded customer are not included."]


async def test_a_note_the_model_already_gave_is_not_repeated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeDatabase(REVENUE).install(monkeypatch)
    refunded = "SELECT SUM(o.total_amount) AS refunded FROM orders o WHERE o.status = 'refunded'"
    llm = FakeLLM().on("sql", draft(refunded, "", ["Counts refunded orders only."]))
    outcome = await run(llm)
    assert outcome.assumptions == ["Counts refunded orders only."]


async def test_the_correctness_check_never_sees_or_names_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(LEFT_ON)).on("sql_repair", draft(INNER_ON))
    await run(llm)
    assert [c.tenant_id for c in db.callers] == [TENANT]
    assert all("tenant" not in call.user.lower() for call in llm.calls)


async def test_a_left_join_undone_by_a_later_inner_join_never_runs_and_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(REVENUE).install(monkeypatch)
    undone = (
        "SELECT t.name AS outlet, COUNT(o.id) AS orders FROM outlets t "
        "LEFT JOIN orders o ON o.outlet_id = t.id AND o.status = 'completed' "
        "JOIN customers c ON c.id = o.customer_id GROUP BY t.name"
    )
    kept = undone.replace("JOIN customers c", "LEFT JOIN customers c").replace(
        "LEFT JOIN orders o", "LEFT JOIN orders o", 1
    )
    llm = FakeLLM().on("sql", draft(undone)).on("sql_repair", draft(kept))
    outcome = await run(llm)

    assert outcome.status == "answered" and outcome.attempts == 2
    assert db.statuses == ["rejected", "ok"] and db.logged[0]["reason"] == "left_join_undone"
    assert len(db.executed) == 1 and "LEFT JOIN customers" in db.executed[0]
    repair = llm.calls_for("sql_repair")[0].user
    assert "the later JOIN to customers" in repair and "a LEFT JOIN as well" in repair
    assert f"<<<QUESTION\n{QUESTION}\nQUESTION>>>" in repair
