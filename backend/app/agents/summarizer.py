"""Turns a query result into a short explanation, and checks the explanation's numbers.

Numbers by code, words by the model: every figure in the text must be a value of the
result. A text with a figure that is not there is asked for once more, then replaced
by a plain sentence built from the result by code.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from app.agents.prompting import block, load_prompt
from app.core.context import Caller
from app.llm.base import LLM, LLMError
from app.store.analytics import QueryResult

log = logging.getLogger("bi.summarizer")

ROWS_SHOWN_TO_MODEL = 30
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
_DATES = re.compile(
    rf"\d{{4}}-\d{{2}}-\d{{2}}"  # 2026-10-31
    # 1 Oct, 1-31 Oct 2026, 31st October 2026
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?(?:\s*[-–]\s*\d{{1,2}})?\s+(?:{_MONTHS})[a-z]*\.?(?:\s+\d{{4}})?"
    rf"|\b(?:{_MONTHS})[a-z]*\.?\s+\d{{4}}\b"  # October 2026 (before "October 20")
    rf"|\b(?:{_MONTHS})[a-z]*\.?\s+\d{{1,2}}(?!\d)(?:,\s*\d{{4}})?"  # October 31, 2026
    rf"|\b(?:19|20)\d{{2}}\b",  # a year on its own
    re.IGNORECASE,
)


class Summary(BaseModel):
    text: str = Field(description="The explanation, one to three sentences")


def numbers_in(text: str) -> list[float]:
    """The figures a text states, ignoring dates."""
    without_dates = _DATES.sub(" ", text)
    return [float(m.group().replace(",", "")) for m in _NUMBER.finditer(without_dates)]


def allowed_numbers(question: str, result: QueryResult) -> list[float]:
    allowed: list[float] = [float(result.row_count)]
    allowed += numbers_in(question)  # "top 10" may be repeated back
    allowed += [float(n) for n in range(1, result.row_count + 1)]  # ranks: "the 3rd"
    for row in result.rows:
        for value in row.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            v = abs(float(value))
            allowed += [v, v * 100]  # a ratio 0.061 may be written as 6.1%
    return allowed


def unsupported_numbers(text: str, question: str, result: QueryResult) -> list[float]:
    """Figures in the text that are not values of the result (allowing for rounding)."""
    allowed = allowed_numbers(question, result)

    def supported(n: float) -> bool:
        return any(abs(n - a) <= max(0.005 * abs(a), 0.51) for a in allowed)

    return [n for n in numbers_in(text) if not supported(n)]


def plain_summary(result: QueryResult, caller: Caller) -> str:
    """A sentence built by code, for an empty result or a model answer that failed the check."""
    if result.row_count == 0:
        return "No data matched this question."
    types = {c["name"]: c["type"] for c in result.columns}

    def show(name: str, value: Any) -> str:
        label = name.replace("_", " ")
        if value is None:
            return f"{label}: none"
        if types.get(name) == "money" and isinstance(value, (int, float)):
            return f"{label}: {caller.currency} {value:,.2f}"
        if isinstance(value, float):
            return f"{label}: {value:,.2f}"
        return f"{label}: {value}"

    first = ", ".join(show(name, value) for name, value in result.rows[0].items())
    if result.row_count == 1:
        return f"Result: {first}."
    more = " Only the first rows are shown." if result.truncated else ""
    return f"Found {result.row_count} rows. The first is {first}.{more}"


def result_block(result: QueryResult, caller: Caller) -> str:
    """The query result as the model is shown it: the first rows, never all of them."""
    return block(
        "RESULT",
        json.dumps(
            {
                "currency": caller.currency,
                "columns": result.columns,
                "rows": result.rows[:ROWS_SHOWN_TO_MODEL],
                "row_count": result.row_count,
                "cut_short": result.truncated or result.row_count > ROWS_SHOWN_TO_MODEL,
            },
            ensure_ascii=False,
            default=str,
        ),
    )


async def summarise(*, question: str, result: QueryResult, caller: Caller, llm: LLM) -> str:
    note = f" {caller.scope_note}" if caller.scope_note else ""
    if result.row_count == 0:
        # nothing to explain: the model is not asked, so it cannot invent a figure
        return plain_summary(result, caller) + note

    user = block("QUESTION", question) + "\n\n" + result_block(result, caller)
    system = load_prompt("summary")
    extra = ""
    for _ in range(2):
        try:
            summary = await llm.structured(
                task="summary", system=system, user=user + extra, schema=Summary, fast=True
            )
        except LLMError as exc:
            # the data is already here; a failed explanation must not lose it
            log.warning("summary failed", extra={"kind": type(exc).__name__})
            break
        text = summary.text.strip()
        wrong = unsupported_numbers(text, question, result)
        if text and not wrong:
            return text + note
        log.warning("summary had unsupported numbers", extra={"count": len(wrong)})
        extra = (
            "\n\nYour previous answer used numbers that are not in RESULT. "
            "Use only values from RESULT."
        )
    return plain_summary(result, caller) + note
