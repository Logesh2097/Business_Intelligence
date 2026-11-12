"""LIVE, NO MODEL: the golden set itself is proved, against real PostgreSQL (`make eval-check`).

Nothing here asks a model anything. It proves that the dataset can be trusted before
a model is measured with it:

1. bi_eval is the approved data (baseline.yaml), built from the fixed inputs;
2. every stored expected row is what its verification query returns, as the owner,
   with the company and outlets written out, read-only;
3. a second, differently written query (pipeline_sql) returns the same rows through
   the guard, the aggregation check and row security, as the case's own user;
4. every trap gives the wrong figure recorded for it, and the aggregation check says
   about it what the case records;
5. the runner passes a correct answer and names the cause of a wrong one;
6. company and outlet limits hold, a viewer gets no SQL, and the demo database and
   the application's normal settings are untouched.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.agents.registry import AGENTS
from app.core.config import Settings, load_settings
from app.seed.generate import Data
from app.semantic.model import Vocabulary, load_vocabulary
from app.sqlguard import (
    Catalog,
    Checked,
    Facts,
    Refused,
    Reviewed,
    check,
    check_aggregation,
    check_correctness,
)
from app.store import analytics, users
from app.store.connections import Stores
from tests.evals import database
from tests.evals.compare import compare_rows
from tests.evals.dataset import GENERAL, GROUPS, Case, Part, Trap, bind, load_dataset
from tests.evals.runner import Outcome, decision_for, report, run_case, scripted_llm

DATASET = load_dataset()
VOCABULARY = load_vocabulary()
FACTS = Facts(**VOCABULARY.correctness_facts())
WITH_ROWS = [c for c in DATASET.active if c.parts]
PARTS = [(c, p) for c in WITH_ROWS for p in c.parts]
TRAPS = [(c, t) for c in DATASET.active for t in c.traps]


def ids(pairs: list[tuple[Case, Any]]) -> list[str]:
    return [f"{c.id}/{getattr(x, 'agent', None) or x.name}" for c, x in pairs]


def vocabulary_of(target: str) -> Vocabulary:
    return VOCABULARY if target == GENERAL else VOCABULARY.restricted_to(AGENTS[target].tables)


def scope_of(owner: psycopg.Connection[dict[str, Any]], email: str) -> dict[str, Any]:
    """:tenant and :outlets for a user, looked up, never typed."""
    user = owner.execute(
        "SELECT id, tenant_id FROM app.users WHERE email = %s", (email,)
    ).fetchone()
    assert user is not None, email
    outlets = owner.execute(
        "SELECT outlet_id FROM app.user_outlets WHERE user_id = %s", (user["id"],)
    ).fetchall()
    return {"tenant": user["tenant_id"], "outlets": [o["outlet_id"] for o in outlets]}


# -- 1. the dataset as a document, and the database it stands on -----------------------------


def test_the_dataset_has_58_questions_all_with_distinct_ids() -> None:
    assert DATASET.total == 58 == len(DATASET.cases)
    assert len({c.id for c in DATASET.cases}) == 58
    assert len(DATASET.active) + len(DATASET.pending) == 58


def test_the_questions_per_group_and_route_are_the_reported_ones() -> None:
    counts = DATASET.counts()
    assert {g: n["total"] for g, n in counts["groups"].items()} == {
        "A": 8, "B": 5, "C": 5, "D": 4, "E": 4, "F": 5, "G": 8,
        "H": 4, "I": 2, "J": 1, "K": 4, "L": 5, "M": 3,
    }  # fmt: skip
    assert set(counts["groups"]) == set(GROUPS)
    assert {r: n["total"] for r, n in counts["routes"].items()} == {
        "one agent": 29, "several agents": 5, "general": 21, "clarify": 1, "unsupported": 2,
    }  # fmt: skip
    for table in counts.values():
        assert sum(n["total"] for n in table.values()) == 58
        assert sum(n["active"] for n in table.values()) == len(DATASET.active)
        assert sum(n["pending"] for n in table.values()) == len(DATASET.pending)


def test_the_reference_date_is_the_one_the_database_is_built_for(eval_settings: Settings) -> None:
    assert DATASET.reference_date == database.REFERENCE_DATE == eval_settings.fake_today
    assert (database.SEED, database.DAYS) == (42, 120)


def test_bi_eval_is_exactly_the_approved_baseline(
    owner: psycopg.Connection[dict[str, Any]], eval_database: tuple[Settings, Data]
) -> None:
    assert owner.execute("SELECT current_database() AS d").fetchone() == {"d": "bi_eval"}
    assert database.check_baseline(owner, eval_database[1]) == []


def test_bi_eval_carries_the_marker_that_allows_it_to_be_dropped(
    owner: psycopg.Connection[dict[str, Any]],
) -> None:
    row = owner.execute(
        "SELECT shobj_description(oid, 'pg_database') AS note FROM pg_database "
        "WHERE datname = current_database()"
    ).fetchone()
    assert row == {"note": database.MARKER}


def test_no_company_row_points_at_another_companys_row(
    owner: psycopg.Connection[dict[str, Any]],
) -> None:
    broken = database.snapshot(owner)["broken_relationships"][0]
    assert set(broken.values()) == {0}


# -- 2. every stored value is what PostgreSQL says --------------------------------------------


@pytest.mark.parametrize(("case", "part"), PARTS, ids=ids(PARTS))
def test_the_stored_rows_are_what_the_verification_query_returns(
    owner: psycopg.Connection[dict[str, Any]], case: Case, part: Part
) -> None:
    rows = owner.execute(bind(part.verify_sql), scope_of(owner, case.user)).fetchall()  # type: ignore[arg-type]
    result = compare_rows(part.expected, rows)
    assert result.ok, f"{case.id}: golden.yaml is stale or wrong: {result.problems}\nnow: {rows}"
    assert not result.notes, "a verification query returns exactly the expected columns"


def test_a_verification_query_cannot_write(owner: psycopg.Connection[dict[str, Any]]) -> None:
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        owner.execute("DELETE FROM biz.expenses")


# -- 3. a second query, through the application's own locks, agrees ---------------------------


@pytest.mark.parametrize(("case", "part"), PARTS, ids=ids(PARTS))
async def test_a_correct_query_under_row_security_returns_the_same_rows(
    stores: Stores, case: Case, part: Part
) -> None:
    vocabulary = vocabulary_of(part.agent)
    catalog = Catalog(vocabulary.catalog())
    guarded = check(part.pipeline_sql, catalog, max_rows=1001)
    assert isinstance(guarded, Checked), f"the guard refuses pipeline_sql: {guarded}"
    sound = check_aggregation(guarded.sql, catalog, vocabulary.unique_keys())
    assert not isinstance(sound, Refused), f"the aggregation check refuses it: {sound.reason}"
    reviewed = check_correctness(guarded.sql, catalog, Facts(**vocabulary.correctness_facts()))
    assert not isinstance(reviewed, Refused), f"the correctness check refuses it: {reviewed}"

    caller = await users.find_caller_by_email(stores.postgres, case.user)
    assert caller is not None
    got = await analytics.run_checked_sql(stores.postgres, caller, guarded.sql, max_rows=1000)
    result = compare_rows(part.expected, got.rows)
    assert result.ok, f"{case.id}/{part.agent}: {result.problems}"


def test_the_safe_stock_join_is_judged_sound_not_merely_passed() -> None:
    """The positive control: order lines joined to stock on the whole key."""
    part = DATASET.case("G8-low-stock-with-units-sold").parts[0]
    assert "il.outlet_id = oi.outlet_id AND il.product_id = oi.product_id" in part.pipeline_sql
    catalog = Catalog(VOCABULARY.catalog())
    guarded = check(part.pipeline_sql, catalog)
    assert isinstance(guarded, Checked)
    sound = check_aggregation(guarded.sql, catalog, VOCABULARY.unique_keys())
    assert not isinstance(sound, Refused) and sound.uncertain == () and sound.judged >= 1


# -- 4. the traps ------------------------------------------------------------------------------


@pytest.mark.parametrize(("case", "trap"), TRAPS, ids=ids(TRAPS))
async def test_a_trap_gives_its_wrong_figure_and_the_check_says_what_is_recorded(
    stores: Stores, case: Case, trap: Trap
) -> None:
    catalog = Catalog(VOCABULARY.catalog())
    guarded = check(trap.sql, catalog, max_rows=1001)
    assert isinstance(guarded, Checked), "a trap is safe SQL: only its figure is wrong"
    # each check is asked alone, so a later change in either one is caught here
    verdict = check_aggregation(guarded.sql, catalog, VOCABULARY.unique_keys())
    assert ("refused" if isinstance(verdict, Refused) else "passed") == trap.aggregation_check
    reviewed = check_correctness(guarded.sql, catalog, FACTS)
    if trap.correctness_check == "refused":
        assert isinstance(reviewed, Refused) and reviewed.code == trap.correctness_code
    else:
        assert isinstance(reviewed, Reviewed) and reviewed.notes == trap.notes

    caller = await users.find_caller_by_email(stores.postgres, case.user)
    assert caller is not None
    async with stores.postgres.tenant_session(caller) as conn:
        row = await (await conn.execute(guarded.sql)).fetchone()  # type: ignore[arg-type]
    assert row is not None
    assert round(float(row["v"] or 0), 2) == round(trap.gives, 2)
    assert round(trap.gives, 2) != round(trap.correct, 2)


def test_there_are_23_traps_and_each_is_stopped_where_the_dataset_says() -> None:
    by_lock: dict[str, list[str]] = {"aggregation": [], "correctness": [], "golden_only": []}
    for _, trap in TRAPS:
        by_lock[trap.caught_by].append(trap.name)
    assert len(TRAPS) == 23 and len({t.sql for _, t in TRAPS}) == 23
    assert {lock: len(names) for lock, names in by_lock.items()} == {
        "aggregation": 13, "correctness": 7, "golden_only": 3,
    }  # fmt: skip
    assert sorted(by_lock["correctness"]) == [
        "LEFT JOIN orders with the status condition in ON",
        "LEFT JOIN undone by a filter in WHERE",
        "LEFT JOIN undone by a later inner join",
        "SUM(DISTINCT) as a fix for a fan-out",
        "SUM(DISTINCT) on one table",
        "revenue with no status condition",
        "the period ended on the timestamp with BETWEEN two dates",
    ]
    # wrong, and still seen by no check: an inner join that drops rows. Only this
    # dataset catches them.
    assert sorted(by_lock["golden_only"]) == [
        "INNER JOIN drops orders without a visit",
        "INNER JOIN drops outlets without expenses",
        "INNER JOIN drops stock rows without a movement",
    ]
    codes = sorted(t.correctness_code for _, t in TRAPS if t.correctness_code)
    assert codes == [
        "left_join_undone", "left_join_undone", "missing_status_filter",
        "missing_status_filter", "sum_distinct", "sum_distinct", "timestamp_period",
    ]  # fmt: skip
    safe_control = DATASET.case("G8-low-stock-with-units-sold")
    assert safe_control.traps == () and "safe_join_control" in safe_control.tags


def test_the_trap_that_still_passes_every_check_at_least_carries_its_note() -> None:
    trap = DATASET.case("H1-revenue-with-and-without-visit").traps[0]
    assert trap.caught_by == "golden_only"
    assert trap.notes == ("Orders with no recorded customer visit are not included.",)


def test_the_traps_cover_every_join_of_the_analysis_and_only_fan_outs_are_refused() -> None:
    refused = {t.name for _, t in TRAPS if t.aggregation_check == "refused"}
    passed = {t.name for _, t in TRAPS if t.aggregation_check == "passed"}
    for needed in (
        "orders joined to order_items, order totals added up",
        "purchase orders joined to purchase lines, order totals added up",
        "orders joined to visits on outlet_id",
        "orders joined to visits on customer_id",
        "expenses joined to orders on outlet_id",
        "customers joined to both orders and visits",
        "order_items joined to inventory_levels on product_id only",
    ):
        assert needed in refused, needed
    for needed in (
        "INNER JOIN drops orders without a visit",
        "INNER JOIN drops stock rows without a movement",
        "INNER JOIN drops outlets without expenses",
        "LEFT JOIN undone by a filter in WHERE",
        "SUM(DISTINCT) on one table",
        "SUM(DISTINCT) as a fix for a fan-out",
    ):
        assert needed in passed, needed  # wrong, but not a fan-out: only this dataset catches them


def test_sum_distinct_loses_a_real_sale(owner: psycopg.Connection[dict[str, Any]]) -> None:
    case = DATASET.case("J1-revenue-one-day-one-outlet")
    same = owner.execute(
        "SELECT o.total_amount - o.tax_amount AS amount, COUNT(*) AS orders FROM biz.orders o "
        "JOIN biz.outlets t ON t.id = o.outlet_id AND t.tenant_id = %(tenant)s "
        "WHERE o.tenant_id = %(tenant)s AND o.status = 'completed' "
        "AND o.order_date = DATE '2026-10-15' AND t.name = 'Whitefield' "
        "GROUP BY 1 HAVING COUNT(*) > 1",
        scope_of(owner, case.user),
    ).fetchall()
    assert len(same) == 1 and same[0]["orders"] == 2  # two different orders, one amount
    correct = case.parts[0].expected.rows[0]["revenue"]
    for trap in case.traps:
        assert round(correct - trap.gives, 2) == float(same[0]["amount"])  # exactly one sale lost


def test_total_revenue_and_product_revenue_are_two_figures_that_reconcile() -> None:
    net = DATASET.case("A1-revenue-last-month").parts[0].expected.rows[0]["revenue"]
    gross = DATASET.case("I1-gross-product-sales").parts[0].expected.rows[0]["product_sales"]
    all_three = DATASET.case("I2-gross-discounts-net").parts[0].expected.rows[0]
    assert gross != net
    assert all_three == {
        "gross_sales": gross,
        "discounts": round(gross - net, 2),
        "net_revenue": net,
    }


def test_this_month_and_month_to_date_have_the_documented_boundaries() -> None:
    for case_id in ("K2-revenue-this-month", "K3-visitors-month-to-date"):
        period = DATASET.case(case_id).period
        assert (str(period.start), str(period.end)) == ("2026-11-01", "2026-11-11")
    last = DATASET.case("K1-completed-orders-last-month").period
    assert (str(last.start), str(last.end)) == ("2026-10-01", "2026-10-31")


# -- 5. the runner, on a scripted model ------------------------------------------------------

EXECUTED: list[Outcome] = []


@pytest.mark.parametrize("case", DATASET.active, ids=[c.id for c in DATASET.active])
async def test_every_active_question_passes_through_the_application_with_a_correct_model(
    stores: Stores, eval_settings: Settings, case: Case
) -> None:
    outcome = await run_case(
        case, llm=scripted_llm(case), stores=stores, settings=eval_settings, check_text=False
    )
    EXECUTED.append(outcome)
    assert outcome.passed, f"{outcome.cause}: {outcome.details}"
    assert outcome.route == case.route.expect and set(outcome.agents) == set(case.route.agents)


def test_the_run_executed_every_active_question(capsys: pytest.CaptureFixture[str]) -> None:
    assert len(EXECUTED) == len(DATASET.active) == 58
    text = report(DATASET, EXECUTED, model="scripted (pipeline_sql), no real model")
    assert "dataset 58 | active 58 | pending 0 | executed 58 | passed 58 | failed 0" in text
    with capsys.disabled():
        print("\n" + text.split("\n")[0] + "\n" + text.split("\n")[1])


async def test_a_wrong_route_is_reported_as_a_routing_failure(
    stores: Stores, eval_settings: Settings
) -> None:
    case = DATASET.case("M1-two-independent-facts")
    llm = scripted_llm(case)
    llm._script["route"].clear()
    llm.on("route", decision_for(DATASET.case("D1-total-purchases")))  # general, as in Phase 5
    outcome = await run_case(case, llm=llm, stores=stores, settings=eval_settings)
    assert not outcome.passed and outcome.cause == "routing"
    assert "expected agents ['sales_agent', 'inventory_agent'], got general" in str(outcome.details)


async def test_a_query_refused_three_times_is_reported_as_an_execution_failure(
    stores: Stores, eval_settings: Settings
) -> None:
    case = DATASET.case("A5-revenue-of-low-stock-products")
    fan_out = case.traps[0].sql.replace(" AS v ", " AS revenue ")
    outcome = await run_case(
        case, llm=scripted_llm(case, sql={GENERAL: fan_out}), stores=stores, settings=eval_settings
    )
    assert not outcome.passed and outcome.cause == "execution"
    assert "status failed" in str(outcome.details)  # no figure, right or wrong, was returned


async def test_a_wrong_figure_no_check_can_see_is_reported_as_a_calculation_failure(
    stores: Stores, eval_settings: Settings
) -> None:
    case = DATASET.case("H1-revenue-with-and-without-visit")
    assert case.traps[0].caught_by == "golden_only"
    wrong = {case.parts[0].agent: case.traps[0].sql}
    outcome = await run_case(
        case, llm=scripted_llm(case, sql=wrong), stores=stores, settings=eval_settings
    )
    assert not outcome.passed and outcome.cause == "calculation"


CAUGHT_NOW = [(c, t) for c, t in TRAPS if t.caught_by == "correctness"]


@pytest.mark.parametrize(("case", "trap"), CAUGHT_NOW, ids=ids(CAUGHT_NOW))
async def test_a_trap_the_correctness_check_stops_returns_no_figure_through_the_application(
    stores: Stores,
    eval_settings: Settings,
    owner: psycopg.Connection[dict[str, Any]],
    case: Case,
    trap: Trap,
) -> None:
    """Before Step 4 these six ran and returned their wrong figure. Now nothing runs."""
    wrong = {case.parts[0].agent: trap.sql}
    outcome = await run_case(
        case, llm=scripted_llm(case, sql=wrong), stores=stores, settings=eval_settings
    )
    assert not outcome.passed and outcome.cause == "execution"
    assert "status failed" in str(outcome.details)
    owner.rollback()
    logged = owner.execute(
        "SELECT status, reason FROM app.query_log WHERE question = %s "
        "ORDER BY created_at DESC LIMIT 3",
        (case.question,),
    ).fetchall()
    # the three attempts just made: each refused with the recorded code, none ran
    assert logged == [{"status": "rejected", "reason": trap.correctness_code}] * 3


async def test_one_missing_outlet_fails_a_by_outlet_question(
    stores: Stores, eval_settings: Settings
) -> None:
    case = DATASET.case("A2-revenue-by-outlet")
    four = case.parts[0].pipeline_sql.replace(
        "GROUP BY t.name", "AND t.name <> 'Whitefield' GROUP BY t.name"
    )
    outcome = await run_case(
        case,
        llm=scripted_llm(case, sql={"sales_agent": four}),
        stores=stores,
        settings=eval_settings,
    )
    assert outcome.cause == "calculation"
    problems = " ".join(outcome.details["calculation"])
    assert "row count: expected 5, got 4" in problems and "missing row" in problems


# -- 6. security and regression ----------------------------------------------------------------


async def test_the_same_question_gives_each_company_and_each_viewer_only_its_own_rows(
    stores: Stores, eval_settings: Settings
) -> None:
    everyone = DATASET.case("A2-revenue-by-outlet")
    viewer = DATASET.case("L1-viewer-revenue-by-outlet")
    assert everyone.question == viewer.question and everyone.user != viewer.user
    all_rows, own_rows = everyone.parts[0].expected.rows, viewer.parts[0].expected.rows
    assert len(all_rows) == 5 and [r["outlet"] for r in own_rows] == ["Indiranagar", "Koramangala"]
    assert all(row in all_rows for row in own_rows)  # the same figures, fewer outlets

    other = DATASET.case("L2-other-company-viewer-purchases")
    brew = DATASET.case("D2-purchases-by-supplier")
    assert other.question == brew.question
    theirs = {r["supplier"] for r in other.parts[0].expected.rows}
    ours = {r["supplier"] for r in brew.parts[0].expected.rows}
    assert theirs.isdisjoint(ours) and all(s.startswith("UrbanMart") for s in theirs)

    # and through the application: the viewer of the other company, the same SQL
    outcome = await run_case(
        other, llm=scripted_llm(other), stores=stores, settings=eval_settings, check_text=True
    )
    assert outcome.passed or outcome.cause == "interpretation", outcome.details
    assert "calculation" not in outcome.details and "execution" not in outcome.details


async def test_a_limited_viewer_is_told_the_answer_is_limited(
    stores: Stores, eval_settings: Settings
) -> None:
    case = DATASET.case("L1-viewer-revenue-by-outlet")
    outcome = await run_case(
        case, llm=scripted_llm(case), stores=stores, settings=eval_settings, check_text=True
    )
    assert outcome.passed, outcome.details  # check_text also requires the scope note


@pytest.mark.parametrize(
    "case_id", ["L3-another-companys-data", "L5-delete-request", "L4-customer-phone-numbers"]
)
async def test_a_request_that_must_be_refused_runs_no_query(
    stores: Stores,
    eval_settings: Settings,
    owner: psycopg.Connection[dict[str, Any]],
    case_id: str,
) -> None:
    case = DATASET.case(case_id)
    before = owner.execute("SELECT count(*) AS n FROM biz.expenses").fetchone()
    outcome = await run_case(case, llm=scripted_llm(case), stores=stores, settings=eval_settings)
    assert outcome.passed, outcome.details
    owner.rollback()
    logged = owner.execute(
        "SELECT count(*) AS n FROM app.query_log WHERE question = %s AND status = 'ok'",
        (case.question,),
    ).fetchone()
    assert logged == {"n": 0}
    assert owner.execute("SELECT count(*) AS n FROM biz.expenses").fetchone() == before


def test_the_api_gives_a_viewer_the_rows_and_never_the_sql(eval_settings: Settings) -> None:
    from app.main import create_app

    url = "/api/v1/assistant/query"
    with TestClient(create_app(eval_settings)) as client:
        seen = {}
        for case_id in ("A2-revenue-by-outlet", "L1-viewer-revenue-by-outlet"):
            case = DATASET.case(case_id)
            client.app.state.llm = scripted_llm(case)  # type: ignore[attr-defined]
            response = client.post(
                url, json={"question": case.question}, headers={"X-Dev-User": case.user}
            )
            assert response.status_code == 200, response.text
            seen[case_id] = response.json()
    analyst, viewer = seen["A2-revenue-by-outlet"], seen["L1-viewer-revenue-by-outlet"]
    assert analyst["results"][0]["sql"] and len(analyst["results"][0]["data"]) == 5
    assert viewer["results"][0]["sql"] is None and len(viewer["results"][0]["data"]) == 2
    assert viewer["scope_note"] == "Limited to your outlets: Indiranagar, Koramangala."


def test_the_applications_normal_settings_are_untouched(
    base_settings: Settings, eval_settings: Settings
) -> None:
    fresh = load_settings()
    assert database.database_of(fresh.pg_dsn) == database.database_of(base_settings.pg_dsn)
    assert database.database_of(fresh.pg_dsn) != database.EVAL_DATABASE
    assert database.database_of(fresh.pg_owner_dsn) != database.EVAL_DATABASE
    assert fresh.fake_today == base_settings.fake_today  # not pinned by the evaluation
    assert database.database_of(eval_settings.pg_dsn) == database.EVAL_DATABASE
    assert eval_settings.openai_api_key == ""  # this run cannot reach a real model


def test_the_demo_database_was_only_read(
    base_settings: Settings, demo_before: dict[str, Any], eval_settings: Settings
) -> None:
    with database.read_only(base_settings.pg_owner_dsn) as demo:
        assert demo.execute("SELECT current_database() AS d").fetchone() != {"d": "bi_eval"}
        assert database.snapshot(demo) == demo_before
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            demo.execute("DELETE FROM biz.expenses")
