"""MOCKED: documents from bytes to searchable chunks. No MongoDB, no Qdrant, no model.

The stores are in-memory stand-ins (rag_fakes.py) and the embedder is the word-based
fake, so nothing here says how well a real embedding model retrieves.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.llm.embeddings import FakeEmbedder, UnconfiguredEmbedder
from app.rag.chunk import MAX_WORDS, OVERLAP_WORDS, chunk
from app.rag.detect import RejectedFile, clean_filename, clean_title, detect
from app.rag.extract import ExtractionFailed, Page, extract
from app.rag.ingest import EMBEDDING_NOT_SET_UP, ingest
from app.rag.retrieve import MAX_PER_DOCUMENT, search, select
from app.store import documents
from app.store.files import FileStore
from app.store.qdrant import Hit
from tests.unit.rag_fakes import FakeMongo, FakeQdrant, docx, pdf, xlsx

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "99999999-9999-9999-9999-999999999999"

# -- what a file is ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "name", "kind"),
    [
        (pdf("Hello"), "notes.txt", "pdf"),  # the bytes decide, not the name
        (docx("Hello"), "report.pdf", "docx"),
        (b"Refunds are accepted within 14 days.", "policy.txt", "txt"),
        (b"\xef\xbb\xbf# Policy\n\nText.", "policy.md", "md"),
        ("Café opening hours".encode(), "no-extension", "txt"),
    ],
)
def test_the_kind_is_read_from_the_bytes(data: bytes, name: str, kind: str) -> None:
    assert detect(data, name) == kind


@pytest.mark.parametrize(
    ("data", "name", "words"),
    [
        (b"", "empty.txt", "empty"),
        (b"   \n ", "blank.txt", "no text"),
        (xlsx(), "sales.xlsx", "Spreadsheets"),
        (xlsx(), "renamed.pdf", "Spreadsheets"),
        (b"a,b,c\n1,2,3\n4,5,6\n7,8,9\n1,2,3\n4,5,6\n", "data.txt", "CSV"),
        (b"just one line", "data.csv", "CSV"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "image.png", "Only PDF"),
        (b"MZ\x90\x00\x03\x00\x00\x00", "program.txt", "Only PDF"),
        (b"PK\x03\x04 not really a zip", "broken.docx", "damaged"),
        ("text".encode("utf-16"), "utf16.txt", "Only PDF"),
    ],
)
def test_a_file_that_is_not_a_document_is_refused_with_a_reason(
    data: bytes, name: str, words: str
) -> None:
    with pytest.raises(RejectedFile) as refused:
        detect(data, name)
    assert words in refused.value.reason


def test_a_shown_name_holds_no_path_and_a_title_is_always_present() -> None:
    assert clean_filename("../../etc/passwd") == "passwd"
    assert clean_filename("C:\\Users\\me\\Refund policy.pdf") == "Refund policy.pdf"
    assert clean_filename("a\x00b\nc.txt") == "abc.txt"
    assert clean_filename("   ") == "document"
    assert clean_title(None, "refund_policy-2026.pdf") == "refund policy 2026"
    assert clean_title("  Our   refund\npolicy ", "x.pdf") == "Our refund policy"
    assert len(clean_title("t" * 900, "x")) == 200


# -- text out of a file -----------------------------------------------------------------


def test_a_pdf_keeps_its_page_numbers_and_skips_pages_with_no_text() -> None:
    pages = extract(pdf("First page.", "", "Third page."), "pdf")
    assert pages == [Page(1, "First page."), Page(3, "Third page.")]


def test_a_scanned_pdf_is_refused_and_says_why() -> None:
    with pytest.raises(ExtractionFailed, match="no readable text"):
        extract(pdf("", ""), "pdf")


def test_a_damaged_file_is_refused_not_crashed_on() -> None:
    with pytest.raises(ExtractionFailed, match="damaged"):
        extract(b"%PDF-1.4 nothing else", "pdf")
    with pytest.raises(ExtractionFailed, match="damaged"):
        extract(b"PK\x03\x04 nothing", "docx")


def test_a_word_file_gives_its_paragraphs_and_marks_headings() -> None:
    data = docx(("Heading1", "Refund policy"), "Refunds within 14 days.", "", ("Title", "End"))
    assert extract(data, "docx") == [
        Page(None, "# Refund policy\n\nRefunds within 14 days.\n\n# End")
    ]


def test_a_word_file_that_declares_entities_is_not_read() -> None:
    import io
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa">]><d>&a;</d>',
        )
    with pytest.raises(ExtractionFailed, match="cannot be read"):
        extract(out.getvalue(), "docx")


def test_text_is_taken_as_it_is() -> None:
    assert extract(b"# Hours\n\nOpen 8 to 23.", "md") == [Page(None, "# Hours\n\nOpen 8 to 23.")]


# -- chunks ------------------------------------------------------------------------------


def words(n: int, word: str = "w") -> str:
    return " ".join(f"{word}{i}" for i in range(n))


def test_a_short_document_is_one_chunk() -> None:
    assert [c.text for c in chunk([Page(None, "One.\n\nTwo.")])] == ["One.\n\nTwo."]


def test_a_long_document_is_cut_near_the_size_with_the_tail_carried_over() -> None:
    text = "\n\n".join(words(100, f"p{i}x") for i in range(10))  # 1,000 words
    chunks = chunk([Page(None, text)])
    assert len(chunks) >= 3 and [c.index for c in chunks] == list(range(len(chunks)))
    for piece in chunks:
        assert len(piece.text.split()) <= MAX_WORDS + OVERLAP_WORDS
    for before, after in zip(chunks, chunks[1:], strict=False):
        tail = before.text.split()[-OVERLAP_WORDS:]
        assert after.text.split()[:OVERLAP_WORDS] == tail  # the overlap
    # nothing is lost: every word of the document is in some chunk
    assert set(text.split()) == {w for c in chunks for w in c.text.split()}


def test_one_huge_paragraph_is_still_cut() -> None:
    chunks = chunk([Page(None, words(2000))])
    assert (
        len(chunks) >= 5 and max(len(c.text.split()) for c in chunks) <= MAX_WORDS + OVERLAP_WORDS
    )


def test_a_chunk_never_crosses_a_page_and_keeps_its_page_number() -> None:
    chunks = chunk([Page(1, words(500, "a")), Page(2, words(30, "b"))])
    assert {c.page for c in chunks} == {1, 2}
    for piece in chunks:
        kinds = {w[0] for w in piece.text.split()}
        assert kinds == ({"a"} if piece.page == 1 else {"b"})


def test_a_heading_starts_a_new_block() -> None:
    assert chunk([Page(None, "Intro line\n# Hours\nOpen late")])[0].text == (
        "Intro line\n\n# Hours Open late"
    )


# -- choosing passages -------------------------------------------------------------------


def hit(document: str, score: float, index: int = 0) -> Hit:
    return Hit(document, index, f"{document}.pdf", document, 1, f"text {document} {index}", score)


def test_passages_come_best_first_above_the_threshold_at_most_three_a_document() -> None:
    hits = [hit("a", 0.9, i) for i in range(5)] + [hit("b", 0.5), hit("c", 0.2), hit("d", 0.31)]
    chosen = select(hits, k=8, min_score=0.3)
    assert [p.document_id for p in chosen] == ["a"] * MAX_PER_DOCUMENT + ["b", "d"]
    assert all(p.score >= 0.3 for p in chosen)
    assert len(select(hits, k=2, min_score=0.3)) == 2


# -- the file store ----------------------------------------------------------------------


async def test_a_file_is_stored_under_company_and_document_and_never_by_its_name(
    tmp_path: Path,
) -> None:
    files = FileStore(str(tmp_path))
    document = str(uuid.uuid4())
    path = await files.save(TENANT_A, document, "pdf", b"data")
    assert path == f"{TENANT_A}/{document}/original.pdf"
    assert await files.read(TENANT_A, document, "pdf") == b"data"
    assert await files.read(TENANT_B, document, "pdf") is None  # another company's folder
    await files.delete(TENANT_A, document)
    assert await files.read(TENANT_A, document, "pdf") is None
    for bad in ("../../etc", "not-a-uuid", ""):
        with pytest.raises(ValueError):
            await files.save(TENANT_A, bad, "pdf", b"x")
        with pytest.raises(ValueError):
            await files.save(bad, document, "pdf", b"x")


# -- ingestion ---------------------------------------------------------------------------


async def record(mongo: FakeMongo, tenant: str = TENANT_A, kind: str = "txt", title: str = "T"):
    return await documents.create(
        mongo,  # type: ignore[arg-type]
        tenant_id=tenant, document_id=documents.new_id(), uploaded_by="u", filename=f"f.{kind}",
        title=title, kind=kind, size=1, sha256=str(uuid.uuid4()), storage_path="p",
    )  # fmt: skip


async def test_a_document_becomes_ready_with_its_chunks_in_the_vector_store() -> None:
    mongo, qdrant, embedder = FakeMongo(), FakeQdrant(), FakeEmbedder()
    made = await record(mongo, kind="pdf", title="Refund policy")
    status = await ingest(
        made, pdf("Refunds within 14 days.", "Store credit after that."),
        mongo=mongo, qdrant=qdrant, embedder=embedder,  # type: ignore[arg-type]
    )  # fmt: skip
    assert status == ("ready", None)
    stored = qdrant.of(TENANT_A)
    assert [(c.page, c.text) for c in stored] == [
        (1, "Refunds within 14 days."), (2, "Store credit after that."),
    ]  # fmt: skip
    assert all(c.document_id == made.id and c.title == "Refund policy" for c in stored)
    assert embedder.calls[0][0].startswith("Refund policy\n\n")  # the title goes in front
    saved = await documents.find(mongo, tenant_id=TENANT_A, document_id=made.id)  # type: ignore[arg-type]
    assert (saved.status, saved.chunk_count, saved.page_count) == ("ready", 2, 2)


async def test_ingesting_again_replaces_the_old_chunks() -> None:
    mongo, qdrant = FakeMongo(), FakeQdrant()
    made = await record(mongo)
    for text in (b"Old text about refunds.", b"New text about opening hours."):
        await ingest(made, text, mongo=mongo, qdrant=qdrant, embedder=FakeEmbedder())  # type: ignore[arg-type]
    assert [c.text for c in qdrant.of(TENANT_A)] == ["New text about opening hours."]


async def test_a_file_with_no_text_is_failed_with_the_reason_and_stores_nothing() -> None:
    mongo, qdrant = FakeMongo(), FakeQdrant()
    made = await record(mongo, kind="pdf")
    status, error = await ingest(
        made,
        pdf(""),
        mongo=mongo,
        qdrant=qdrant,
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
    )
    assert status == "failed" and "no readable text" in (error or "")
    assert qdrant.points == []
    saved = await documents.find(mongo, tenant_id=TENANT_A, document_id=made.id)  # type: ignore[arg-type]
    assert saved.status == "failed" and saved.error == error


async def test_with_no_embedding_model_or_no_vector_store_the_document_is_failed_not_lost() -> None:
    mongo = FakeMongo()
    made = await record(mongo)
    assert await ingest(
        made,
        b"Some text.",
        mongo=mongo,
        qdrant=FakeQdrant(),
        embedder=UnconfiguredEmbedder(),  # type: ignore[arg-type]
    ) == ("failed", EMBEDDING_NOT_SET_UP)
    down = FakeQdrant()
    down.down = True
    status, error = await ingest(
        made,
        b"Some text.",
        mongo=mongo,
        qdrant=down,
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
    )
    assert status == "failed" and "not available" in (error or "")


# -- retrieval and isolation -------------------------------------------------------------


async def test_a_search_returns_only_the_callers_own_companys_passages() -> None:
    mongo, qdrant, embedder = FakeMongo(), FakeQdrant(), FakeEmbedder()
    mine = await record(mongo, TENANT_A)
    theirs = await record(mongo, TENANT_B)
    await ingest(
        mine, b"Our refund window is 14 days.", mongo=mongo, qdrant=qdrant, embedder=embedder
    )  # type: ignore[arg-type]
    await ingest(
        theirs, b"Our refund window is 90 days.", mongo=mongo, qdrant=qdrant, embedder=embedder
    )  # type: ignore[arg-type]

    for tenant, days in ((TENANT_A, "14"), (TENANT_B, "90")):
        found = await search(
            "refund window",
            tenant_id=tenant,
            qdrant=qdrant,
            embedder=embedder,
            min_score=0.1,  # type: ignore[arg-type]
        )
        assert [p.text for p in found] == [f"Our refund window is {days} days."]
    assert (
        await search(
            "refund window",
            tenant_id=str(uuid.uuid4()),
            qdrant=qdrant,
            embedder=embedder,
            min_score=0.1,  # type: ignore[arg-type]
        )
        == []
    )
    assert (
        await search("  ", tenant_id=TENANT_A, qdrant=qdrant, embedder=embedder, min_score=0.1)
        == []
    )  # type: ignore[arg-type]


async def test_every_document_record_query_names_the_company() -> None:
    mongo = FakeMongo()
    mine = await record(mongo, TENANT_A)
    await record(mongo, TENANT_B)
    assert [d.id for d in await documents.list_all(mongo, tenant_id=TENANT_A)] == [mine.id]  # type: ignore[arg-type]
    assert await documents.find(mongo, tenant_id=TENANT_B, document_id=mine.id) is None  # type: ignore[arg-type]
    assert await documents.count(mongo, tenant_id=TENANT_A) == 1  # type: ignore[arg-type]
    assert not await documents.mark_deleted(mongo, tenant_id=TENANT_B, document_id=mine.id)  # type: ignore[arg-type]
    await documents.set_status(mongo, tenant_id=TENANT_B, document_id=mine.id, status="failed")  # type: ignore[arg-type]
    assert (
        await documents.find(mongo, tenant_id=TENANT_A, document_id=mine.id)
    ).status == "processing"  # type: ignore[arg-type,union-attr]
    assert await documents.mark_deleted(mongo, tenant_id=TENANT_A, document_id=mine.id)  # type: ignore[arg-type]
    assert await documents.list_all(mongo, tenant_id=TENANT_A) == []  # type: ignore[arg-type]
    queries = mongo.collection("documents").queries
    assert queries and all(q.get("tenant_id") in (TENANT_A, TENANT_B) for q in queries)
    with pytest.raises(ValueError):
        await documents.list_all(mongo, tenant_id="")  # type: ignore[arg-type]
