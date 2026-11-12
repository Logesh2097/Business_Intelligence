"""Prompts are text files in `prompts/`, reviewed like code; this loads them."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS = Path(__file__).parent / "prompts"


@lru_cache
def load_prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text().strip()


def block(label: str, content: str) -> str:
    """A clearly delimited piece of data inside a prompt."""
    return f"<<<{label}\n{content}\n{label}>>>"
