"""Turning text into vectors, for document search (docs/design/rag.md §2 and §3).

Like the chat model, the provider is reached from this package only, through one line
(`OpenAIEmbedder._embed`) that tests replace or block. `BI_OPENAI_BASE_URL` applies
here too: an OpenAI-compatible service must offer `/embeddings` for the model named
in `BI_EMBEDDING_MODEL`.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from typing import Any, Protocol

import openai
from langchain_openai import OpenAIEmbeddings

from app.core.config import Settings
from app.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.llm.openai import MAX_RETRIES, REQUEST_TIMEOUT_SECONDS

log = logging.getLogger("bi.llm")

BATCH = 64  # texts per provider request


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, in order. All vectors have the same length."""
        ...


class UnconfiguredEmbedder:
    """Stands in when no key or embedding model is set: every call says so."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise LLMUnavailableError("not configured")


class OpenAIEmbedder:
    def __init__(self, settings: Settings, *, max_retries: int = MAX_RETRIES) -> None:
        self.model = settings.embedding_model
        options: dict[str, Any] = {
            "model": settings.embedding_model,
            "api_key": settings.openai_api_key,
            "base_url": settings.openai_base_url or None,
            "timeout": REQUEST_TIMEOUT_SECONDS,
            "max_retries": max_retries,
            # send the text as it is: no local token counting, which is OpenAI-specific
            "check_embedding_ctx_length": False,
        }
        self._client = OpenAIEmbeddings(**options)

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """The one line that reaches the provider (replaced or blocked in tests)."""
        return await self._client.aembed_documents(texts)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        started = time.perf_counter()
        for start in range(0, len(texts), BATCH):
            batch = texts[start : start + BATCH]
            try:
                got = await self._embed(batch)
            except openai.APIError as exc:
                status = getattr(exc, "status_code", None)
                log.warning(
                    "embedding call failed",
                    extra={
                        "kind": type(exc).__name__,
                        "http_status": status if isinstance(status, int) else None,
                    },
                )
                raise LLMUnavailableError(type(exc).__name__) from exc
            if len(got) != len(batch) or any(not v for v in got):
                raise LLMInvalidResponseError("embedding")
            vectors.extend([float(x) for x in v] for v in got)
        if len({len(v) for v in vectors}) > 1:
            raise LLMInvalidResponseError("embedding")
        log.info(
            "embedding call",
            extra={"texts": len(texts), "ms": round((time.perf_counter() - started) * 1000)},
        )
        return vectors


# words that say nothing about the subject; the fake ignores them, as meaning-based
# models effectively do
_COMMON_WORDS = frozenset(
    "the and for are was were has have had not but can may must will with from that this "
    "these those what which who whom whose when where why how does did our your their its "
    "his her any all each every many much more most some such than then there here into "
    "over under about after before between during within without only also very just you "
    "they them she him one two been being would could should per out off own same other "
    "describe tell give show list".split()
)


class FakeEmbedder:
    """For tests: a vector made from the words of the text, with no model.

    Texts that share words get similar vectors, so retrieval can be tested for real.
    It knows nothing about meaning: "refund" and "money back" are unrelated to it.
    """

    def __init__(self, size: int = 4096) -> None:
        self.size = size
        self.calls: list[list[str]] = []

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.size
        for word in set(re.findall(r"[a-z0-9]+", text.lower())):
            if len(word) < 3 or word in _COMMON_WORDS:
                continue
            # a crude stem, so that "refunds" meets "refund" and "deliveries" "delivery"
            stem = re.sub(r"(ies|y)$", "i", word)
            stem = re.sub(r"(ed|es|s)$", "", stem) if len(stem) > 4 else stem
            digest = hashlib.sha256(stem.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % self.size] = 1.0  # present or not
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]


def build_embedder(settings: Settings) -> Embedder:
    if not settings.openai_api_key or not settings.embedding_model:
        return UnconfiguredEmbedder()
    return OpenAIEmbedder(settings)
