"""Stand-ins for MongoDB and Qdrant, and sample files, for the document tests.

`FakeQdrant` keeps points in memory and ranks them by cosine similarity, and it
filters by company exactly where the real store does: inside, from its own argument.
"""

from __future__ import annotations

import io
import math
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.store.qdrant import ChunkPoint, Hit


class FakeQdrant:
    def __init__(self) -> None:
        self.points: list[tuple[str, ChunkPoint]] = []  # (tenant_id, chunk)
        self.searches: list[str] = []  # the tenant of every search
        self.down = False

    def _check(self) -> None:
        if self.down:
            from app.core.errors import UnavailableError

            raise UnavailableError("Document search is not available right now. Please try again.")

    async def replace_document(
        self, *, tenant_id: str, document_id: str, chunks: list[ChunkPoint]
    ) -> None:
        self._check()
        await self.delete_document(tenant_id=tenant_id, document_id=document_id)
        self.points += [(tenant_id, chunk) for chunk in chunks]

    async def delete_document(self, *, tenant_id: str, document_id: str) -> None:
        self._check()
        self.points = [
            (t, c) for t, c in self.points if not (t == tenant_id and c.document_id == document_id)
        ]

    async def search_chunks(
        self, *, tenant_id: str, vector: list[float], limit: int, min_score: float
    ) -> list[Hit]:
        self._check()
        self.searches.append(tenant_id)

        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b, strict=True))
            return dot / (
                (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1
            )

        scored = [
            Hit(c.document_id, c.chunk_index, c.filename, c.title, c.page, c.text,
                cosine(vector, c.vector))
            for t, c in self.points
            if t == tenant_id
        ]  # fmt: skip
        scored = [h for h in scored if h.score >= min_score]
        return sorted(scored, key=lambda h: h.score, reverse=True)[:limit]

    def of(self, tenant_id: str) -> list[ChunkPoint]:
        return [c for t, c in self.points if t == tenant_id]


def _matches(record: dict[str, Any], query: dict[str, Any]) -> bool:
    return all(record.get(key) == value for key, value in query.items())


class _Cursor:
    def __init__(self, found: list[dict[str, Any]]) -> None:
        self.found = found

    def sort(self, key: str, direction: int) -> _Cursor:
        self.found.sort(key=lambda r: r[key], reverse=direction < 0)
        return self

    async def to_list(self, length: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.found[:length]]


class _Result:
    def __init__(self, modified: int) -> None:
        self.modified_count = modified


class FakeCollection:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.queries: list[dict[str, Any]] = []  # every filter that was used

    async def insert_one(self, record: dict[str, Any]) -> None:
        self.records.append(dict(record))

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        self.queries.append(query)
        return next((dict(r) for r in self.records if _matches(r, query)), None)

    def find(self, query: dict[str, Any]) -> _Cursor:
        self.queries.append(query)
        return _Cursor([r for r in self.records if _matches(r, query)])

    async def count_documents(self, query: dict[str, Any]) -> int:
        self.queries.append(query)
        return sum(1 for r in self.records if _matches(r, query))

    async def update_one(self, query: dict[str, Any], change: dict[str, Any]) -> _Result:
        self.queries.append(query)
        for record in self.records:
            if _matches(record, query):
                record.update(change["$set"])
                return _Result(1)
        return _Result(0)


class FakeMongo:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def collection(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())

    @asynccontextmanager
    async def guard(self) -> AsyncIterator[None]:
        yield

    async def close(self) -> None:
        return


# -- sample files ------------------------------------------------------------------------


def docx(*paragraphs: str | tuple[str, str]) -> bytes:
    """A minimal Word file. A paragraph is text, or (style, text) for a heading."""
    body = ""
    for paragraph in paragraphs:
        style, text = paragraph if isinstance(paragraph, tuple) else ("", paragraph)
        properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body += f"<w:p>{properties}<w:r><w:t>{text}</w:t></w:r></w:p>"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    return out.getvalue()


def xlsx() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return out.getvalue()


def pdf(*pages: str) -> bytes:
    """A minimal PDF with one line of text on each page ('' makes a page with no text)."""
    objects: list[bytes] = []

    def add(body: str) -> int:
        objects.append(body.encode("latin-1"))
        return len(objects)

    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    pages_id = len(pages) * 2 + 2  # known in advance: font, then two objects per page
    for text in pages:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET" if text else ""
        content = add(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        page_ids.append(
            add(
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>"
            )
        )
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    assert add(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>") == pages_id
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\n".encode()
    out += f"startxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
