"""MOCKED: the evaluation's pacing between model calls, and what it must not change.

No database, no model, no real waiting (one test sleeps 0.15 s to show real time works).
`run_all` is driven with a stand-in for the application, so what is tested here is the
harness: every model call waits its turn, no call is ever repeated, three provider
failures in a row still stop the run, and a results file is never written over.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.llm.base import LLMUnavailableError
from tests.evals import runner, test_golden_eval
from tests.evals.dataset import Case, load_dataset
from tests.evals.runner import (
    INTERVAL_VARIABLE,
    NOT_PACED,
    REAL,
    SCRIPTED,
    Outcome,
    Pacer,
    PacingNotConfigured,
    Recording,
    ResultsFile,
    interval_from_environment,
    remaining,
    results_report,
    run_all,
)

DATASET = load_dataset()
TASKS = ("route", "sql", "sql_repair", "agent_report", "summary", "supervisor_report")


class Answer(BaseModel):
    value: int = 1


class Time:
    """A clock that only moves when something sleeps or a model 'works'."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds

    def pacer(self, interval: float) -> Pacer:
        return Pacer(interval, clock=self.clock, sleep=self.sleep)


class Model:
    """A scripted model. Counts every call; fails where told; can take (fake) time."""

    def __init__(self, fail: bool = False, takes: float = 0.0, at: Time | None = None) -> None:
        self.fail, self.takes, self.at = fail, takes, at
        self.tasks: list[str] = []
        self.started: list[float] = []

    async def structured(self, *, task: str, schema: Any, **_: Any) -> Any:
        self.tasks.append(task)
        if self.at is not None:
            self.started.append(self.at.now)
            self.at.now += self.takes
        if self.fail:
            raise LLMUnavailableError("RateLimitError")
        return schema()


async def ask(llm: Any, task: str) -> Any:
    return await llm.structured(task=task, system="s", user="u", schema=Answer)


# -- the interval is chosen by the person running the evaluation ---------------------------


@pytest.mark.parametrize(("raw", "seconds"), [("0", 0.0), ("7", 7.0), (" 6.5 ", 6.5), ("600", 600)])
def test_the_interval_is_read_from_its_variable(raw: str, seconds: float) -> None:
    assert interval_from_environment({INTERVAL_VARIABLE: raw}) == seconds


@pytest.mark.parametrize("raw", [None, "", "  ", "fast", "-1", "nan", "inf", "601", "6s"])
def test_a_missing_or_unusable_interval_is_refused_and_no_quota_is_assumed(
    raw: str | None,
) -> None:
    environ = {} if raw is None else {INTERVAL_VARIABLE: raw}
    with pytest.raises(PacingNotConfigured, match=INTERVAL_VARIABLE):
        interval_from_environment(environ)


def test_the_real_model_test_asks_for_the_interval_before_it_builds_the_model() -> None:
    source = inspect.getsource(test_golden_eval.test_the_golden_set_with_the_real_model)
    assert source.index("interval_from_environment()") < source.index("evaluation_llm(")
    assert source.index("interval_from_environment()") < source.index("run_all(")
    assert "pacer=pacer" in source


# -- the pacer --------------------------------------------------------------------------------


async def test_the_first_call_does_not_wait_and_each_later_one_keeps_the_interval() -> None:
    at = Time()
    pacer = at.pacer(6.0)
    starts = []
    for _ in range(4):
        waited = await pacer.wait()
        starts.append((at.now, waited))
    assert starts == [(1000.0, 0.0), (1006.0, 6.0), (1012.0, 6.0), (1018.0, 6.0)]
    assert (pacer.calls, pacer.waits, pacer.waited) == (4, 3, 18.0)


async def test_time_the_model_itself_took_counts_towards_the_interval() -> None:
    at = Time()
    pacer = at.pacer(6.0)
    await pacer.wait()
    at.now += 2.5  # the call took 2.5 s
    assert await pacer.wait() == 3.5
    at.now += 9.0  # a slow call: the interval has already passed
    assert await pacer.wait() == 0.0
    assert at.sleeps == [3.5]


async def test_an_interval_of_zero_never_sleeps() -> None:
    at = Time()
    pacer = at.pacer(0)
    assert [await pacer.wait() for _ in range(5)] == [0.0] * 5
    assert at.sleeps == [] and pacer.metadata()["waits"] == 0


