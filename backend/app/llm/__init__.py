"""Model integration: the one place that talks to a model provider.

- `base`     the small interface the rest of the app uses, and its errors
- `openai`   the real client (OpenAI through LangChain)
- `fake`     the scripted stand-in every test uses (docs/design/testing.md §2)

Nothing else in the app imports a provider's library.
"""

from app.llm.base import LLM, LLMError, LLMInvalidResponseError, LLMUnavailableError
from app.llm.factory import build_llm

__all__ = ["LLM", "LLMError", "LLMInvalidResponseError", "LLMUnavailableError", "build_llm"]
