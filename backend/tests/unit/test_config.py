"""Start-up refuses unsafe settings outside development (architecture.md §7)."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import InvalidSettingsError, Settings, UnsafeSettingsError, load_settings

GOOD_SECRET = "s" * 40


def make(**overrides: Any) -> Settings:
    """Settings built from arguments only: the developer's .env never leaks into a test."""
    return Settings(_env_file=None, **overrides)


def prod(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "prod",
        "secret_key": GOOD_SECRET,
        "openai_api_key": "sk-test",
        "llm_model": "test-model",
    }
    base.update(overrides)
    return make(**base)


def test_development_defaults_are_accepted() -> None:
    assert make().problems() == []


def test_safe_production_settings_are_accepted() -> None:
    assert prod().problems() == []
    prod().validate_or_raise()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"secret_key": ""}, "BI_SECRET_KEY is missing"),
        (
            {"secret_key": "change-me-" + "x" * 40},
            "BI_SECRET_KEY is the placeholder from .env.example",
        ),
        (
            {"pg_dsn": "postgresql://bi_app:change-me-app@db/bi"},
            "BI_PG_DSN holds a placeholder password from .env.example",
        ),
        (
            {"mongo_url": "mongodb://bi:change-me@mongo"},
            "BI_MONGO_URL holds a placeholder password from .env.example",
        ),
        ({"secret_key": "short"}, "BI_SECRET_KEY is shorter than 32 characters"),
        ({"seed_demo": True}, "BI_SEED_DEMO is on"),
        ({"fake_today": date(2026, 11, 1)}, "BI_FAKE_TODAY is set"),
        ({"openai_api_key": ""}, "BI_OPENAI_API_KEY is missing"),
        ({"llm_model": ""}, "BI_LLM_MODEL is missing"),
        (
            {"openai_base_url": "http://models.internal/v1"},
            "BI_OPENAI_BASE_URL is not an https address",
        ),
        (
            {"pg_owner_dsn": "postgresql://bi_owner:x@db/bi"},
            "BI_PG_OWNER_DSN is set for a process that is not the migration job",
        ),
    ],
)
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_each_unsafe_setting_is_refused(env: str, overrides: dict[str, Any], expected: str) -> None:
    settings = prod(env=env, **overrides)
    assert settings.problems() == [expected]
    with pytest.raises(UnsafeSettingsError) as raised:
        settings.validate_or_raise()
    assert expected in str(raised.value)


@pytest.mark.parametrize("env", ["dev", "test"])
def test_the_same_settings_are_allowed_in_development(env: str) -> None:
    settings = make(
        env=env,
        secret_key="change-me-" + "x" * 40,
        pg_dsn="postgresql://bi_app:change-me-app@db/bi",
        seed_demo=True,
        fake_today=date(2026, 11, 1),
        pg_owner_dsn="postgresql://bi_owner:x@db/bi",
    )
    assert settings.problems() == []


def test_the_migration_job_may_hold_the_owner_dsn() -> None:
    settings = prod(pg_owner_dsn="postgresql://bi_owner:x@db/bi")
    assert settings.problems(process="migrate") == []


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_readonly_user_must_differ_from_the_app_user(env: str) -> None:
    same = "postgresql://bi_app:x@db/bi"
    settings = prod(env=env, pg_dsn=same, pg_readonly_dsn=same)
    assert settings.problems() == [
        "BI_PG_READONLY_DSN must be a different database user from BI_PG_DSN"
    ]


def test_an_unknown_environment_is_refused() -> None:
    # an unknown name must not silently switch every guard off
    with pytest.raises(ValueError):
        make(env="production")


def test_settings_come_from_bi_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BI_PORT", "9999")
    monkeypatch.setenv("BI_QUERY_MAX_ROWS", "50")
    settings = make()
    assert (settings.port, settings.query_max_rows) == (9999, 50)


def test_development_gets_a_random_key_when_none_is_set() -> None:
    first, second = make(), make()
    assert len(first.secret_key) >= 32
    assert first.secret_key != second.secret_key  # generated, not a constant in the code


def test_no_credential_has_a_default_in_the_code() -> None:
    fields = Settings.model_fields
    for name in ("secret_key", "openai_api_key", "qdrant_api_key", "seed_password", "pg_owner_dsn"):
        assert fields[name].default == "", name
    for name in ("pg_dsn", "pg_readonly_dsn", "mongo_url"):
        address = fields[name].default.split("//", 1)[1]
        user_part = address.split("@")[0] if "@" in address else ""
        assert ":" not in user_part, name  # a user name at most, never user:password


def test_env_example_loads_as_it_is_and_is_safe_for_development() -> None:
    # `make setup` copies this file to .env, so it must start the app unchanged.
    example = Path(__file__).resolve().parents[3] / ".env.example"
    settings = Settings(_env_file=example)
    assert settings.problems() == []
    assert settings.fake_today is None  # an empty value means "not set"


