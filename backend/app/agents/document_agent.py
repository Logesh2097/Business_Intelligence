"""The document agent: answers from the company's uploaded documents, with sources.

    question -> the search_documents tool (the caller's company only; in process or
                over MCP, by BI_TOOLS_TRANSPORT) -> numbered passages
             -> one model call -> the answer, checked by code -> the cited sources

The model sees passages and nothing else: no tools, no database, no caller. What it
writes is checked before it is shown: every source number must exist, an answer with
no valid source is not an answer, and every figure in it must be in a cited passage.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.agents.prompting import block, load_prompt
from app.agents.summarizer import numbers_in
from app.llm.base import LLM, LLMError
from app.rag.retrieve import Passage
from app.tools.client import search_documents
from app.tools.context import ToolContext

log = logging.getLogger("bi.agents")

NAME = "document_agent"
TITLE = "Documents"
NOT_FOUND = "I could not find this in your documents."
NOT_VERIFIED = (
    "I found passages that may be related, but I could not confirm an answer from them. "
    "The passages are listed as sources."
)
WRITE_UP_FAILED = (
    "I found passages that may be related, but could not write an answer right now. "
    "The passages are listed as sources."
)
MAX_QUOTE_CHARS = 400
MAX_PASSAGE_CHARS = 4000
_CITATION = re.compile(r"\[(\d{1,2})\]")


class DocumentDraft(BaseModel):
    """What the model must return."""

    found: bool = Field(description="True if the passages answer the question")
    answer: str = Field(default="", description="The answer with [n] source numbers")


@dataclass(frozen=True)
class Source:
    number: int  # the [n] in the answer
    document_id: str
    filename: str
    title: str
    page: int | None
    quote: str  # the start of the passage, as stored
    score: float


@dataclass
class DocumentAnswer:
    question: str
    status: str  # answered | no_data | failed
    summary: str
    sources: list[Source] = field(default_factory=list)


def passages_block(passages: list[Passage]) -> str:
    lines = []
    for number, passage in enumerate(passages, start=1):
        where = passage.filename + (f", page {passage.page}" if passage.page else "")
        # a passage cannot close the block it sits in
        text = passage.text[:MAX_PASSAGE_CHARS].replace("DOCUMENTS>>>", "DOCUMENTS> > >")
        lines.append(f"[{number}] {where}\n{text}")
    return "\n\n".join(lines)


def _source(number: int, passage: Passage) -> Source:
    quote = " ".join(passage.text.split())
    if len(quote) > MAX_QUOTE_CHARS:
        quote = quote[:MAX_QUOTE_CHARS].rsplit(" ", 1)[0] + "…"
    return Source(
        number=number,
        document_id=passage.document_id,
        filename=passage.filename,
        title=passage.title,
        page=passage.page,
        quote=quote,
        score=passage.score,
    )


def check(draft: DocumentDraft, passages: list[Passage], question: str) -> tuple[str, list[int]]:
    """The answer as it may be shown, and the source numbers it really cites.

    Returns ("", []) when there is nothing that may be shown as an answer.
    """
    text = " ".join(draft.answer.split())
    if not draft.found or not text:
        return "", []
    cited: list[int] = []

    def keep(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if 1 <= number <= len(passages):
            if number not in cited:
                cited.append(number)
            return match.group(0)
        return ""  # a source that does not exist is removed

    text = re.sub(r"\s+([.,;:])", r"\1", _CITATION.sub(keep, text)).strip()
    if not cited:
        return "", []  # nothing to stand on: not shown as an answer
    # every figure must be written in a cited passage or in the question itself
    allowed = " ".join(passages[n - 1].text for n in cited) + " " + question
    known = set(numbers_in(allowed))
    stated = numbers_in(_CITATION.sub(" ", text))
    if any(number not in known for number in stated):
        return "", cited
    return text, sorted(cited)


async def answer(question: str, *, ctx: ToolContext, llm: LLM) -> DocumentAnswer:
    """May raise `AppError` (search down, no permission). A model outage is not raised."""
    passages = await search_documents(ctx, question)
    if not passages:
        # nothing was found: the model is not asked, so it cannot invent an answer
        return DocumentAnswer(question, "no_data", NOT_FOUND)

    user = block("QUESTION", question) + "\n\n" + block("DOCUMENTS", passages_block(passages))
    everything = [_source(n, p) for n, p in enumerate(passages, start=1)]
    try:
        draft = await llm.structured(
            task="document_answer",
            system=load_prompt(NAME),
            user=user,
            schema=DocumentDraft,
        )
    except LLMError as exc:
        log.warning("document answer failed", extra={"kind": type(exc).__name__})
        return DocumentAnswer(question, "failed", WRITE_UP_FAILED, everything)

    text, cited = check(draft, passages, question)
    if text:
        return DocumentAnswer(question, "answered", text, [everything[n - 1] for n in cited])
    if draft.found and draft.answer.strip() and cited:
        # it cited real passages but stated a figure they do not contain
        log.warning("document answer had figures that are not in its sources")
        return DocumentAnswer(question, "no_data", NOT_VERIFIED, [everything[n - 1] for n in cited])
    return DocumentAnswer(question, "no_data", NOT_FOUND)
