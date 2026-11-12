"""Runs a golden question through the application and says whether it passed, and why not.

It adds no pipeline: a question goes through `workflows.assistant.ask`, the same
entry point the API uses, as the case's own user. What comes back is judged against
the case. A failure is given one cause, the first of these that applies:

    routing         the supervisor chose a route or agents the case does not allow
    execution       a part did not run: refused, failed, declined, or data came back
                    for a question that must be refused
    calculation     the query ran and its rows are not the expected rows
    interpretation  the rows are right; the written answer leaves out an expected
                    figure, or does not say it is limited to the viewer's outlets

Instrumentation (Phase 6). `run_all` runs a list of cases and records, for every
question, everything needed to analyse it afterwards without asking the model again:
the answer, the rows, every SQL attempt and refusal code (read from the application's
own query log), every model call with its time and tokens, and the verdict. The record
is written to tests/evals/results/ after EVERY question, atomically, so an interruption
loses nothing. Three provider failures in a row stop the run; the rest are listed as
not executed and are never retried automatically. `replay` then puts every recorded
SQL through the three checks offline, and compares the run with the dataset's traps.

Pacing (Phase 6 step 2). A provider allows only so many calls a minute. `Pacer` keeps
at least a set number of seconds between the starts of two model calls, whatever the
task (route, sql, sql_repair, agent_report, summary, supervisor_report): every call of
a run goes through the one `Recording` wrapper, which asks the pacer first. The interval
comes from BI_EVAL_MIN_CALL_INTERVAL_SECONDS and has no default for a real run: this
file does not know any account's quota, so the person who runs the evaluation chooses
it. Pacing spaces calls out; it never repeats one. The scripted model is not paced.
The real client is built by `evaluation_llm` with its own retries switched off, so one
call is one provider request and no request escapes the pacing or the record.

No prompt text and no secret is recorded: of a model call only the task name, the
duration, the wait before it, the token counts and, on failure, the error class.

    python -m tests.evals.runner dry-run [--interrupt-after N] [--provider-fails-from N]
                                         [--min-call-interval SECONDS]
    python -m tests.evals.runner replay <results file>
    python -m tests.evals.runner remaining <results file>

The dry run uses the scripted model on bi_eval. Nothing in this file calls a real
model by itself: `evaluation_llm` only builds the client, test_golden_eval.py is the
one caller, and every call is refused without the confirmation (gate.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel

from app.agents.domain import AgentReport
from app.agents.registry import AGENTS
from app.agents.registry import AGENTS as REGISTRY
from app.agents.sql_analyst import SqlDraft
from app.agents.summarizer import Summary
from app.agents.supervisor import AgentTask, Decision
from app.core.config import Settings
from app.llm.base import LLM, LLMInvalidResponseError, LLMUnavailableError, T
from app.llm.factory import build_llm
from app.llm.fake import Call, FakeLLM
from app.semantic.model import load_vocabulary
from app.sqlguard import (
    Catalog,
    Checked,
    Facts,
    Refused,
    check,
    check_aggregation,
    check_correctness,
)
from app.store import users
from app.store.connections import Stores
from app.workflows import assistant
from app.workflows.assistant import AssistantAnswer, Combined
from tests.evals import database
from tests.evals.compare import compare_rows, not_mentioned
from tests.evals.dataset import GENERAL, Case, Dataset, load_dataset

CAUSES = ("routing", "execution", "calculation", "interpretation")


@dataclass
class Outcome:
    case_id: str
    passed: bool
    cause: str | None = None  # one of CAUSES, or None when it passed
    details: dict[str, list[str]] = field(default_factory=dict)  # every problem, by cause
    route: str = ""
    agents: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)
    answer: AssistantAnswer | None = None  # what the application returned, kept for the record


def judge(case: Case, answer: AssistantAnswer, *, check_text: bool = True) -> Outcome:
    """Compare what the application answered with what the case expects. Pure."""
    found: dict[str, list[str]] = {cause: [] for cause in CAUSES}
    notes: list[str] = []
    agents = tuple(p.agent for p in answer.results if p.agent != GENERAL)

    # -- routing
    allowed = [(case.route.expect, set(case.route.agents))]
    allowed += [(a.route, set(a.agents)) for a in case.route.accept]
    chosen = (answer.route, set(agents))
    if chosen not in allowed:
        expected = case.route.expect + (f" {list(case.route.agents)}" if case.route.agents else "")
        got = answer.route + (f" {list(agents)}" if agents else "")
        found["routing"].append(f"expected {expected}, got {got}")
    elif chosen != allowed[0]:
        notes.append(f"an accepted alternative route was taken: {answer.route} {list(agents)}")

    # -- execution and calculation
    if case.compare == "refusal":
        if answer.status not in case.expect_status:
            found["execution"].append(
                f"status {answer.status}, expected one of {list(case.expect_status)}"
            )
        for part in answer.results:
            if part.data:
                found["execution"].append(f"{part.agent} returned {len(part.data)} rows")
    else:
        by_target = {p.agent: p for p in answer.results}
        for expected_part in case.parts:
            part = by_target.get(expected_part.agent)
            if part is None and len(case.parts) == 1 and len(answer.results) == 1:
                part = answer.results[0]  # an accepted alternative answered the one part
            if part is None:
                found["execution"].append(f"{expected_part.agent}: no result")
                continue
            wanted = "no_data" if case.compare == "empty" else "answered"
            if part.status != wanted:
                found["execution"].append(f"{part.agent}: status {part.status}, not {wanted}")
                continue
            compared = compare_rows(expected_part.expected, part.data)
            found["calculation"] += [f"{part.agent}: {p}" for p in compared.problems]
            notes += [f"{part.agent}: {n}" for n in compared.notes]

    # -- interpretation
    if check_text and not any(found.values()):
        text = " ".join([answer.summary] + [p.summary for p in answer.results])
        missing = not_mentioned(text, case.answer_must_contain)
        if missing:
            found["interpretation"].append(f"the answer does not state {missing}")
        if case.limited and case.compare != "refusal" and not answer.scope_note:
            found["interpretation"].append("the answer does not say it is limited to outlets")

    cause = next((c for c in CAUSES if found[c]), None)
    return Outcome(
        case_id=case.id,
        passed=cause is None,
        cause=cause,
        details={c: p for c, p in found.items() if p},
        route=answer.route,
        agents=agents,
        notes=notes,
        answer=answer,
    )


async def run_case(
    case: Case, *, llm: LLM, stores: Stores, settings: Settings, check_text: bool = True
) -> Outcome:
    caller = await users.find_caller_by_email(stores.postgres, case.user)
    if caller is None:
        return Outcome(case.id, False, "execution", {"execution": [f"no user {case.user}"]})
    answer = await assistant.ask(
        question=case.question, caller=caller, llm=llm, pg=stores.postgres, settings=settings
    )
    return judge(case, answer, check_text=check_text)


# -- a scripted model that answers as the case says a correct model would -----------------


def target_of(call: Call) -> str:
    for agent in AGENTS.values():
        if agent.prompt in call.system:
            return agent.name
    return GENERAL


def decision_for(case: Case) -> Decision:
    route = case.route.expect
    if route == "agents":
        tasks = [AgentTask(agent=name) for name in case.route.agents]
        return Decision(route="agents", agents=tasks, reason="areas")
    if route == "clarify":
        return Decision(
            route="clarify",
            clarification="Which area do you mean: sales, inventory or customers?",
            reason="unclear",
        )
    return Decision(route=route, reason="scripted")


def scripted_llm(case: Case, *, sql: dict[str, str] | None = None) -> FakeLLM:
    """The model's part, played from the case: its route, then its pipeline_sql.

    Nothing here decides a figure: the SQL still goes through the guard, the
    aggregation check, the read-only user and row security, as the case's user.
    `sql` replaces the query per target, to show what a wrong query does.
    """
    queries = sql or {part.agent: part.pipeline_sql for part in case.parts}

    def draft(call: Call) -> BaseModel:
        query = queries.get(target_of(call))
        if query is None:  # a question that must be declined
            return SqlDraft(sql=None, explanation="Personal details are not available.")
        return SqlDraft(sql=query, explanation="scripted")

    llm = FakeLLM().on("route", decision_for(case))
    for _ in range(max(1, len(queries))):
        llm.on("sql", draft).on("sql_repair", draft, draft)
        llm.on("agent_report", AgentReport(summary="Done."))
    llm.on("summary", Summary(text="Done."))
    llm.on("supervisor_report", Combined(text="Done."))
    return llm


# -- the report ----------------------------------------------------------------------------


def report(dataset: Dataset, outcomes: list[Outcome], *, model: str) -> str:
    passed = [o for o in outcomes if o.passed]
    failed = [o for o in outcomes if not o.passed]
    lines = [
        f"model: {model}",
        f"dataset {dataset.total} | active {len(dataset.active)} | pending {len(dataset.pending)}"
        f" | executed {len(outcomes)} | passed {len(passed)} | failed {len(failed)}",
    ]
    for cause in CAUSES:
        lines.append(f"  failed by {cause:<15} {sum(1 for o in failed if o.cause == cause)}")
    for outcome in outcomes:
        mark = "PASS" if outcome.passed else f"FAIL ({outcome.cause})"
        lines.append(f"{mark:<22} {outcome.case_id}  -> {outcome.route} {list(outcome.agents)}")
        for cause, problems in outcome.details.items():
            lines += [f"      {cause}: {p}" for p in problems[:6]]
        lines += [f"      note: {n}" for n in outcome.notes]
    return "\n".join(lines)


Scripted = Callable[[Case], FakeLLM]


# == instrumentation: one complete record per question =====================================

RESULTS_DIR = Path(__file__).parent / "results"
MAX_PROVIDER_FAILURES = 3
_LLM_LOGGER = "bi.llm"
INTERVAL_VARIABLE = "BI_EVAL_MIN_CALL_INTERVAL_SECONDS"
MAX_INTERVAL_SECONDS = 600.0  # above this it is a typing mistake, not a quota


EVALUATION_MAX_RETRIES = 0  # one evaluation call is one provider request, never more


class HiddenRetries(AssertionError):
    """The evaluation's model client would repeat a request by itself. Nothing was sent."""


