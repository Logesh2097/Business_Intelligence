"""REAL DRIVERS, NO SERVER: a real model cannot be reached by accident.

Three layers, each proved here without calling any model:
1. `make eval` alone plans no test run (read with `make -n`, which executes nothing);
2. the real-model test is skipped unless the confirmation is present;
3. under any test run, the one line that reaches the provider refuses.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.llm.openai import OpenAILLM
from tests.evals import gate

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def plan(*arguments: str, **environment: str) -> str:
    """What `make <target>` WOULD run. `-n` prints the commands and executes none."""
    result = subprocess.run(  # noqa: S603
        ["make", "-n", *arguments],  # noqa: S607
        cwd=ROOT,
        env={**os.environ, **environment},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.stdout + result.stderr


# -- 1. the Makefile ---------------------------------------------------------------------


def test_make_eval_alone_plans_no_test_run_and_says_how_to_confirm() -> None:
    planned = plan("eval")
    assert "pytest" not in planned and "test_golden_eval" not in planned
    assert "Nothing was run" in planned and "make eval CONFIRM_REAL_MODEL=1" in planned
    assert "exit 1" in planned  # it ends as a failure, not as a quiet success


@pytest.mark.parametrize("value", ["0", "yes", "true", "", "01", "1 "])
def test_only_the_exact_confirmation_counts(value: str) -> None:
    assert "pytest" not in plan("eval", f"CONFIRM_REAL_MODEL={value}")


def test_a_confirmation_left_exported_in_the_shell_does_not_count() -> None:
    planned = plan("eval", CONFIRM_REAL_MODEL="1", BI_EVAL_CONFIRM_REAL_MODEL="1")
    assert "pytest" not in planned and "Nothing was run" in planned


def test_the_old_variable_no_longer_starts_anything() -> None:
    assert "pytest" not in plan("eval", BI_EVAL_REAL_MODEL="1")


def test_with_the_confirmation_typed_make_eval_plans_exactly_the_real_model_test() -> None:
    planned = plan("eval", "CONFIRM_REAL_MODEL=1", "ONLY=A1-revenue-last-month")
    assert "BI_EVAL_CONFIRM_REAL_MODEL=1" in planned
    assert 'BI_EVAL_ONLY="A1-revenue-last-month"' in planned
    assert planned.count("pytest") == 1 and "tests/evals/test_golden_eval.py" in planned


def test_make_eval_check_clears_the_confirmation_and_runs_only_the_dataset_tests() -> None:
    planned = plan("eval-check", CONFIRM_REAL_MODEL="1", BI_EVAL_CONFIRM_REAL_MODEL="1")
    assert "BI_EVAL_CONFIRM_REAL_MODEL= " in planned and "BI_EVAL_REAL_MODEL= " in planned
    assert "tests/evals/test_golden_dataset.py" in planned and "test_golden_eval" not in planned


@pytest.mark.parametrize("target", ["ci", "test", "test-integration"])
def test_no_other_target_confirms_or_runs_the_real_model_test(target: str) -> None:
    planned = plan(target, CONFIRM_REAL_MODEL="1")
    assert "BI_EVAL_CONFIRM_REAL_MODEL=1" not in planned and "test_golden_eval" not in planned


# -- 2. the switch -------------------------------------------------------------------------


def test_the_switch_is_one_variable_with_one_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(gate.VARIABLE, raising=False)
    assert not gate.confirmed()
    for value in ("0", "true", "yes", "", "11"):
        monkeypatch.setenv(gate.VARIABLE, value)
        assert not gate.confirmed()
    monkeypatch.setenv("BI_EVAL_REAL_MODEL", "1")  # the name used before the gate
    monkeypatch.delenv(gate.VARIABLE)
    assert not gate.confirmed()
    monkeypatch.setenv(gate.VARIABLE, "1")
    assert gate.confirmed()


def test_this_test_run_is_not_confirmed() -> None:
    """`make ci` must never be a real-model run."""
    assert not gate.confirmed()


def test_the_real_model_test_is_skipped_without_the_confirmation() -> None:
    from tests.evals import test_golden_eval

    mark = test_golden_eval.pytestmark
    assert mark.name == "skipif" and mark.args == (True,)  # evaluated when collected: skip
    assert "CONFIRM_REAL_MODEL=1" in mark.kwargs["reason"]


# -- 3. the guard under every test -------------------------------------------------------------


class Answer(BaseModel):
    value: int


def a_client() -> OpenAILLM:
    return OpenAILLM(
        Settings(_env_file=None, env="test", openai_api_key="sk-test-not-real", llm_model="m")  # type: ignore[call-arg]
    )


async def test_a_real_provider_call_from_a_test_is_refused_before_anything_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(gate.VARIABLE, raising=False)
    with pytest.raises(gate.RealModelCallBlocked, match="Nothing was sent"):
        await a_client().structured(task="t", system="s", user="u", schema=Answer)


async def test_the_refusal_is_not_mistaken_for_a_provider_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It must fail the test loudly, not turn into a polite 'model unavailable'."""
    monkeypatch.delenv(gate.VARIABLE, raising=False)
    from app.llm.base import LLMError

    with pytest.raises(AssertionError) as caught:
        await a_client().structured(task="t", system="s", user="u", schema=Answer)
    assert not isinstance(caught.value, LLMError)


async def test_a_client_stubbed_by_a_test_still_works() -> None:
    client = a_client()

    async def stub(model: Any, schema: Any, messages: Any) -> Any:
        return {"parsed": Answer(value=4), "raw": None}

    client._invoke = stub  # type: ignore[method-assign]
    answer = await client.structured(task="t", system="s", user="u", schema=Answer)
    assert answer.value == 4


def test_the_guard_is_installed_once_for_the_whole_run() -> None:
    assert getattr(OpenAILLM._invoke, "guarded", False)
    before = OpenAILLM._invoke
    gate.install()
    assert OpenAILLM._invoke is before
