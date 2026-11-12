"""A file's text, as pages. PDFs keep their page numbers; other kinds are one page
with no number. No OCR: a scanned PDF with no text layer is refused with a reason.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET  # noqa: S405 - input size is capped; see _docx
import zipfile
from dataclasses import dataclass

MAX_PAGES = 2000
MAX_DOCX_XML_BYTES = 50 * 1024 * 1024
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ExtractionFailed(Exception):
    """The text could not be read. `reason` is a sentence for the user."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Page:
    number: int | None  # the PDF page, starting at 1; None for other kinds
    text: str


def extract(data: bytes, kind: str) -> list[Page]:
    """Pages that hold text. Blocking and CPU-bound: call it in a worker thread."""
    if kind == "pdf":
        pages = _pdf(data)
    elif kind == "docx":
        pages = _docx(data)
    elif kind in ("txt", "md"):
        pages = [Page(None, data.decode("utf-8-sig", errors="replace"))]
    else:
        raise ExtractionFailed("This kind of file is not supported.")
    pages = [Page(p.number, p.text.replace("\x00", "").strip()) for p in pages]
    pages = [p for p in pages if p.text]
    if not pages:
        raise ExtractionFailed(
            "This PDF has no readable text. Scanned documents are not supported yet."
            if kind == "pdf"
            else "This file has no readable text."
        )
    return pages


def _pdf(data: bytes) -> list[Page]:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
    except ImportError:  # pragma: no cover - pypdf is a declared dependency
        raise ExtractionFailed("PDF support is not installed on this server.") from None
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractionFailed("This PDF is password-protected and cannot be read.")
        if len(reader.pages) > MAX_PAGES:
            raise ExtractionFailed(f"This PDF has more than {MAX_PAGES} pages.")
        return [
            Page(number, page.extract_text() or "")
            for number, page in enumerate(reader.pages, start=1)
        ]
    except ExtractionFailed:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise ExtractionFailed("This PDF is damaged and cannot be read.") from None


def _docx(data: bytes) -> list[Page]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:  # also the guard against a zip bomb
                raise ExtractionFailed("This Word file is too large to read.")
            xml = archive.read(info)
        if b"<!DOCTYPE" in xml[:4096] or b"<!ENTITY" in xml[:4096]:
            # a real DOCX never declares entities; one that does is not read
            raise ExtractionFailed("This Word file cannot be read.")
        root = ET.fromstring(xml)  # noqa: S314 - size capped and entities refused above
    except ExtractionFailed:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise ExtractionFailed("This Word file is damaged and cannot be read.") from None

    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_W}p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag in (f"{_W}tab",):
                parts.append(" ")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        text = "".join(parts).strip()
        if not text:
            continue
        style = paragraph.find(f"{_W}pPr/{_W}pStyle")
        name = (style.get(f"{_W}val") or "") if style is not None else ""
        paragraphs.append(f"# {text}" if name.lower().startswith(("heading", "title")) else text)
    return [Page(None, "\n\n".join(paragraphs))]