def evaluation_llm(settings: Settings) -> LLM:
    """The real model client for an evaluation run: the application's, with retries off.

    The application's client repeats a request up to twice on a connection error or a
    rate limit. In an evaluation that would send requests the pacing never sees and the
    record never counts. So the client is built with no retries, and this is checked on
    the built object, main model and fast model alike, before it is handed over: every
    task (route, sql, sql_repair, agent_report, summary, supervisor_report) uses one of
    those two. A failed request then fails the call at once and reaches the stop rule.
    The application's own retry setting is not touched.
    """
    llm = build_llm(settings, max_retries=EVALUATION_MAX_RETRIES)
    found = client_retries(llm)
    if found and set(found) != {EVALUATION_MAX_RETRIES}:
        raise HiddenRetries(f"the evaluation client has retries configured: {found}")
    return llm


def client_retries(llm: LLM) -> list[int]:
    """The retry count of every provider client inside `llm` (empty: it has none)."""
    models = getattr(llm, "_models", {})
    return [int(model.max_retries) for model in models.values()]


class PacingNotConfigured(ValueError):
    """The interval between real model calls is missing or is not a usable number."""


def interval_from_environment(environ: Mapping[str, str] | None = None) -> float:
    """Seconds between the starts of two real model calls, as the person running it chose.

    There is no default on purpose. The right value depends on the account's quota, which
    this code cannot know: for a limit of N calls a minute, 60 / N plus a margin. 0 is
    accepted and means "no pacing"; it has to be written out, never assumed.
    """
    raw = (os.environ if environ is None else environ).get(INTERVAL_VARIABLE, "").strip()
    how = (
        f"Set {INTERVAL_VARIABLE} to the seconds to keep between two model calls: "
        "60 divided by the calls a minute your account allows, plus a margin "
        "(for example 7 for a limit of 10 a minute). 0 means no pacing."
    )
    if not raw:
        raise PacingNotConfigured(f"{INTERVAL_VARIABLE} is not set. {how}")
    try:
        seconds = float(raw)
    except ValueError:
        raise PacingNotConfigured(f"{INTERVAL_VARIABLE} is not a number. {how}") from None
    if not 0 <= seconds <= MAX_INTERVAL_SECONDS:  # also false for nan
        raise PacingNotConfigured(
            f"{INTERVAL_VARIABLE} must be between 0 and {MAX_INTERVAL_SECONDS:g}. {how}"
        )
    return seconds