async def test_calls_made_at_the_same_moment_are_queued_one_interval_apart() -> None:
    at = Time()
    pacer = at.pacer(4.0)
    model = Model(at=at)
    recording = Recording(model, pacer)
    # two agents working side by side, as a multi-agent question does
    await asyncio.gather(*(ask(recording, "sql") for _ in range(5)))
    assert model.started == [1000.0, 1004.0, 1008.0, 1012.0, 1016.0]


async def test_real_time_is_kept_with_the_real_clock() -> None:
    pacer = Pacer(0.05)
    started = time.monotonic()
    for _ in range(4):
        await pacer.wait()
    assert 0.15 <= time.monotonic() - started < 1.0
    assert pacer.waits == 3 and pacer.waited >= 0.15


def test_an_unusable_interval_cannot_build_a_pacer() -> None:
    for bad in (-1, float("nan"), 10_000):
        with pytest.raises(PacingNotConfigured):
            Pacer(bad)


# -- every kind of model call goes through it ----------------------------------------------


async def test_every_task_is_paced_and_its_own_time_excludes_the_wait() -> None:
    at = Time()
    model = Model(at=at)
    recording = Recording(model, at.pacer(5.0))
    for task in TASKS:
        await ask(recording, task)
    assert model.tasks == list(TASKS)  # each sent once, in order
    assert model.started == [1000.0 + 5 * n for n in range(len(TASKS))]
    assert [c["waited_seconds"] for c in recording.calls] == [0.0, 5.0, 5.0, 5.0, 5.0, 5.0]
    assert all(c["seconds"] < 1 for c in recording.calls)  # real time: the wait is not in it


async def test_without_a_pacer_nothing_waits() -> None:
    model = Model()
    recording = Recording(model)
    started = time.monotonic()
    for task in TASKS * 50:
        await ask(recording, task)
    assert time.monotonic() - started < 1.0
    assert {c["waited_seconds"] for c in recording.calls} == {0.0}


async def test_a_failed_call_is_paced_like_any_other_and_is_not_sent_again() -> None:
    at = Time()
    model = Model(fail=True, at=at)
    recording = Recording(model, at.pacer(5.0))
    for _ in range(3):
        with pytest.raises(LLMUnavailableError):
            await ask(recording, "route")
    assert model.tasks == ["route"] * 3  # three asks, three sends: no retry
    assert model.started == [1000.0, 1005.0, 1010.0]
    assert all(c["outcome"].startswith("unavailable") for c in recording.calls)


# -- the whole run: pacing, no retry, the stop rule ------------------------------------------


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """`run_all` with the application and the database replaced: three calls a question."""

    async def run_case(case: Case, *, llm: Any, **_: Any) -> Outcome:
        for task in ("route", "sql", "agent_report"):
            await ask(llm, task)
        return Outcome(case.id, True, route="general")

    monkeypatch.setattr(runner, "run_case", run_case)
    monkeypatch.setattr(runner, "_attempts", lambda *a, **k: [])
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
    return tmp_path / "results"


def results(kind: str = SCRIPTED) -> ResultsFile:
    return ResultsFile(model="unit test", provider="none", dataset=DATASET, only=(), kind=kind)


async def run(models: list[Model], pacer: Pacer | None, file: ResultsFile) -> dict[str, Any]:
    cases = list(DATASET.active)[: len(models)]
    by_case = dict(zip([c.id for c in cases], models, strict=True))
    return await run_all(
        cases, llm_for=lambda case: by_case[case.id], stores=None,  # type: ignore[arg-type]
        settings=None, results=file, pacer=pacer,  # type: ignore[arg-type]
    )  # fmt: skip


