"""What the app asks of a model: fill in a typed object from a prompt.

Every call asks for structured output (a Pydantic model), never free text to be
parsed afterwards, so an incomplete or malformed answer is caught here.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Base of the model errors."""


class LLMUnavailableError(LLMError):
    """The provider cannot be reached, refused the key, is rate limited, or no key is set."""


class LLMInvalidResponseError(LLMError):
    """The model answered, but not with the object that was asked for."""


class LLM(Protocol):
    async def structured(
        self, *, task: str, system: str, user: str, schema: type[T], fast: bool = False
    ) -> T:
        """Return a `schema` filled in by the model.

        `task` names the call ("sql", "sql_repair", "summary") for logs and tests.
        `fast` asks for the cheaper model where the task is simple.
        """
        ...