class Pacer:
    """Keeps at least `interval` seconds between the starts of two model calls.

    One pacer serves a whole run, so the spacing also holds from one question to the
    next and between agents that work at the same time (the lock makes them queue).
    The first call does not wait. It only ever waits: it never sends or repeats a call.
    `clock` and `sleep` are replaceable so that tests take no real time.
    """

    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not 0 <= interval <= MAX_INTERVAL_SECONDS:
            raise PacingNotConfigured(f"an interval of {interval} seconds is not usable")
        self.interval = interval
        self._clock, self._sleep = clock, sleep
        self._lock = asyncio.Lock()
        self._last_start: float | None = None
        self.calls = 0
        self.waits = 0
        self.waited = 0.0

    async def wait(self) -> float:
        """Wait until the next call may start. Returns the seconds actually waited."""
        async with self._lock:
            waited = 0.0
            if self.interval > 0 and self._last_start is not None:
                due = self._last_start + self.interval
                before = self._clock()
                if due > before:
                    await self._sleep(due - before)
                    waited = max(0.0, self._clock() - before)
                    self.waits += 1
                    self.waited += waited
            self._last_start = self._clock()
            self.calls += 1
            return waited

    def metadata(self) -> dict[str, Any]:
        return {
            "paced": True,
            "variable": INTERVAL_VARIABLE,
            "min_call_interval_seconds": self.interval,
            "calls_paced": self.calls,
            "waits": self.waits,
            "waited_seconds_total": round(self.waited, 3),
        }


NOT_PACED: dict[str, Any] = {
    "paced": False,
    "variable": INTERVAL_VARIABLE,
    "min_call_interval_seconds": None,
    "calls_paced": 0,
    "waits": 0,
    "waited_seconds_total": 0.0,
}


