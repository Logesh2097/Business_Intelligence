"""REAL DRIVERS, NO PROVIDER: an evaluation call is one provider request, never more.

The real client (LangChain's ChatOpenAI and the provider's SDK under it) is pointed at
a small HTTP server started by this file on 127.0.0.1. The server answers every request
with a rate-limit error (429) and counts the requests. So the count is what the client
really sent, hidden retries included. No request leaves this machine and no key is real.

The application's client repeats a failed request twice; the evaluation's must not.
Both are measured here, so a change to either is seen.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.llm import LLMUnavailableError, build_llm
from app.llm.factory import UnconfiguredLLM
from app.llm.openai import MAX_RETRIES, OpenAILLM
from tests.evals import runner, test_golden_eval
from tests.evals.dataset import Case, load_dataset
from tests.evals.runner import (
    EVALUATION_MAX_RETRIES,
    REAL,
    HiddenRetries,
    Outcome,
    Pacer,
    Recording,
    ResultsFile,
    client_retries,
    evaluation_llm,
    remaining,
    results_report,
    run_all,
)

DATASET = load_dataset()
TASKS = ("route", "sql", "sql_repair", "agent_report", "summary", "supervisor_report")


class Answer(BaseModel):
    value: int = 1


class Provider:
    """A stand-in provider on 127.0.0.1: counts requests, always says "rate limit"."""

    def __init__(self) -> None:
        self.requests = 0
        counter = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the name http.server requires
                self.rfile.read(int(self.headers.get("content-length") or 0))
                counter.requests += 1
                body = json.dumps({"error": {"message": "quota", "code": 429}}).encode()
                self.send_response(429)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.send_header("retry-after-ms", "1")  # a retrying client retries at once
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/"
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def provider() -> Iterator[Provider]:
    stand_in = Provider()
    yield stand_in
    stand_in.close()


def settings(url: str = "http://127.0.0.1:9/v1/", **more: Any) -> Settings:
    base: dict[str, Any] = {
        "openai_api_key": "sk-test-not-real",
        "llm_model": "main-model",
        "llm_model_fast": "fast-model",
        "openai_base_url": url,
    }
    return Settings(_env_file=None, env="test", **{**base, **more})


def sending_for_real(llm: Any) -> Any:
    """Let this client send, to the stand-in only.

    Under tests the line that reaches the provider is replaced by a guard (gate.py).
    This puts the same line back on this one object, after checking where it points.
    """
    for model in llm._models.values():
        assert str(model.openai_api_base).startswith("http://127.0.0.1:")

    async def invoke(model: Any, schema: Any, messages: Any) -> Any:
        return await model.with_structured_output(schema, include_raw=True).ainvoke(messages)

    llm._invoke = invoke
    return llm


async def ask(llm: Any, task: str = "route", fast: bool = False) -> Any:
    return await llm.structured(task=task, system="s", user="u", schema=Answer, fast=fast)


# -- how each client is configured ------------------------------------------------------------


def test_the_evaluation_client_has_no_retries_on_either_model() -> None:
    llm = evaluation_llm(settings())
    assert isinstance(llm, OpenAILLM)
    assert EVALUATION_MAX_RETRIES == 0 and llm.max_retries == 0
    assert client_retries(llm) == [0, 0]  # the main model and the fast one
    for model in llm._models.values():
        assert model.max_retries == 0
        # and the provider's own SDK client under it, where the retry would happen
        assert model.root_async_client.max_retries == 0
        assert model.root_client.max_retries == 0


def test_the_application_client_keeps_its_retries() -> None:
    assert MAX_RETRIES == 2
    for llm in (build_llm(settings()), OpenAILLM(settings())):
        assert isinstance(llm, OpenAILLM)
        assert llm.max_retries == 2 and client_retries(llm) == [2, 2]
        for model in llm._models.values():
            assert model.root_async_client.max_retries == 2


def test_the_application_builds_its_client_without_naming_a_retry_count() -> None:
    main = (Path(__file__).resolve().parents[2] / "app" / "main.py").read_text()
    assert "build_llm(settings)" in main and "max_retries" not in main
    # nothing in the application asks for the evaluation's value
    app = Path(__file__).resolve().parents[2] / "app"
    for file in app.rglob("*.py"):
        # embeddings.py takes the application's own MAX_RETRIES as its default
        if file.name not in ("openai.py", "factory.py", "embeddings.py"):
            assert "max_retries" not in file.read_text(), file


def test_the_real_model_test_builds_its_client_through_the_evaluation_builder() -> None:
    source = Path(test_golden_eval.__file__).read_text()
    assert "evaluation_llm(settings)" in source
    assert "build_llm" not in source and "OpenAILLM" not in source
    assert "client_max_retries=EVALUATION_MAX_RETRIES" in source


def test_a_client_that_would_retry_is_refused_as_the_evaluation_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # as if someone later made the builder ignore the option
    monkeypatch.setattr(runner, "build_llm", lambda s, **_: OpenAILLM(s))
    with pytest.raises(HiddenRetries, match="retries configured"):
        evaluation_llm(settings())


def test_without_a_key_there_is_no_client_and_nothing_to_retry() -> None:
    llm = evaluation_llm(settings(openai_api_key=""))
    assert isinstance(llm, UnconfiguredLLM) and client_retries(llm) == []


def test_a_negative_retry_count_is_refused() -> None:
    with pytest.raises(ValueError):
        OpenAILLM(settings(), max_retries=-1)


# -- what each client really sends ------------------------------------------------------------


async def test_a_rate_limited_evaluation_call_sends_exactly_one_request(
    provider: Provider,
) -> None:
    llm = sending_for_real(evaluation_llm(settings(provider.url)))
    with pytest.raises(LLMUnavailableError, match="RateLimitError"):
        await ask(llm)
    assert provider.requests == 1


async def test_every_task_and_both_models_send_one_request_each(provider: Provider) -> None:
    llm = sending_for_real(evaluation_llm(settings(provider.url)))
    for fast in (False, True):
        for task in TASKS:
            with pytest.raises(LLMUnavailableError):
                await ask(llm, task, fast)
    assert provider.requests == 2 * len(TASKS)


async def test_the_application_client_does_repeat_the_same_call(provider: Provider) -> None:
    # the contrast: this is what an evaluation call did on 2026-11-12
    llm = sending_for_real(build_llm(settings(provider.url)))
    with pytest.raises(LLMUnavailableError, match="RateLimitError"):
        await ask(llm)
    assert provider.requests == 1 + MAX_RETRIES == 3


async def test_a_refused_connection_is_one_attempt_and_fails_at_once() -> None:
    # port 9 on this machine: nothing listens, the connection is refused
    llm = sending_for_real(evaluation_llm(settings("http://127.0.0.1:9/v1/")))
    with pytest.raises(LLMUnavailableError, match="ConnectionError"):
        await ask(llm)


# -- no request escapes the pacing, and the stop rule still works -----------------------------


class Time:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


async def test_requests_sent_equal_calls_paced(provider: Provider) -> None:
    at = Time()
    pacer = Pacer(6.0, clock=at.clock, sleep=at.sleep)
    recording = Recording(sending_for_real(evaluation_llm(settings(provider.url))), pacer)
    for task in TASKS:
        with pytest.raises(LLMUnavailableError):
            await ask(recording, task)
    # one request for each paced call: none was sent behind the pacer's back
    assert provider.requests == pacer.calls == len(recording.calls) == len(TASKS)
    assert pacer.waits == len(TASKS) - 1
    assert all(c["outcome"].startswith("unavailable") for c in recording.calls)


async def test_three_rate_limited_questions_stop_the_run_after_three_requests(
    provider: Provider, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def run_case(case: Case, *, llm: Any, **_: Any) -> Outcome:
        await ask(llm, "route")  # the first call of a question; it fails
        return Outcome(case.id, True, route="general")

    monkeypatch.setattr(runner, "run_case", run_case)
    monkeypatch.setattr(runner, "_attempts", lambda *a, **k: [])
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")

    llm = sending_for_real(evaluation_llm(settings(provider.url)))
    at = Time()
    pacer = Pacer(6.0, clock=at.clock, sleep=at.sleep)
    cases = list(DATASET.active)[:6]
    file = ResultsFile(
        model="unit test", provider="127.0.0.1", dataset=DATASET, only=(), kind=REAL,
        client_max_retries=EVALUATION_MAX_RETRIES,
    )  # fmt: skip
    data = await run_all(
        cases, llm_for=lambda case: llm, stores=None, settings=None,  # type: ignore[arg-type]
        results=file, pacer=pacer,
    )  # fmt: skip

    assert provider.requests == 3  # three questions, one request each, then the stop
    assert pacer.calls == 3
    assert [q["model_call_count"] for q in data["questions"]] == [1, 1, 1]
    assert all(
        q["provider_failure"] and q["error"] == "LLMUnavailableError" for q in data["questions"]
    )
    assert data["run"]["stopped_early"] == (
        f"3 provider failures in a row; stopped before {cases[3].id}"
    )
    assert data["not_executed"] == [c.id for c in cases[3:]]
    assert remaining(data) == [c.id for c in cases]
    assert data["run"]["client_max_retries"] == 0
    # the record now says what the provider answered: a class and a status number
    assert data["questions"][0]["provider_errors"] == [
        {"task": "route", "kind": "OpenAIRateLimitError", "http_status": 429}
    ]
    assert data["summary"]["provider_errors"] == {"OpenAIRateLimitError 429": 3}
    assert "provider errors: {'OpenAIRateLimitError 429': 3}" in results_report(data)
    assert "model client retries: 0" in results_report(data)
