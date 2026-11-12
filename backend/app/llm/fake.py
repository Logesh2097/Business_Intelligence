"""A scripted model for tests. No network, no key, no cost.

    llm = FakeLLM()
    llm.on("sql", SqlDraft(sql="SELECT 1", explanation="", assumptions=[]))
    llm.on("summary", Summary(text="One."))

Answers are consumed in order, per task. An answer may be an object, an exception to
raise, or a function of the call. A call nobody scripted fails the test, so a test
can never depend on what a real model would have said. Every prompt is recorded in
`calls`, so a test can assert what was (and was not) sent to the model.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.llm.base import T


@dataclass(frozen=True)
class Call:
    task: str
    system: str
    user: str
    fast: bool

    @property
    def text(self) -> str:
        return self.system + "\n" + self.user


Answer = BaseModel | BaseException | Callable[[Call], BaseModel]


class FakeLLM:
    def __init__(self) -> None:
        self._script: dict[str, deque[Answer]] = defaultdict(deque)
        self.calls: list[Call] = []

    def on(self, task: str, *answers: Answer) -> FakeLLM:
        self._script[task].extend(answers)
        return self

    def calls_for(self, task: str) -> list[Call]:
        return [call for call in self.calls if call.task == task]

    async def structured(
        self, *, task: str, system: str, user: str, schema: type[T], fast: bool = False
    ) -> T:
        call = Call(task=task, system=system, user=user, fast=fast)
        self.calls.append(call)
        if not self._script[task]:
            raise AssertionError(f"FakeLLM: no answer scripted for task {task!r}")
        answer: Any = self._script[task].popleft()
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer) and not isinstance(answer, BaseModel):
            answer = answer(call)
        assert isinstance(answer, schema), (
            f"scripted answer for {task!r} is not a {schema.__name__}"
        )
        return answer