class Recording:
    """A model seen from outside: each call's task, duration and outcome. Never its text.

    It wraps whatever model the run uses (the real one, or the scripted one in a dry
    run) and passes every call through unchanged.
    """

    def __init__(self, inner: LLM, pacer: Pacer | None = None) -> None:
        self._inner = inner
        self._pacer = pacer
        self.calls: list[dict[str, Any]] = []

    async def structured(
        self, *, task: str, system: str, user: str, schema: type[T], fast: bool = False
    ) -> T:
        # the wait comes before the clock starts: `seconds` is the model's time alone
        waited = await self._pacer.wait() if self._pacer is not None else 0.0
        started = time.perf_counter()
        entry: dict[str, Any] = {
            "task": task,
            "fast": fast,
            "outcome": "ok",
            "waited_seconds": round(waited, 3),
        }
        self.calls.append(entry)
        try:
            return await self._inner.structured(
                task=task, system=system, user=user, schema=schema, fast=fast
            )
        except LLMUnavailableError as error:
            entry["outcome"] = f"unavailable: {type(error).__name__}: {str(error)[:60]}"
            raise
        except LLMInvalidResponseError:
            entry["outcome"] = "invalid answer"
            raise
        finally:
            entry["seconds"] = round(time.perf_counter() - started, 3)


class _TokenLog(logging.Handler):
    """Reads the application's own "model call" log lines: task, ms and tokens only."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() not in ("model call", "model call failed"):
            return
        self.lines.append(
            {
                "task": getattr(record, "task", None),
                "ms": getattr(record, "ms", None),
                "input_tokens": getattr(record, "input_tokens", None),
                "output_tokens": getattr(record, "output_tokens", None),
                "failed": getattr(record, "kind", None),  # the error class, never its text
                "http_status": getattr(record, "http_status", None),  # 429, 503…; a number
            }
        )


@contextmanager
def _listening() -> Iterator[_TokenLog]:
    logger = logging.getLogger(_LLM_LOGGER)
    handler, level = _TokenLog(), logger.level
    logger.addHandler(handler)
    if logger.getEffectiveLevel() > logging.INFO:
        logger.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


def _plain(value: Any) -> Any:
    """A value as JSON can hold it, without losing a figure."""
    if isinstance(value, Decimal):
        return (
            int(value)
            if value == value.to_integral_value() and value.as_tuple().exponent >= 0
            else float(value)
        )  # noqa: E501
    if isinstance(value, (datetime, date, UUID)):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    return value


def _attempts(settings: Settings, case: Case, since: datetime) -> list[dict[str, Any]]:
    """Every SQL attempt the application logged for this question: read-only, as the owner.

    The application writes app.query_log itself; nothing here changes how. Two cases ask
    the same words as different users, so the user and the start time select the rows.
    """
    with database.read_only(settings.pg_owner_dsn) as owner:
        rows = owner.execute(
            "SELECT q.sql, q.status, q.reason, q.row_count, q.duration_ms, q.created_at "
            "FROM app.query_log q JOIN app.users u ON u.id = q.user_id "
            "WHERE q.question = %s AND u.email = %s AND q.created_at >= %s "
            "ORDER BY q.created_at",
            (case.question, case.user, since),
        ).fetchall()
    return [_plain(dict(row)) for row in rows]


def _record(
    case: Case,
    outcome: Outcome | None,
    *,
    seconds: float,
    calls: list[dict[str, Any]],
    tokens: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    error: str | None,
) -> dict[str, Any]:
    answer = outcome.answer if outcome is not None else None
    waiting = sum(c.get("waited_seconds", 0.0) for c in calls)
    failed_calls = [c for c in calls if c["outcome"].startswith("unavailable")]
    provider_failure = bool(failed_calls) or any(t["failed"] for t in tokens)
    if outcome is None:
        verdict = {"passed": False, "cause": "execution", "details": {"execution": [error]}}
    else:
        verdict = {
            "passed": outcome.passed,
            "cause": outcome.cause,
            "details": outcome.details,
            "notes": outcome.notes,
        }
    return {
        "id": case.id,
        "group": case.group,
        "tags": list(case.tags),
        "question": case.question,
        "asked_by": case.user,
        "scope": {"company": case.scope.company, "outlets": _plain(case.scope.outlets)},
        "expected": {
            "route": case.route.expect,
            "agents": list(case.route.agents),
            "accepted_alternatives": [
                {"route": a.route, "agents": list(a.agents)} for a in case.route.accept
            ],
            "compare": case.compare,
            "statuses": list(case.expect_status),
            "answer_must_contain": list(case.answer_must_contain),
            "parts": [
                {
                    "agent": part.agent,
                    "order": part.expected.order,
                    "columns": [c.name for c in part.expected.columns],
                    "rows": _plain(list(part.expected.rows)),
                }
                for part in case.parts
            ],
        },
        "actual": None
        if answer is None
        else {
            "route": answer.route,
            "agents": [p.agent for p in answer.results if p.agent != GENERAL],
            "status": answer.status,
            "reason": answer.reason,
            "summary": answer.summary,
            "clarification": answer.clarification,
            "scope_note": answer.scope_note,
            "parts": [
                {
                    "agent": p.agent,
                    "question": p.question,
                    "status": p.status,
                    "summary": p.summary,
                    "insights": list(p.insights),
                    "sql": p.sql,
                    "columns": _plain(p.columns),
                    "rows": _plain(p.data),
                    "row_count": p.row_count,
                    "truncated": p.truncated,
                    "assumptions": list(p.assumptions),
                }
                for p in answer.results
            ],
        },
        "sql_attempts": attempts,
        "repair_attempts": max(
            0, len(attempts) - len({a["sql"] for a in attempts if a["status"] == "ok"})
        ),  # noqa: E501
        "refusal_codes": [a["reason"] for a in attempts if a["status"] == "rejected"],
        "model_calls": calls,
        "model_call_count": len(calls),
        "tokens": {
            "input": sum(t["input_tokens"] or 0 for t in tokens),
            "output": sum(t["output_tokens"] or 0 for t in tokens),
            "by_task": _by_task(tokens),
            "reported_calls": len([t for t in tokens if not t["failed"]]),
        },
        # why a provider call failed, as far as a class and a status code say it
        "provider_errors": [
            {"task": t["task"], "kind": t["failed"], "http_status": t.get("http_status")}
            for t in tokens
            if t["failed"]
        ],
        "seconds": round(seconds, 3),  # wall clock, pacing waits included
        "seconds_waiting": round(waiting, 3),
        "seconds_working": round(max(0.0, seconds - waiting), 3),
        "provider_failure": provider_failure,
        "error": error,
        "verdict": verdict,
    }


def _by_task(tokens: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for line in tokens:
        if line["failed"]:
            continue
        entry = out.setdefault(str(line["task"]), {"calls": 0, "input": 0, "output": 0, "ms": 0})
        entry["calls"] += 1
        entry["input"] += line["input_tokens"] or 0
        entry["output"] += line["output_tokens"] or 0
        entry["ms"] += line["ms"] or 0
    return out


class ResultsFile:
    """One run's results, rewritten whole after every question. Never half-written."""

    def __init__(
        self,
        *,
        model: str,
        provider: str,
        dataset: Dataset,
        only: Sequence[str],
        kind: str,
        client_max_retries: int | None = None,
    ) -> None:
        if kind not in (REAL, SCRIPTED):
            raise ValueError(f"a run is {REAL!r} or {SCRIPTED!r}, not {kind!r}")
        RESULTS_DIR.mkdir(exist_ok=True)
        # the folder ignores itself, so a results file can never be staged by accident
        (RESULTS_DIR / ".gitignore").write_text("*\n")
        started = datetime.now(timezone.utc)
        # a scripted run says so in its name as well as inside
        name = f"{started:%Y%m%dT%H%M%SZ}-" + ("SCRIPTED-" if kind == SCRIPTED else "")
        name += _slug(model)
        self.path = _claim(RESULTS_DIR / f"{name}.json")
        self.data: dict[str, Any] = {
            "run": {
                "kind": kind,  # "real-model", or "scripted": not a result of any model
                "pacing": dict(NOT_PACED),
                # the model client's own retries: 0 in a real run; None for a scripted model
                "client_max_retries": client_max_retries,
                "started": started.isoformat(),
                "finished": None,
                "model": model,
                "provider": provider,
                "database": database.EVAL_DATABASE,
                "reference_date": str(dataset.reference_date),
                "dataset_total": dataset.total,
                "dataset_active": len(dataset.active),
                "selected": list(only),
                "planned": [],  # the ids this run set out to ask, in order
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
                "stopped_early": None,
            },
            "questions": [],
            "not_executed": [],
            "summary": {},
        }
        self.save()  # the claimed file is a complete record from the first moment

    def save(self) -> None:
        self.data["summary"] = summarise(self.data)
        temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self.data, handle, indent=1, ensure_ascii=False, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)  # atomic: readers see the old file or the new one


