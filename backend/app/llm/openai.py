"""The real model client: OpenAI through LangChain's `ChatOpenAI`.

`BI_OPENAI_BASE_URL` points the same client at another service that speaks OpenAI's
API (for example Google Gemini's compatible endpoint). Empty means OpenAI itself.
Error handling is unit-tested with the provider's call replaced.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import openai
from langchain_core.exceptions import OutputParserException
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from app.core.config import Settings
from app.llm.base import LLMInvalidResponseError, LLMUnavailableError, T

log = logging.getLogger("bi.llm")

REQUEST_TIMEOUT_SECONDS = 45
# The client's own retries on connection errors and rate limits. This is the ONLY place
# a retry is configured: it is handed to ChatOpenAI, which hands it to the provider's
# SDK. The application always uses this value. The golden-set evaluation passes 0
# (tests/evals/runner.py) so that one call is one request and its pacing is exact.
MAX_RETRIES = 2


class OpenAILLM:
    def __init__(self, settings: Settings, *, max_retries: int = MAX_RETRIES) -> None:
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        self.max_retries = max_retries  # the same for every task, main model and fast
        self._models = {
            False: self._client(settings, settings.llm_model, max_retries),
            True: self._client(
                settings, settings.llm_model_fast or settings.llm_model, max_retries
            ),
        }

    @staticmethod
    def _client(settings: Settings, model: str, max_retries: int = MAX_RETRIES) -> ChatOpenAI:
        return ChatOpenAI(
            model=model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,  # None: OpenAI's own address
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=max_retries,
        )

    async def _invoke(self, model: ChatOpenAI, schema: type[T], messages: list[Any]) -> Any:
        """The one line that reaches the provider (replaced in unit tests)."""
        return await model.with_structured_output(schema, include_raw=True).ainvoke(messages)

    async def structured(
        self, *, task: str, system: str, user: str, schema: type[T], fast: bool = False
    ) -> T:
        started = time.perf_counter()
        try:
            result = await self._invoke(
                self._models[fast], schema, [("system", system), ("human", user)]
            )
        except (OutputParserException, ValidationError) as exc:
            log.warning("model answer did not fit", extra={"task": task})
            raise LLMInvalidResponseError(task) from exc
        except openai.APIError as exc:
            # connection, timeout, rate limit, bad key, bad request: the class only, never the text
            # and the HTTP status when the provider answered (429, 503…): a number, no text
            status = getattr(exc, "status_code", None)
            log.warning(
                "model call failed",
                extra={
                    "task": task,
                    "kind": type(exc).__name__,
                    "http_status": status if isinstance(status, int) else None,
                },
            )
            raise LLMUnavailableError(type(exc).__name__) from exc

        parsed = result.get("parsed") if isinstance(result, dict) else None
        if not isinstance(parsed, schema):
            log.warning("model answer did not fit", extra={"task": task})
            raise LLMInvalidResponseError(task)

        usage = getattr(result.get("raw"), "usage_metadata", None) or {}
        log.info(
            "model call",
            extra={
                "task": task,
                "ms": round((time.perf_counter() - started) * 1000),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            },
        )
        return parsed