async def test_a_run_is_paced_from_one_question_to_the_next_and_says_so(harness: Path) -> None:
    at = Time()
    models = [Model(at=at) for _ in range(4)]
    file = results()
    data = await run(models, at.pacer(6.0), file)

    starts = [s for m in models for s in m.started]
    assert starts == [1000.0 + 6 * n for n in range(12)]  # 12 calls, 6 s apart, across questions
    assert all(m.tasks == ["route", "sql", "agent_report"] for m in models)
    assert data["run"]["pacing"] == {
        "paced": True,
        "variable": INTERVAL_VARIABLE,
        "min_call_interval_seconds": 6.0,
        "calls_paced": 12,
        "waits": 11,
        "waited_seconds_total": 66.0,
    }
    first, second = data["questions"][0], data["questions"][1]
    assert (first["seconds_waiting"], second["seconds_waiting"]) == (12.0, 18.0)
    assert data["summary"]["seconds_waiting_total"] == 66.0
    assert "pacing: at least 6.0 s between model calls | waited 66.0 s in 11 waits" in (
        results_report(data)
    )
    assert json.loads(file.path.read_text())["run"]["pacing"]["waited_seconds_total"] == 66.0


async def test_a_scripted_run_is_not_paced_and_does_not_sleep(
    harness: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(seconds: float) -> None:
        raise AssertionError(f"a scripted run slept for {seconds} s")

    monkeypatch.setattr(asyncio, "sleep", never)
    data = await run([Model() for _ in range(6)], None, results())
    assert data["run"]["pacing"] == NOT_PACED
    assert data["summary"]["seconds_waiting_total"] == 0.0
    assert "pacing: none" in results_report(data)


async def test_three_provider_failures_in_a_row_still_stop_the_run(harness: Path) -> None:
    at = Time()
    models = [
        Model(at=at),
        *[Model(fail=True, at=at) for _ in range(3)],
        Model(at=at),
        Model(at=at),
    ]
    data = await run(models, at.pacer(6.0), results())
    cases = [c.id for c in list(DATASET.active)[:6]]

    assert [q["id"] for q in data["questions"]] == cases[:4]
    assert data["not_executed"] == cases[4:]
    assert (
        data["run"]["stopped_early"] == f"3 provider failures in a row; stopped before {cases[4]}"
    )
    assert data["summary"]["provider_failures"] == 3 and data["summary"]["scored"] == 1
    assert data["summary"]["not_executed"] == 2
    assert remaining(data) == cases[1:]
    # the questions after the stop were never sent to a model
    assert models[4].tasks == [] and models[5].tasks == []


async def test_a_failed_question_is_asked_once_and_never_again(harness: Path) -> None:
    at = Time()
    models = [Model(fail=True, at=at), Model(at=at), Model(fail=True, at=at), Model(at=at)]
    data = await run(models, at.pacer(6.0), results())

    # a failing model raises on its first call: one call for that question, and no more
    assert [m.tasks for m in models] == [
        ["route"], ["route", "sql", "agent_report"], ["route"], ["route", "sql", "agent_report"],
    ]  # fmt: skip
    assert [q["model_call_count"] for q in data["questions"]] == [1, 3, 1, 3]
    assert len({q["id"] for q in data["questions"]}) == 4  # each question appears once
    # a success between two failures starts the count again: the run was not stopped
    assert data["run"]["stopped_early"] is None and data["not_executed"] == []
    assert data["run"]["pacing"]["calls_paced"] == 8  # exactly the calls made, none added


# -- results files ---------------------------------------------------------------------------


def test_a_results_file_is_never_written_over(harness: Path) -> None:
    first, second, third = results(), results(), results()  # the same second, the same name
    assert len({first.path, second.path, third.path}) == 3
    first.save()
    kept = first.path.read_bytes()
    again = results()
    again.save()
    assert again.path != first.path and first.path.read_bytes() == kept


def test_a_scripted_run_is_labelled_in_its_name_and_inside(harness: Path) -> None:
    scripted, real = results(SCRIPTED), results(REAL)
    assert "-SCRIPTED-" in scripted.path.name and scripted.data["run"]["kind"] == "scripted"
    assert "SCRIPTED" not in real.path.name and real.data["run"]["kind"] == "real-model"
    scripted.save()
    assert results_report(scripted.data).startswith("SCRIPTED RUN, not a result of any model.")
    with pytest.raises(ValueError):
        results("something else")
    assert (harness / ".gitignore").read_text() == "*\n"


def test_a_results_file_from_before_pacing_can_still_be_read(harness: Path) -> None:
    file = results(REAL)
    file.save()
    old = json.loads(file.path.read_text())
    del old["run"]["pacing"], old["run"]["kind"]  # as the file of 2026-11-12 is
    assert "pacing: none" in results_report(old)
    assert remaining(old) == []