REAL, SCRIPTED = "real-model", "scripted"


def _claim(path: Path) -> Path:
    """Create this file, or the same name with -2, -3…, and return the one created.

    Created with "x" (fails if it exists), so an earlier results file is never written
    over, also when two runs start in the same second.
    """
    candidate, number = path, 1
    while True:
        try:
            candidate.open("x").close()
            return candidate
        except FileExistsError:
            number += 1
            candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text).strip("-") or "model"


def provider_of(settings: Settings) -> str:
    """The provider's host name. Never the key, never the full address."""
    return urlsplit(settings.openai_base_url).hostname or "api.openai.com"


def summarise(data: dict[str, Any]) -> dict[str, Any]:
    questions = data["questions"]
    scored = [q for q in questions if not q["provider_failure"]]
    passed = [q for q in scored if q["verdict"]["passed"]]
    refusals = [q for q in scored if q["expected"]["compare"] == "refusal"]
    ran = [q for q in scored if q["expected"]["compare"] != "refusal"]
    seconds = [q["seconds"] for q in questions]
    routed = [
        q for q in scored if q["actual"] is not None and "routing" not in q["verdict"]["details"]
    ]

    def rate(part: int, whole: int) -> float | None:
        return round(100 * part / whole, 1) if whole else None

    return {
        "dataset": data["run"]["dataset_total"],
        "selected": len(data["run"]["planned"]) or len(questions) + len(data["not_executed"]),
        "executed": len(questions),
        "not_executed": len(data["not_executed"]),
        "provider_failures": len(questions) - len(scored),
        "scored": len(scored),
        "passed": len(passed),
        "failed": len(scored) - len(passed),
        "failed_by_cause": dict(
            Counter(q["verdict"]["cause"] for q in scored if not q["verdict"]["passed"])
        ),  # noqa: E501
        "by_status": dict(Counter((q["actual"] or {}).get("status", "no answer") for q in scored)),
        "exact_result_accuracy_percent": rate(len(passed), len(scored)),
        "routing_accuracy_percent": rate(len(routed), len(scored)),
        "sql_execution_success_percent": rate(
            sum(1 for q in ran if "execution" not in q["verdict"]["details"]), len(ran)
        ),
        "refusal_accuracy_percent": rate(
            sum(1 for q in refusals if q["verdict"]["passed"]), len(refusals)
        ),
        "questions_with_a_repair": sum(1 for q in questions if q["repair_attempts"] > 0),
        "refusal_codes": dict(Counter(c for q in questions for c in q["refusal_codes"])),
        "provider_errors": dict(
            Counter(
                f"{e['kind']} {e['http_status'] or 'no status'}"
                for q in questions
                for e in q.get("provider_errors", [])
            )
        ),
        "model_calls": sum(q["model_call_count"] for q in questions),
        "input_tokens": sum(q["tokens"]["input"] for q in questions),
        "output_tokens": sum(q["tokens"]["output"] for q in questions),
        "seconds_total": round(sum(seconds), 1),
        "seconds_waiting_total": round(sum(q.get("seconds_waiting", 0.0) for q in questions), 1),
        "seconds_average": round(sum(seconds) / len(seconds), 2) if seconds else None,
        "seconds_max": max(seconds) if seconds else None,
    }


