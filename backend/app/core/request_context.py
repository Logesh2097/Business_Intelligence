"""Values that belong to the request being served, readable from anywhere in it."""

from __future__ import annotations

from contextvars import ContextVar

# Set by the request middleware; appears in every log line and every error body.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
