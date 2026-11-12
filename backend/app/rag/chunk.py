"""Pages to chunks: split on headings and paragraphs first, then by size.

A chunk is about 500 tokens with about 80 tokens carried over from the chunk before
it, and never crosses a PDF page. Size is counted in words (a token is about three
quarters of a word), so no tokenizer and no network are needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.extract import Page

MAX_WORDS = 380  # about 500 tokens
OVERLAP_WORDS = 60  # about 80 tokens
MAX_CHUNKS = 3000  # per document


@dataclass(frozen=True)
class Chunk:
    index: int  # position in the document, from 0
    page: int | None
    text: str


def _blocks(text: str) -> list[str]:
    """Paragraphs, with a heading starting a new one."""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                blocks.append(" ".join(current))
                current = []
        elif stripped.startswith("#"):
            if current:
                blocks.append(" ".join(current))
            current = [stripped]
        else:
            current.append(stripped)
    if current:
        blocks.append(" ".join(current))
    return blocks


def _pieces(block: str) -> list[str]:
    """A block no longer than MAX_WORDS, or its sentences, or its words in runs."""
    if len(block.split()) <= MAX_WORDS:
        return [block]
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", block):
        words = sentence.split()
        for start in range(0, len(words), MAX_WORDS):
            out.append(" ".join(words[start : start + MAX_WORDS]))
    return out


def _page_chunks(text: str) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for block in _blocks(text):
        for piece in _pieces(block):
            words = len(piece.split())
            if current and size + words > MAX_WORDS:
                chunks.append("\n\n".join(current))
                tail = " ".join(chunks[-1].split()[-OVERLAP_WORDS:])
                current, size = [tail], len(tail.split())
            current.append(piece)
            size += words
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk(pages: list[Page]) -> list[Chunk]:
    out: list[Chunk] = []
    for page in pages:
        for text in _page_chunks(page.text):
            out.append(Chunk(index=len(out), page=page.number, text=text))
            if len(out) > MAX_CHUNKS:
                raise ValueError("the document is too long to index")
    return out