async def run_all(
    cases: Sequence[Case],
    *,
    llm_for: Callable[[Case], LLM],
    stores: Stores,
    settings: Settings,
    results: ResultsFile,
    check_text: bool = True,
    max_provider_failures: int = MAX_PROVIDER_FAILURES,
    after_each: Callable[[int], None] | None = None,
    pacer: Pacer | None = None,
) -> dict[str, Any]:
    """Each case once, in order. A record is saved after every one. Nothing is retried.

    Three provider failures in a row stop the run: the questions that were not asked are
    listed under `not_executed`, not counted as wrong. With a `pacer`, every model call
    of the run waits its turn; without one (the scripted model) nothing waits.
    """
    in_a_row = 0
    results.data["run"]["planned"] = [case.id for case in cases]
    results.data["run"]["pacing"] = pacer.metadata() if pacer is not None else dict(NOT_PACED)
    results.save()
    for position, case in enumerate(cases):
        if in_a_row >= max_provider_failures:
            results.data["run"]["stopped_early"] = (
                f"{in_a_row} provider failures in a row; stopped before {case.id}"
            )
            results.data["not_executed"] = [c.id for c in cases[position:]]
            break
        model = Recording(llm_for(case), pacer)
        since = datetime.now(timezone.utc)
        started = time.perf_counter()
        outcome: Outcome | None = None
        error: str | None = None
        with _listening() as tokens:
            try:
                outcome = await run_case(
                    case, llm=model, stores=stores, settings=settings, check_text=check_text
                )
            except Exception as failure:  # an outage is a result to record, not a crash
                error = type(failure).__name__
        entry = _record(
            case,
            outcome,
            seconds=time.perf_counter() - started,
            calls=model.calls,
            tokens=tokens.lines,
            attempts=_attempts(settings, case, since),
            error=error,
        )
        in_a_row = in_a_row + 1 if entry["provider_failure"] else 0
        results.data["questions"].append(entry)
        if pacer is not None:
            results.data["run"]["pacing"] = pacer.metadata()
        results.save()
        if after_each is not None:
            after_each(position + 1)
    else:
        if in_a_row >= max_provider_failures:
            results.data["run"]["stopped_early"] = (
                f"{in_a_row} provider failures in a row, at the end"
            )
    results.data["run"]["finished"] = datetime.now(timezone.utc).isoformat()
    results.data["replay"] = replay(results.data, load_dataset())
    results.save()
    return results.data


def remaining(data: dict[str, Any]) -> list[str]:
    """The questions a later, separately approved run would still have to ask."""
    answered = {q["id"] for q in data["questions"] if not q["provider_failure"]}
    # also right after an interruption, when the run never got to list what it skipped
    return [case_id for case_id in data["run"]["planned"] if case_id not in answered]


# == offline: every recorded SQL through the three checks, and the traps ====================


