"""Choose the model client from the settings."""

from __future__ import annotations

from app.core.config import Settings
from app.llm.base import LLM, LLMUnavailableError, T


class UnconfiguredLLM:
    """Stands in when no key or model name is set: every call says so."""

    async def structured(
        self, *, task: str, system: str, user: str, schema: type[T], fast: bool = False
    ) -> T:
        raise LLMUnavailableError("not configured")


def build_llm(settings: Settings, *, max_retries: int | None = None) -> LLM:
    """The model client for these settings.

    `max_retries` is left out by the application, which then gets the client's normal
    retries (`MAX_RETRIES` in openai.py). Only the golden-set evaluation passes 0.
    """
    if not settings.openai_api_key or not settings.llm_model:
        return UnconfiguredLLM()
    from app.llm.openai import MAX_RETRIES, OpenAILLM  # here so tests never load the provider

    return OpenAILLM(settings, max_retries=MAX_RETRIES if max_retries is None else max_retries)
