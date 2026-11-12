"""What an uploaded file is, read from its bytes and not from its name.

Accepted: PDF, DOCX, plain text and Markdown. Tabular files (CSV, Excel) are refused:
numbers belong in the database, where they can be added up correctly.
"""

from __future__ import annotations

import io
import re
import zipfile

KINDS = ("pdf", "docx", "txt", "md")
MAX_TITLE_CHARS = 200
MAX_FILENAME_CHARS = 200


class RejectedFile(Exception):
    """The file is not accepted. `reason` is a sentence for the user."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _looks_tabular(text: str) -> bool:
    lines = [line for line in text.splitlines()[:200] if line.strip()]
    if len(lines) < 5:
        return False
    for delimiter in (",", ";", "\t", "|"):
        counts = [line.count(delimiter) for line in lines]
        common = max(set(counts), key=counts.count)
        if common >= 2 and counts.count(common) >= 0.9 * len(lines):
            return True
    return False


def detect(data: bytes, filename: str) -> str:
    """One of KINDS, or RejectedFile."""
    if not data:
        raise RejectedFile("The file is empty.")
    lowered = filename.lower()
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            raise RejectedFile("The file is damaged and cannot be read.") from None
        if "word/document.xml" in names:
            return "docx"
        if any(name.startswith("xl/") for name in names):
            raise RejectedFile(
                "Spreadsheets are not accepted as documents. Numbers belong in the database."
            )
        raise RejectedFile("Only PDF, Word (DOCX), text and Markdown files are accepted.")
    if b"\x00" in data[:8192]:
        raise RejectedFile("Only PDF, Word (DOCX), text and Markdown files are accepted.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RejectedFile(
            "Only PDF, Word (DOCX), text and Markdown files are accepted. "
            "A text file must be saved as UTF-8."
        ) from None
    if not text.strip():
        raise RejectedFile("The file has no text.")
    if lowered.endswith((".csv", ".tsv")) or _looks_tabular(text):
        raise RejectedFile(
            "Tables of data (CSV) are not accepted as documents. Numbers belong in the database."
        )
    # text and Markdown are the same bytes; only here does the name decide anything
    return "md" if lowered.endswith((".md", ".markdown")) else "txt"


def clean_filename(filename: str) -> str:
    """The name as it will be shown: no path, no control characters, bounded."""
    name = re.split(r"[\\/]", filename.strip())[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[:MAX_FILENAME_CHARS] or "document"


def clean_title(title: str | None, filename: str) -> str:
    # white space first (a line break becomes a space), then anything unprintable goes
    text = "".join(ch for ch in " ".join((title or "").split()) if ch.isprintable())
    if not text:
        text = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", clean_filename(filename))
        text = re.sub(r"[_\-]+", " ", text).strip() or "Document"
    return text[:MAX_TITLE_CHARS]
