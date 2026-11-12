"""Pick the tables and metrics a question needs, so the model is given only those.

Plain word matching against the keywords in tables.yaml and the names in
metrics.yaml, then the tables needed to join the chosen ones. No model call and no
vector search: it is fast, repeatable and explainable. If it proves too coarse, a
vector search can replace `relevant()` without touching the callers.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

from app.semantic.model import Metric, Table, Vocabulary

MAX_TABLES = 6
# what most questions are about, used when no word of the question matches anything
FALLBACK = ("orders", "order_items", "outlets", "products")


@dataclass(frozen=True)
class Relevant:
    tables: tuple[Table, ...]
    metrics: tuple[Metric, ...]


def everything(vocabulary: Vocabulary) -> Relevant:
    """All of a vocabulary. For an agent, whose vocabulary is already cut down to its
    own few tables: choosing among them by word matching only loses what it needs."""
    return Relevant(tables=vocabulary.tables, metrics=vocabulary.metrics)


def _words(text: str) -> set[str]:
    words = set(re.findall(r"[a-z]+", text.lower()))
    # crude singular forms so "outlets" matches "outlet" and "categories" matches "category"
    singular = {w[:-3] + "y" for w in words if w.endswith("ies")}
    singular |= {w[:-1] for w in words if w.endswith("s") and len(w) > 3}
    return words | singular


def _matches(phrase: str, text: str, words: set[str]) -> bool:
    return phrase in text if " " in phrase else phrase in words


def relevant(question: str, vocabulary: Vocabulary) -> Relevant:
    text = " ".join(re.findall(r"[a-z]+", question.lower()))
    words = _words(question)

    metrics = tuple(m for m in vocabulary.metrics if any(_matches(n, text, words) for n in m.names))

    scores: dict[str, int] = {}
    for table in vocabulary.tables:
        score = sum(1 for k in table.keywords if _matches(k, text, words))
        if score:
            scores[table.table] = score
    for metric in metrics:
        for name in metric.tables:
            scores[name] = scores.get(name, 0) + 2  # a metric's tables are needed, not just likely

    if not scores:
        chosen = list(FALLBACK)
    else:
        chosen = sorted(scores, key=lambda name: (-scores[name], name))[:MAX_TABLES]
        chosen = _connect(chosen, vocabulary)[: MAX_TABLES + 2]

    order = [t.table for t in vocabulary.tables]
    chosen.sort(key=order.index)
    return Relevant(tables=tuple(vocabulary.table(n) for n in chosen), metrics=metrics)


def _connect(chosen: list[str], vocabulary: Vocabulary) -> list[str]:
    """Add the tables that sit between the chosen ones, so they can be joined."""
    graph: dict[str, set[str]] = {t.table: set() for t in vocabulary.tables}
    for table in vocabulary.tables:
        for join in table.joins:
            graph[table.table].add(join.table)
            graph[join.table].add(table.table)

    result = list(chosen)
    anchor = chosen[0]
    for target in chosen[1:]:
        for step in _path(graph, anchor, target):
            if step not in result:
                result.append(step)
    return result


def _path(graph: dict[str, set[str]], start: str, goal: str) -> list[str]:
    queue = deque([[start]])
    seen = {start}
    while queue:
        path = queue.popleft()
        if path[-1] == goal:
            return path
        for neighbour in sorted(graph[path[-1]]):
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append([*path, neighbour])
    return []