def _locks(sql: str, target: str) -> dict[str, Any]:
    """What each check says about one query, each asked alone. No database, no model."""
    whole = load_vocabulary()
    vocabulary = whole if target not in REGISTRY else whole.restricted_to(REGISTRY[target].tables)
    catalog = Catalog(vocabulary.catalog())
    guarded = check(sql, catalog, max_rows=1001)
    if not isinstance(guarded, Checked):
        return {"guard": f"refused: {guarded.code}", "aggregation": None, "correctness": None}
    sound = check_aggregation(guarded.sql, catalog, vocabulary.unique_keys())
    reviewed = check_correctness(guarded.sql, catalog, Facts(**vocabulary.correctness_facts()))
    return {
        "guard": "passed",
        "aggregation": f"refused: {sound.code}"
        if isinstance(sound, Refused)
        else ("passed" + (" (uncertain)" if sound.uncertain else "")),
        "correctness": f"refused: {reviewed.code}"
        if isinstance(reviewed, Refused)
        else ("passed" + (" (uncertain)" if reviewed.uncertain else "")),
        "notes": [] if isinstance(reviewed, Refused) else list(reviewed.notes),
    }


def _same_sql(a: str, b: str) -> bool:
    return " ".join(a.lower().split()).rstrip(";") == " ".join(b.lower().split()).rstrip(";")


def replay(data: dict[str, Any], dataset: Dataset) -> dict[str, Any]:
    """Offline. (1) Every SQL the run produced, through the three checks. (2) Every trap of
    the dataset: its recorded verdicts confirmed, and whether the run shows its pattern.
    (3) The safe control."""
    by_id = {q["id"]: q for q in data["questions"]}
    generated = []
    for question in data["questions"]:
        target = (question["actual"] or {}).get("agents") or []
        parts = (question["actual"] or {}).get("parts") or []
        for attempt in question["sql_attempts"]:
            owner = next(
                (p["agent"] for p in parts if p["sql"] and _same_sql(p["sql"], attempt["sql"])),
                None,
            )  # noqa: E501
            if owner is None:
                owner = target[0] if len(target) == 1 else GENERAL
            generated.append(
                {
                    "id": question["id"],
                    "target": owner,
                    "logged": f"{attempt['status']}"
                    + (f": {attempt['reason']}" if attempt["reason"] else ""),  # noqa: E501
                    "sql": attempt["sql"],
                    **_locks(attempt["sql"], owner if len(target) <= 1 else GENERAL),
                }
            )

    traps = []
    for case in dataset.cases:
        question = by_id.get(case.id)
        for trap in case.traps:
            now = _locks(trap.sql, GENERAL)
            recorded = {
                "aggregation": trap.aggregation_check,
                "correctness": trap.correctness_check
                + (f": {trap.correctness_code}" if trap.correctness_code else ""),  # noqa: E501
            }
            confirmed = (
                now["guard"] == "passed"
                and now["aggregation"].startswith(trap.aggregation_check)
                and now["correctness"].startswith(recorded["correctness"])
            )
            seen: list[str] = []
            if question is not None:
                code = (
                    "unsafe_aggregation"
                    if trap.caught_by == "aggregation"
                    else trap.correctness_code
                )
                if any(_same_sql(a["sql"], trap.sql) for a in question["sql_attempts"]):
                    seen.append("the same SQL was written")
                if code and code in question["refusal_codes"]:
                    seen.append(f"an attempt was refused with {code}")
                figures = [
                    v
                    for p in (question["actual"] or {}).get("parts") or []
                    for row in p["rows"]
                    for v in row.values()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                ]
                wrong = not question["verdict"]["passed"]  # a right answer may hold the number
                same = any(abs(float(v) - trap.gives) < 0.005 for v in figures)
                if wrong and same and trap.gives != 0:
                    seen.append(f"the answer holds the trap's figure {trap.gives}")
            traps.append(
                {
                    "id": case.id,
                    "trap": trap.name,
                    "stopped_by": trap.caught_by,
                    "recorded": recorded,
                    "now": {"aggregation": now["aggregation"], "correctness": now["correctness"]},
                    "verdicts_unchanged": confirmed,
                    "question_executed": question is not None,
                    "seen_in_this_run": seen,
                }
            )

    control = next(c for c in dataset.cases if "safe_join_control" in c.tags)
    control_question = by_id.get(control.id)
    return {
        "generated_sql": generated,
        "generated_sql_count": len(generated),
        "traps": traps,
        "trap_count": len(traps),
        "traps_by_lock": dict(Counter(t["stopped_by"] for t in traps)),
        "trap_verdicts_all_unchanged": all(t["verdicts_unchanged"] for t in traps),
        "traps_seen_in_this_run": [t for t in traps if t["seen_in_this_run"]],
        "safe_control": {
            "id": control.id,
            "reference_query": _locks(control.parts[0].pipeline_sql, GENERAL),
            "executed": control_question is not None,
            "verdict": control_question["verdict"] if control_question else None,
            "refusal_codes": control_question["refusal_codes"] if control_question else None,
        },
    }


