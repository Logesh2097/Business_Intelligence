"""EVALUATION: the golden set against the REAL model (`make eval`). Costs model calls.

The only tests in the project that call a real model (CLAUDE.md rule 9). They do not
run in `make ci`, `make test-integration` or `make eval-check`: they are skipped unless
the confirmation of tests/evals/gate.py is present, which only
`make eval CONFIRM_REAL_MODEL=1` sets, and a model key is configured. `make eval` alone
prints what it would do and stops.

Each active question goes through the application exactly as a user's would
(`workflows.assistant.ask`), on bi_eval, as the case's own user. The result is
compared with the stored expected rows, and a failure is given its cause. A complete
record of every question is saved to tests/evals/results/ after each one (runner.py).

    make eval CONFIRM_REAL_MODEL=1                  every active question
    make eval CONFIRM_REAL_MODEL=1 ONLY=<case id>   one question (several: comma-separated)

Expected values are never changed to fit what a model returned.
"""

from __future__ import annotations

import os

import pytest

from app.core.config import Settings
from app.store.connections import Stores
from tests.evals import database, gate
from tests.evals.dataset import load_dataset
from tests.evals.runner import (
    EVALUATION_MAX_RETRIES,
    REAL,
    Pacer,
    PacingNotConfigured,
    ResultsFile,
    evaluation_llm,
    interval_from_environment,
    provider_of,
    remaining,
    results_report,
    run_all,
)

DATASET = load_dataset()
ONLY = [name.strip() for name in os.environ.get("BI_EVAL_ONLY", "").split(",") if name.strip()]

pytestmark = pytest.mark.skipif(
    not gate.confirmed(),
    reason="the real-model evaluation runs only through `make eval CONFIRM_REAL_MODEL=1`",
)


async def test_the_golden_set_with_the_real_model(
    base_settings: Settings, eval_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    if not base_settings.openai_api_key:
        pytest.skip("no model key is configured (BI_OPENAI_API_KEY)")
    unknown = [name for name in ONLY if name not in {c.id for c in DATASET.cases}]
    assert not unknown, f"no such case: {unknown}"
    cases = [c for c in DATASET.active if not ONLY or c.id in ONLY]
    # the spacing between model calls is chosen by the person who runs this, from their
    # account's quota. Without it the run stops here, before any call is made.
    try:
        pacer = Pacer(interval_from_environment())
    except PacingNotConfigured as problem:
        pytest.fail(f"Nothing was sent to the model. {problem}", pytrace=False)

    # the same database and pinned date as the dataset checks, with the model switched on
    settings = database.eval_settings(base_settings, keep_model=True)
    assert database.database_of(settings.pg_dsn) == database.EVAL_DATABASE
    assert settings.fake_today == eval_settings.fake_today == database.REFERENCE_DATE
    # one client for the whole run, with its own retries off: one call, one request
    llm = evaluation_llm(settings)
    results = ResultsFile(
        model=settings.llm_model, provider=provider_of(settings), dataset=DATASET, only=ONLY,
        kind=REAL, client_max_retries=EVALUATION_MAX_RETRIES,
    )  # fmt: skip
    stores = await Stores.open(settings)
    try:
        # every question once, in order; a record is saved after each; nothing is retried;
        # three provider failures in a row stop the run; every model call waits its turn
        data = await run_all(
            cases, llm_for=lambda case: llm, stores=stores, settings=settings, results=results,
            pacer=pacer,
        )  # fmt: skip
    finally:
        await stores.close()

    with capsys.disabled():
        print("\n" + results_report(data))
        print(f"\nresults: {results.path}")
        if remaining(data):
            print("not answered (a later run needs its own approval): " + ",".join(remaining(data)))
    summary = data["summary"]
    if summary["failed"] or summary["not_executed"]:  # the same rule as before
        # pytrace=False: the failure is this one line. A traceback would print this test's
        # arguments, and one of them is the settings object.
        pytest.fail(
            f"{summary['failed']} of {summary['scored']} scored questions failed, "
            f"{summary['provider_failures']} hit a provider failure, "
            f"{summary['not_executed']} were not executed. See {results.path}",
            pytrace=False,
        )