def test_env_example_holds_no_real_secret() -> None:
    example = Path(__file__).resolve().parents[3] / ".env.example"
    for line in example.read_text().splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if any(word in key for word in ("PASSWORD", "SECRET", "API_KEY")):
            assert value == "" or value.startswith("change-me"), key
        if "://" in value and "@" in value:
            password = value.split("://", 1)[1].split("@")[0].split(":", 1)[1]
            assert password.startswith("change-me"), key


def test_env_example_is_refused_outside_development() -> None:
    example = Path(__file__).resolve().parents[3] / ".env.example"
    problems = Settings(_env_file=example, env="prod").problems()
    assert "BI_SECRET_KEY is missing" in problems
    assert "BI_PG_DSN holds a placeholder password from .env.example" in problems
    assert "BI_SEED_DEMO is on" in problems


# -- missing or invalid environment variables --------------------------------


@pytest.mark.parametrize(
    ("variable", "value", "expected"),
    [
        ("BI_PG_DSN", "mysql://bi_app:pw-in-dsn@db/bi", "BI_PG_DSN: must start with postgresql://"),
        ("BI_PG_DSN", "", "BI_PG_DSN: must start with postgresql://"),
        (
            "BI_PG_READONLY_DSN",
            "bi_readonly:pw-in-dsn@db/bi",
            "BI_PG_READONLY_DSN: must start with postgresql://",
        ),
        (
            "BI_PG_OWNER_DSN",
            "owner:pw-in-dsn@db",
            "BI_PG_OWNER_DSN: must be empty or start with postgresql://",
        ),
        (
            "BI_MONGO_URL",
            "http://user:pw-in-dsn@mongo",
            "BI_MONGO_URL: must start with mongodb:// or mongodb+srv://",
        ),
        ("BI_MONGO_DB", "", "BI_MONGO_DB"),
        ("BI_QDRANT_URL", "qdrant:6333", "BI_QDRANT_URL: must start with http:// or https://"),
        ("BI_PORT", "not-a-number", "BI_PORT"),
        ("BI_PORT", "70000", "BI_PORT"),
        ("BI_PG_POOL_MAX", "0", "BI_PG_POOL_MAX"),
        ("BI_PG_POOL_TIMEOUT_SECONDS", "-1", "BI_PG_POOL_TIMEOUT_SECONDS"),
        ("BI_PG_STATEMENT_TIMEOUT_SECONDS", "0", "BI_PG_STATEMENT_TIMEOUT_SECONDS"),
        ("BI_MONGO_TIMEOUT_MS", "abc", "BI_MONGO_TIMEOUT_MS"),
        ("BI_ENV", "production", "BI_ENV"),
        ("BI_FAKE_TODAY", "tomorrow", "BI_FAKE_TODAY"),
    ],
)
def test_an_invalid_variable_is_named_and_its_value_is_never_repeated(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, expected: str
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(InvalidSettingsError) as raised:
        load_settings(_env_file=None)
    message = str(raised.value)
    assert expected in message
    assert "pw-in-dsn" not in message  # a rejected connection string may hold a password
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_every_invalid_variable_is_reported_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BI_PORT", "0")
    monkeypatch.setenv("BI_MONGO_URL", "nope")
    with pytest.raises(InvalidSettingsError) as raised:
        load_settings(_env_file=None)
    assert len(raised.value.problems) == 2


def test_pool_minimum_larger_than_maximum_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BI_PG_POOL_MIN", "5")
    monkeypatch.setenv("BI_PG_POOL_MAX", "2")
    with pytest.raises(InvalidSettingsError, match="BI_PG_POOL_MIN is larger than BI_PG_POOL_MAX"):
        load_settings(_env_file=None)


def test_with_no_variables_at_all_development_still_starts() -> None:
    # Missing variables fall back to local defaults that hold no credential.
    settings = load_settings(_env_file=None)
    assert settings.problems() == []
    assert (settings.pg_pool_min, settings.pg_pool_max, settings.mongo_timeout_ms) == (0, 10, 2000)


@pytest.mark.parametrize("missing", ["BI_SECRET_KEY", "BI_OPENAI_API_KEY", "BI_LLM_MODEL"])
def test_a_missing_required_variable_stops_production(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv("BI_ENV", "prod")
    monkeypatch.setenv("BI_SECRET_KEY", "s" * 40)
    monkeypatch.setenv("BI_OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("BI_LLM_MODEL", "test-model")
    monkeypatch.delenv(missing)
    settings = load_settings(_env_file=None)
    with pytest.raises(UnsafeSettingsError, match=f"{missing} is missing"):
        settings.validate_or_raise()


# -- the settings object never shows a secret ---------------------------------
# Dummy values only. Each is a string that appears nowhere else, so finding it in
# any text proves a leak.

_DUMMY = {
    "pg_dsn": "postgresql://bi_app:dummy-pg-pass-1@db.internal/bi",
    "pg_readonly_dsn": "postgresql://bi_readonly:dummy-ro-pass-2@db.internal/bi",
    "pg_owner_dsn": "postgresql://bi_owner:dummy-owner-pass-3@db.internal/bi",
    "mongo_url": "mongodb://bi:dummy-mongo-pass-4@mongo.internal:27017",
    "qdrant_url": "https://user:dummy-qdrant-pass-5@qdrant.internal",
    "qdrant_api_key": "dummy-qdrant-key-6",
    "openai_api_key": "sk-dummy-model-key-7",
    "openai_base_url": "https://user:dummy-proxy-pass-8@models.internal/v1/",
    "secret_key": "dummy-signing-key-9-" + "x" * 32,
    "mcp_url": "https://user:dummy-mcp-pass-10@tools.internal/mcp",
    "seed_password": "dummy-seed-pass-11",
}
_DUMMY_SECRETS = (
    "dummy-pg-pass-1", "dummy-ro-pass-2", "dummy-owner-pass-3", "dummy-mongo-pass-4",
    "dummy-qdrant-pass-5", "dummy-qdrant-key-6", "sk-dummy-model-key-7", "dummy-proxy-pass-8",
    "dummy-signing-key-9", "dummy-mcp-pass-10", "dummy-seed-pass-11",
)  # fmt: skip


def test_every_field_that_can_hold_a_secret_is_left_out_of_the_representation() -> None:
    hidden = {name for name, field in Settings.model_fields.items() if not field.repr}
    assert hidden == set(_DUMMY)
    # and no other field looks like one: a new secret field must be hidden too
    for name in set(Settings.model_fields) - hidden:
        assert not any(
            word in name for word in ("dsn", "url", "key", "password", "secret", "token")
        ) or name in ("access_token_minutes", "refresh_token_days", "daily_token_limit"), name


@pytest.mark.parametrize("show", [repr, str, lambda s: f"{s}", lambda s: f"{s!r}"])
def test_the_settings_representation_holds_no_password_key_or_token(show: Any) -> None:
    settings = make(env="test", **_DUMMY)
    text = show(settings)
    for secret in _DUMMY_SECRETS:
        assert secret not in text
    assert "://" not in text  # no address at all, so nothing that could carry a password
    assert "env='test'" in text and "port=8110" in text  # the rest is still shown


def test_hiding_a_field_does_not_change_its_value_or_how_it_is_checked() -> None:
    settings = make(**_DUMMY)
    for name, value in _DUMMY.items():
        assert getattr(settings, name) == value
    assert settings.model_dump()["pg_dsn"] == _DUMMY["pg_dsn"]  # only repr and str changed
    assert make().pg_dsn == "postgresql://bi_app@localhost:5610/bi"  # defaults as before
    assert make().problems() == []


@pytest.mark.parametrize(
    "bad",
    [
        {"pg_dsn": "mysql://bi_app:dummy-pg-pass-1@db/bi"},
        {"mongo_url": "http://bi:dummy-mongo-pass-4@mongo"},
        {"port": 70000},  # another field is wrong; the secrets given beside it stay out
        {"pg_pool_min": 50, "pg_pool_max": 2},  # a whole-object rule fails
    ],
)
def test_a_validation_error_raised_directly_never_repeats_a_value(bad: dict[str, Any]) -> None:
    # load_settings() already hides values; this is Settings(...) itself, as tests call it
    with pytest.raises(ValidationError) as raised:
        make(**{**_DUMMY, **bad})
    text = str(raised.value) + repr(raised.value)
    for secret in _DUMMY_SECRETS:
        assert secret not in text
    assert "input_value" not in text


def test_a_failing_test_that_holds_the_settings_does_not_print_a_secret(tmp_path: Path) -> None:
    # What happened on 2026-11-12: a test with a settings argument failed, and pytest
    # printed the argument. Run exactly that, in a process of its own, and read its output.
    backend = Path(__file__).resolve().parents[2]
    (tmp_path / "test_leak.py").write_text(
        "import pytest\n"
        "from app.core.config import Settings\n"
        f"DUMMY = {_DUMMY!r}\n"
        "@pytest.fixture\n"
        "def settings():\n"
        "    return Settings(_env_file=None, env='test', **DUMMY)\n"
        "def test_fails(settings):\n"
        "    copy = settings\n"
        "    assert copy.port == 1, f'failed with {settings}'\n"
    )
    done = subprocess.run(  # noqa: S603 - our own interpreter on a file written above
        [sys.executable, "-m", "pytest", "-l", "--tb=long", "-p", "no:cacheprovider",
         "-c", os.devnull, "--rootdir", str(tmp_path), str(tmp_path / "test_leak.py")],
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(backend)},
    )  # fmt: skip
    output = done.stdout + done.stderr
    assert done.returncode == 1 and "1 failed" in output, output[-400:]
    assert "settings = Settings(" in output  # pytest did print the object
    for secret in _DUMMY_SECRETS:
        assert secret not in output
