"""MOCKED: the scripted document-retrieval set (tests/evals/rag.yaml).

The documents of rag_docs/ are indexed for one company through the real pipeline
(extract, chunk, ingest, search, the document agent) with in-memory stores and the
word-based fake embedder. So this measures the pipeline, NOT a real embedding model:
see the note at the top of rag.yaml.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agents import document_agent
from app.agents.document_agent import NOT_FOUND, DocumentDraft
from app.core.config import Settings
from app.llm.embeddings import FakeEmbedder
from app.llm.fake import Call, FakeLLM
from app.rag.detect import detect
from app.rag.ingest import ingest
from app.rag.retrieve import MAX_K, MAX_PER_DOCUMENT
from app.store import documents
from app.tools.context import ToolContext
from app.tools.documents import search_documents
from tests.unit.analytics_fakes import TENANT, caller
from tests.unit.rag_fakes import FakeMongo, FakeQdrant

EVALS = Path(__file__).resolve().parents[1] / "evals"
SET: dict[str, Any] = yaml.safe_load((EVALS / "rag.yaml").read_text())
# The threshold here is for the word-based fake, whose scores are on another scale than a
# real model's (a short question against a 150-word passage shares few words). It is NOT
# evidence for the production default of 0.30, which only a real-model run can set.
SETTINGS = Settings(_env_file=None, env="test", rag_min_score=0.08)
OTHER = "99999999-9999-9999-9999-999999999999"


@pytest.fixture
async def ctx() -> ToolContext:
    mongo, qdrant, embedder = FakeMongo(), FakeQdrant(), FakeEmbedder()
    for tenant in (str(TENANT), OTHER):  # the same documents for a second company too
        for item in SET["documents"]:
            data = (EVALS / "rag_docs" / item["file"]).read_bytes()
            record = await documents.create(
                mongo,  # type: ignore[arg-type]
                tenant_id=tenant, document_id=documents.new_id(), uploaded_by="eval",
                filename=item["file"], title=item["title"], kind=detect(data, item["file"]),
                size=len(data), sha256=f"{tenant}-{item['file']}", storage_path="none",
            )  # fmt: skip
            assert await ingest(record, data, mongo=mongo, qdrant=qdrant, embedder=embedder) == (  # type: ignore[arg-type]
                "ready", None,
            )  # fmt: skip
    return ToolContext(caller=caller("viewer"), pg=None, settings=SETTINGS, qdrant=qdrant,  # type: ignore[arg-type]
                       embedder=embedder)  # fmt: skip


def test_the_set_has_the_designed_size() -> None:
    assert len(SET["documents"]) == 3
    assert len(SET["answerable"]) == 12 and len(SET["unanswerable"]) == 5
    for item in SET["answerable"]:
        text = (EVALS / "rag_docs" / item["document"]).read_text()
        assert item["phrase"] in " ".join(text.split()), item["question"]


async def test_the_right_passage_is_among_those_retrieved_for_every_question(
    ctx: ToolContext,
) -> None:
    missed = []
    for item in SET["answerable"]:
        found = await search_documents(ctx, item["question"])
        assert len(found) <= MAX_K
        for document in {p.document_id for p in found}:
            assert sum(1 for p in found if p.document_id == document) <= MAX_PER_DOCUMENT
        if not any(
            p.filename == item["document"] and item["phrase"] in " ".join(p.text.split())
            for p in found
        ):
            missed.append(item["question"])
    recall = 1 - len(missed) / len(SET["answerable"])
    assert recall >= 0.9, f"recall at 8 is {recall:.0%}; missed: {missed}"  # the design's target


async def test_the_best_passage_comes_from_the_right_document(ctx: ToolContext) -> None:
    wrong = []
    for item in SET["answerable"]:
        found = await search_documents(ctx, item["question"])
        if not found or found[0].filename != item["document"]:
            wrong.append(item["question"])
    assert len(wrong) <= 1, wrong


async def test_a_question_no_document_answers_is_refused_without_asking_the_model(
    ctx: ToolContext,
) -> None:
    for question in SET["unanswerable"]:
        llm = FakeLLM()  # unscripted: a model call would fail the test
        answer = await document_agent.answer(question, ctx=ctx, llm=llm)
        assert (answer.status, answer.summary, answer.sources) == ("no_data", NOT_FOUND, []), (
            question
        )


async def test_an_answer_cites_the_document_that_holds_it(ctx: ToolContext) -> None:
    """The model's part is scripted: it quotes the phrase from the passage that has it."""
    for item in SET["answerable"]:

        def quote(call: Call, item: dict[str, str] = item) -> DocumentDraft:
            blocks = call.user.split("<<<DOCUMENTS\n", 1)[1].split("\n\n[")
            for number, block in enumerate(blocks, start=1):
                if item["phrase"] in " ".join(block.split()):
                    return DocumentDraft(found=True, answer=f"{item['phrase']} [{number}].")
            return DocumentDraft(found=False)

        answer = await document_agent.answer(
            item["question"], ctx=ctx, llm=FakeLLM().on("document_answer", quote)
        )
        assert answer.status == "answered", item["question"]
        assert [s.filename for s in answer.sources] == [item["document"]]
        assert (
            item["phrase"] in " ".join(answer.sources[0].quote.split())
            or len(answer.sources[0].quote) >= 300
        )


async def test_the_other_company_holding_the_same_documents_is_never_searched(
    ctx: ToolContext,
) -> None:
    await search_documents(ctx, SET["answerable"][0]["question"])
    assert set(ctx.qdrant.searches) == {str(TENANT)}  # type: ignore[union-attr]
    stranger = replace(ctx, caller=replace(caller(), tenant_id=__import__("uuid").uuid4()))
    assert await search_documents(stranger, SET["answerable"][0]["question"]) == []
