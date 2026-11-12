"""Context tokens: how the caller's identity reaches the MCP server (mcp.md §3).

The API makes one per tool call: who is asking, signed, valid for two minutes. The MCP
server trusts nothing else in a request. The key is derived from `BI_SECRET_KEY` under
its own label, so no other token signed with that secret can pass as a context token.

A token is `<payload>.<signature>`, both base64url; the signature is HMAC-SHA256.
Standard library only. It is never sent to a browser and never placed in a prompt.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

from app.core.context import Caller

LABEL = b"bi-context-token-v1"
LIFETIME_SECONDS = 120
MAX_TOKEN_CHARS = 8192


class InvalidContextToken(Exception):
    """The token is missing, malformed, forged or expired. The reason is for the log only."""


def _key(secret_key: str) -> bytes:
    if not secret_key:
        raise InvalidContextToken("no signing key")
    return hmac.new(secret_key.encode(), LABEL, hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make(caller: Caller, secret_key: str, *, now: float | None = None) -> str:
    issued = int(time.time() if now is None else now)
    payload = {
        "v": 1,
        "iat": issued,
        "exp": issued + LIFETIME_SECONDS,
        "user_id": str(caller.user_id),
        "tenant_id": str(caller.tenant_id),
        "tenant_name": caller.tenant_name,
        "role": caller.role,
        "timezone": caller.timezone,
        "currency": caller.currency,
        "all_outlets": caller.all_outlets,
        "outlet_ids": [str(o) for o in caller.outlet_ids],
        "outlet_names": list(caller.outlet_names),
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.new(_key(secret_key), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def verify(token: str, secret_key: str, *, now: float | None = None) -> Caller:
    """The caller the token names, or InvalidContextToken."""
    if not token or len(token) > MAX_TOKEN_CHARS or token.count(".") != 1:
        raise InvalidContextToken("malformed")
    body, signature = token.split(".")
    expected = hmac.new(_key(secret_key), body.encode(), hashlib.sha256).digest()
    try:
        given = _unb64(signature)
    except (ValueError, TypeError):
        raise InvalidContextToken("malformed") from None
    if not hmac.compare_digest(given, expected):
        raise InvalidContextToken("bad signature")
    try:
        payload: dict[str, Any] = json.loads(_unb64(body))
        if payload.get("v") != 1:
            raise InvalidContextToken("unknown version")
        current = time.time() if now is None else now
        if not int(payload["iat"]) - 5 <= current <= int(payload["exp"]):
            raise InvalidContextToken("expired")
        if int(payload["exp"]) - int(payload["iat"]) > LIFETIME_SECONDS:
            raise InvalidContextToken("lifetime too long")
        return Caller(
            user_id=uuid.UUID(payload["user_id"]),
            tenant_id=uuid.UUID(payload["tenant_id"]),
            tenant_name=str(payload["tenant_name"]),
            role=str(payload["role"]),
            timezone=str(payload["timezone"]),
            currency=str(payload["currency"]),
            all_outlets=payload["all_outlets"] is True,
            outlet_ids=tuple(uuid.UUID(o) for o in payload["outlet_ids"]),
            outlet_names=tuple(str(n) for n in payload["outlet_names"]),
        )
    except InvalidContextToken:
        raise
    except (KeyError, ValueError, TypeError, AttributeError):
        raise InvalidContextToken("malformed payload") from None
