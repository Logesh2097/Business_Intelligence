"""MOCKED: the model clients. The provider is never called; `_invoke` is replaced."""

from __future__ import annotations

from typing import Any

import httpx
import openai
import pytest
from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel

from app.core.config import Settings
from app.llm import LLMInvalidResponseError, LLMUnavailableError, build_llm
from app.llm.factory import UnconfiguredLLM
from app.llm.fake import FakeLLM
from app.llm.openai import OpenAILLM


class Answer(BaseModel):
    value: int


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"openai_api_key": "sk-test-not-real", "llm_model": "strong-model"}
    return Settings(_env_file=None, env="test", **{**base, **overrides})


def client_returning(result: Any) -> OpenAILLM:
    llm = OpenAILLM(settings())

    async def invoke(model: Any, schema: Any, messages: Any) -> Any:
        invoke.seen = (model, schema, messages)  # type: ignore[attr-defined]
        if isinstance(result, BaseException):
            raise result
        return result

    llm._invoke = invoke  # type: ignore[method-assign]
    return llm


async def ask(llm: Any, **kw: Any) -> Answer:
    return await llm.structured(task="t", system="be exact", user="2+2", schema=Answer, **kw)


# -- the factory -----------------------------------------------------------------


@pytest.mark.parametrize("missing", [{"openai_api_key": ""}, {"llm_model": ""}])
async def test_without_a_key_or_a_model_name_every_call_says_not_configured(
    missing: dict[str, str],
) -> None:
    llm = build_llm(settings(**missing))
    assert isinstance(llm, UnconfiguredLLM)
    with pytest.raises(LLMUnavailableError, match="not configured"):
        await ask(llm)


def test_with_a_key_and_a_model_the_real_client_is_built_from_the_settings() -> None:
    llm = build_llm(settings(llm_model_fast="cheap-model"))
    assert isinstance(llm, OpenAILLM)
    assert llm._models[False].model_name == "strong-model"
    assert llm._models[True].model_name == "cheap-model"


def test_the_fast_model_falls_back_to_the_main_one() -> None:
    assert OpenAILLM(settings())._models[True].model_name == "strong-model"


# -- answers ---------------------------------------------------------------------


async def test_a_well_formed_answer_is_returned_as_the_object() -> None:
    llm = client_returning({"raw": None, "parsed": Answer(value=4), "parsing_error": None})
    assert await ask(llm) == Answer(value=4)
    _, schema, messages = llm._invoke.seen  # type: ignore[attr-defined]
    assert schema is Answer
    assert messages == [("system", "be exact"), ("human", "2+2")]


def test_the_client_talks_to_openai_unless_another_address_is_set() -> None:
    default = OpenAILLM(settings())
    assert default._models[False].openai_api_base is None

    other = OpenAILLM(settings(openai_base_url="https://models.example/v1/"))
    assert other._models[False].openai_api_base == "https://models.example/v1/"
    assert other._models[True].openai_api_base == "https://models.example/v1/"


async def test_fast_uses_the_fast_model() -> None:
    llm = OpenAILLM(settings(llm_model_fast="cheap-model"))
    used = []

    async def invoke(model: Any, schema: Any, messages: Any) -> Any:
        used.append(model.model_name)
        return {"parsed": Answer(value=1)}

    llm._invoke = invoke  # type: ignore[method-assign]
    await ask(llm)
    await ask(llm, fast=True)
    assert used == ["strong-model", "cheap-model"]


@pytest.mark.parametrize(
    "result",
    [
        {"raw": None, "parsed": None, "parsing_error": "could not parse"},
        {"raw": None, "parsed": {"value": 4}},  # a dictionary, not the object
        {},
        None,
        "free text",
        OutputParserException("not json"),
    ],
)
async def test_an_incomplete_or_malformed_answer_is_an_invalid_response(result: Any) -> None:
    with pytest.raises(LLMInvalidResponseError):
        await ask(client_returning(result))


def _api_error(kind: type[openai.APIError]) -> openai.APIError:
    request = httpx.Request("POST", "https://api.example/v1/chat")
    if issubclass(kind, openai.APIStatusError):
        response = httpx.Response(429, request=request)
        return kind("the key sk-secret-123 was refused", response=response, body=None)
    if kind is openai.APITimeoutError:
        return openai.APITimeoutError(request=request)
    return openai.APIConnectionError(request=request)


@pytest.mark.parametrize(
    "kind",
    [openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError,
     openai.AuthenticationError, openai.BadRequestError],
)  # fmt: skip
async def test_a_provider_failure_is_unavailable_and_carries_only_its_class(
    kind: type[openai.APIError],
) -> None:
    with pytest.raises(LLMUnavailableError) as raised:
        await ask(client_returning(_api_error(kind)))
    assert str(raised.value) == kind.__name__
    assert "sk-secret" not in str(raised.value)


# -- the fake itself ---------------------------------------------------------------


async def test_the_fake_answers_in_order_records_prompts_and_fails_when_unscripted() -> None:
    fake = FakeLLM().on("t", Answer(value=1), LLMUnavailableError("down"))
    assert await ask(fake) == Answer(value=1)
    with pytest.raises(LLMUnavailableError):
        await ask(fake)
    with pytest.raises(AssertionError, match="no answer scripted"):
        await ask(fake)
    assert [c.task for c in fake.calls] == ["t", "t", "t"]
    assert fake.calls[0].text == "be exact\n2+2"


async def test_a_failed_call_logs_the_class_and_the_status_number_and_no_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger="bi.llm")
    with pytest.raises(LLMUnavailableError):
        await ask(client_returning(_api_error(openai.RateLimitError)))
    with pytest.raises(LLMUnavailableError):
        await ask(client_returning(_api_error(openai.APIConnectionError)))
    limited, refused = [r for r in caplog.records if r.getMessage() == "model call failed"]
    assert (limited.kind, limited.http_status) == ("RateLimitError", 429)
    assert (refused.kind, refused.http_status) == ("APIConnectionError", None)  # no answer at all
    for record in (limited, refused):
        assert "sk-secret" not in str(record.__dict__)
