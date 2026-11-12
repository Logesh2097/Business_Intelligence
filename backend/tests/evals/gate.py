"""The one switch that lets a test reach a real model, and the guard behind it.

Every test in the project uses a scripted model (CLAUDE.md rule 9). The only exception
is the golden set's real-model run, which costs model calls and so must never start by
accident. It is allowed by exactly one thing: the environment variable below set to
"1", which only `make eval CONFIRM_REAL_MODEL=1` does.

`install()` is called once per test run (tests/conftest.py). It replaces the one line
of the application that reaches the provider (`OpenAILLM._invoke`) with a version that
refuses unless the switch is on. So `make ci`, `make test`, `make test-integration`,
`make eval-check` and a bare `pytest` cannot reach a model, whatever key is configured
and whatever else is exported in the shell. Tests that stub `_invoke` on their own
client are not affected.
"""

from __future__ import annotations

import os
from typing import Any

VARIABLE = "BI_EVAL_CONFIRM_REAL_MODEL"


class RealModelCallBlocked(AssertionError):
    """A test tried to call a real model without the confirmation. Nothing was sent."""


def confirmed() -> bool:
    return os.environ.get(VARIABLE) == "1"


def install() -> None:
    from app.llm.openai import OpenAILLM

    if getattr(OpenAILLM._invoke, "guarded", False):
        return
    real = OpenAILLM._invoke

    async def _invoke(self: Any, model: Any, schema: Any, messages: Any) -> Any:
        if not confirmed():
            raise RealModelCallBlocked(
                "a test tried to call the real model. Nothing was sent. The only way to run "
                "against a real model is `make eval CONFIRM_REAL_MODEL=1`."
            )
        return await real(self, model, schema, messages)

    _invoke.guarded = True  # type: ignore[attr-defined]
    OpenAILLM._invoke = _invoke  # type: ignore[method-assign]

    # the same for embeddings: the one line that sends text to the provider
    from app.llm.embeddings import OpenAIEmbedder

    real_embed = OpenAIEmbedder._embed

    async def _embed(self: Any, texts: Any) -> Any:
        if not confirmed():
            raise RealModelCallBlocked(
                "a test tried to call the real embedding model. Nothing was sent."
            )
        return await real_embed(self, texts)

    OpenAIEmbedder._embed = _embed  # type: ignore[method-assign]
