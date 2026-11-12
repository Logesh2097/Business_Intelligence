"""MOCKED: context tokens. No server, no network."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import replace

import pytest

from app.core import context_token
from app.core.context_token import LIFETIME_SECONDS, InvalidContextToken, make, verify
from tests.unit.analytics_fakes import caller

KEY = "k" * 40
OTHER_KEY = "z" * 40


def test_a_token_carries_the_caller_exactly() -> None:
    for who in (caller("admin"), caller("viewer", limited=True)):
        assert verify(make(who, KEY), KEY) == who


def test_a_token_lives_two_minutes() -> None:
    assert LIFETIME_SECONDS == 120
    token = make(caller(), KEY, now=1000)
    assert verify(token, KEY, now=1000 + 119) == caller()
    with pytest.raises(InvalidContextToken, match="expired"):
        verify(token, KEY, now=1000 + 121)
    with pytest.raises(InvalidContextToken, match="expired"):
        verify(token, KEY, now=900)  # not yet valid: a clock far behind


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", ".", "x" * 9000, "not base64.###"])
def test_a_malformed_token_is_refused(token: str) -> None:
    with pytest.raises(InvalidContextToken):
        verify(token, KEY)


def test_a_token_signed_with_another_key_is_refused() -> None:
    with pytest.raises(InvalidContextToken, match="bad signature"):
        verify(make(caller(), OTHER_KEY), KEY)


def _forge(payload: dict[str, object], key: bytes) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signature = hmac.new(key, body.encode(), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def test_a_changed_payload_is_refused() -> None:
    token = make(caller("viewer", limited=True), KEY)
    body, signature = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["role"], payload["all_outlets"] = "admin", True  # what an attacker would want
    changed = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    with pytest.raises(InvalidContextToken, match="bad signature"):
        verify(f"{changed}.{signature}", KEY)


def test_a_token_signed_with_the_raw_secret_is_refused() -> None:
    # anything else signed with BI_SECRET_KEY itself (an access token, say) is not a
    # context token: the context key is derived under its own label
    now = int(time.time())
    payload = {"v": 1, "iat": now, "exp": now + 60, "user_id": str(caller().user_id),
               "tenant_id": str(caller().tenant_id), "tenant_name": "x", "role": "admin",
               "timezone": "UTC", "currency": "INR", "all_outlets": True, "outlet_ids": [],
               "outlet_names": []}  # fmt: skip
    with pytest.raises(InvalidContextToken, match="bad signature"):
        verify(_forge(payload, KEY.encode()), KEY)
    # the same payload under the derived key is accepted: the forgery above failed on the key
    derived = hmac.new(KEY.encode(), context_token.LABEL, hashlib.sha256).digest()
    assert verify(_forge(payload, derived), KEY).role == "admin"


def test_a_validly_signed_token_that_asks_for_a_long_life_or_lacks_a_field_is_refused() -> None:
    derived = hmac.new(KEY.encode(), context_token.LABEL, hashlib.sha256).digest()
    now = int(time.time())
    base = json.loads(base64.urlsafe_b64decode(make(caller(), KEY).split(".")[0] + "=="))
    with pytest.raises(InvalidContextToken, match="lifetime"):
        verify(_forge({**base, "iat": now, "exp": now + 86400}, derived), KEY)
    for field in ("tenant_id", "user_id", "role", "outlet_ids"):
        with pytest.raises(InvalidContextToken):
            verify(_forge({k: v for k, v in base.items() if k != field}, derived), KEY)
    with pytest.raises(InvalidContextToken):
        verify(_forge({**base, "tenant_id": "not-a-uuid"}, derived), KEY)


def test_all_outlets_is_true_only_for_the_boolean_true() -> None:
    derived = hmac.new(KEY.encode(), context_token.LABEL, hashlib.sha256).digest()
    base = json.loads(base64.urlsafe_b64decode(make(caller(), KEY).split(".")[0] + "=="))
    assert verify(_forge({**base, "all_outlets": "yes"}, derived), KEY).all_outlets is False
    assert verify(_forge({**base, "all_outlets": 1}, derived), KEY).all_outlets is False


def test_no_signing_key_means_no_token() -> None:
    with pytest.raises(InvalidContextToken):
        make(caller(), "")
    with pytest.raises(InvalidContextToken):
        verify(make(caller(), KEY), "")


def test_two_companies_get_different_tokens_and_neither_reads_as_the_other() -> None:
    other = replace(caller(), tenant_id=__import__("uuid").uuid4())
    assert verify(make(other, KEY), KEY).tenant_id == other.tenant_id != caller().tenant_id