def results_report(data: dict[str, Any]) -> str:
    summary, run = data["summary"], data["run"]
    pacing = run.get("pacing") or NOT_PACED  # a file from before pacing has no such entry
    lines = [
        ("SCRIPTED RUN, not a result of any model. " if run.get("kind") == SCRIPTED else "")
        + f"model: {run['model']} at {run['provider']} | database: {run['database']}",
        f"dataset {summary['dataset']} | selected {summary['selected']} | executed "
        f"{summary['executed']} | not executed {summary['not_executed']} | provider failures "
        f"{summary['provider_failures']} | passed {summary['passed']} | failed {summary['failed']}",
        f"model calls {summary['model_calls']} | tokens in {summary['input_tokens']} out "
        f"{summary['output_tokens']} | seconds total {summary['seconds_total']} "
        f"average {summary['seconds_average']} max {summary['seconds_max']}",
    ]
    if run.get("client_max_retries") is not None:
        lines.append(f"model client retries: {run['client_max_retries']}")
    lines.append(
        f"pacing: at least {pacing['min_call_interval_seconds']} s between model calls | "
        f"waited {pacing['waited_seconds_total']} s in {pacing['waits']} waits"
        if pacing["paced"]
        else "pacing: none"
    )
    if summary.get("provider_errors"):
        lines.append(f"provider errors: {summary['provider_errors']}")
    if run["stopped_early"]:
        lines.append(f"STOPPED EARLY: {run['stopped_early']}")
    for cause in CAUSES:
        lines.append(f"  failed by {cause:<15} {summary['failed_by_cause'].get(cause, 0)}")
    for question in data["questions"]:
        verdict = question["verdict"]
        mark = (
            "PROVIDER FAILURE"
            if question["provider_failure"]
            else "PASS"
            if verdict["passed"]
            else f"FAIL ({verdict['cause']})"
        )
        actual = question["actual"] or {}
        lines.append(
            f"{mark:<22} {question['id']}  -> {actual.get('route', '-')} {actual.get('agents', [])}"
            f"  [{question['model_call_count']} calls, {question['seconds']} s"
            + (f", refused: {question['refusal_codes']}" if question["refusal_codes"] else "")
            + "]"
        )
        for cause, problems in verdict["details"].items():
            lines += [f"      {cause}: {p}" for p in problems[:6]]
    for case_id in data["not_executed"]:
        lines.append(f"{'NOT EXECUTED':<22} {case_id}")
    return "\n".join(lines)


# == a scripted dry run of the same code path, and the offline commands ======================


class _Outage:
    """A scripted model whose provider is down."""

    async def structured(self, **_: Any) -> Any:
        raise LLMUnavailableError("RateLimitError")


async def _dry_run(
    interrupt_after: int | None,
    provider_fails_from: int | None,
    min_call_interval: float | None = None,
) -> Path:
    from app.core.config import load_settings

    settings, _ = database.recreate(load_settings())  # bi_eval, key blanked: no real model
    assert settings.openai_api_key == "" and database.database_of(settings.pg_dsn) == "bi_eval"
    dataset = load_dataset()
    cases = list(dataset.active)

    def llm_for(case: Case) -> LLM:
        position = cases.index(case)
        if provider_fails_from is not None and position >= provider_fails_from:
            return _Outage()
        return scripted_llm(case)

    def after_each(done: int) -> None:
        if interrupt_after is not None and done >= interrupt_after:
            raise KeyboardInterrupt  # as if the run were killed here

    results = ResultsFile(
        model="pipeline_sql, no real model", provider="none", dataset=dataset, only=(),
        kind=SCRIPTED,
    )  # fmt: skip
    # the scripted model is not paced, unless the dry run is asked to show the pacing itself
    pacer = Pacer(min_call_interval) if min_call_interval is not None else None
    stores = await Stores.open(settings)
    try:
        await run_all(
            cases, llm_for=llm_for, stores=stores, settings=settings, results=results,
            check_text=False, after_each=after_each, pacer=pacer,
        )  # fmt: skip
    finally:
        await stores.close()
    return results.path


def main(arguments: list[str]) -> int:
    if not arguments or arguments[0] not in ("dry-run", "replay", "remaining"):
        print(__doc__)
        return 2
    command, rest = arguments[0], arguments[1:]
    if command == "dry-run":
        options = dict(zip(rest[::2], rest[1::2], strict=False))
        interrupt = options.get("--interrupt-after")
        outage = options.get("--provider-fails-from")
        interval = options.get("--min-call-interval")
        path: Path | None = None
        try:
            path = asyncio.run(
                _dry_run(
                    int(interrupt) if interrupt else None,
                    int(outage) if outage else None,
                    float(interval) if interval else None,
                )
            )
        except KeyboardInterrupt:
            path = max(RESULTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
            print("interrupted, as asked")
        data = json.loads(path.read_text())
        print("\n".join(results_report(data).split("\n")[:4]))
        print(f"results: {path}")
        return 0
    data = json.loads(Path(rest[0]).read_text())
    if command == "remaining":
        print(",".join(remaining(data)))
        return 0
    found = replay(data, load_dataset())
    print(json.dumps({k: v for k, v in found.items() if k != "generated_sql"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
