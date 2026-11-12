"""Settings, read once from BI_* environment variables.

Every key is explained in `.env.example` and in docs/design/architecture.md §7.
`problems()` lists the settings that are unsafe outside development; the app
refuses to start while the list is not empty.
"""

from __future__ import annotations

import secrets
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from psycopg.conninfo import conninfo_to_dict
from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# No password, key or token has a default in this file. They come from the environment
# (.env in development, Secret Manager in the cloud). `.env.example` holds placeholders
# that begin with this word, and a placeholder is refused outside development.
PLACEHOLDER = "change-me"
MIN_SECRET_LENGTH = 32
RELAXED_ENVS = ("dev", "test")

Env = Literal["dev", "test", "staging", "prod"]
Process = Literal["api", "mcp", "migrate"]

# backend/app/core/config.py -> the repository root, where .env lives in development
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BI_",
        env_file=_REPO_ROOT / ".env",
        extra="ignore",
        # a validation error names the field and the rule, never the rejected value
        hide_input_in_errors=True,
    )

    env: Env = "dev"
    port: int = Field(default=8110, ge=1, le=65535)
    version: str = "0.1.0"

    # `repr=False`: the field is left out of repr() and str(), so a traceback, a failed
    # test or a debug line that shows the settings object cannot show a password, a key
    # or a token. It is set on every field that holds one or that is an address which
    # can carry one (user:password@host). The value itself is read and used as before.
    pg_dsn: str = Field(default="postgresql://bi_app@localhost:5610/bi", repr=False)
    pg_readonly_dsn: str = Field(default="postgresql://bi_readonly@localhost:5610/bi", repr=False)
    pg_owner_dsn: str = Field(default="", repr=False)
    # Connection pooling and timeouts (docs/design/architecture.md §7).
    pg_pool_min: int = Field(default=0, ge=0)
    pg_pool_max: int = Field(default=10, ge=1)
    pg_connect_timeout_seconds: float = Field(default=3, gt=0)
    pg_pool_timeout_seconds: float = Field(default=5, gt=0)
    pg_statement_timeout_seconds: int = Field(default=30, gt=0)

    mongo_url: str = Field(default="mongodb://localhost:27027", repr=False)
    mongo_db: str = Field(default="bi", min_length=1)
    mongo_timeout_ms: int = Field(default=2000, gt=0)
    mongo_pool_max: int = Field(default=20, ge=1)

    qdrant_url: str = Field(default="http://localhost:6343", repr=False)
    qdrant_api_key: str = Field(default="", repr=False)

    openai_api_key: str = Field(default="", repr=False)
    # empty: OpenAI itself; else an OpenAI-compatible service
    openai_base_url: str = Field(default="", repr=False)
    llm_model: str = ""
    llm_model_fast: str = ""
    embedding_model: str = "text-embedding-3-small"

    secret_key: str = Field(default="", repr=False)
    access_token_minutes: int = 15
    refresh_token_days: int = 14

    tools_transport: Literal["direct", "mcp"] = "direct"
    mcp_url: str = Field(default="http://localhost:8111/mcp", repr=False)
    # where the bi-mcp process listens. Loopback by default: it is never a public server
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8111, ge=1, le=65535)

    upload_dir: str = "./data/uploads"
    doc_max_mb: int = Field(default=20, gt=0, le=200)  # one uploaded document
    doc_max_per_tenant: int = Field(default=200, gt=0)
    # passages scoring below this are not used (rag.md §3); to be set from an evaluation
    rag_min_score: float = Field(default=0.30, ge=0, le=1)
    gcs_bucket: str = ""

    seed_demo: bool = False
    seed_password: str = Field(default="", repr=False)
    fake_today: date | None = None

    daily_question_limit: int = Field(default=500, gt=0)
    daily_token_limit: int = Field(default=2_000_000, gt=0)
    query_timeout_seconds: int = Field(default=15, gt=0)
    query_max_rows: int = Field(default=1000, gt=0)
    agent_timeout_seconds: int = Field(default=90, gt=0)  # one agent answer, start to end
    assistant_timeout_seconds: int = Field(default=150, gt=0)  # supervisor, agents and summary

    # The messages below never repeat the value: it may hold a password.
    @field_validator("pg_dsn", "pg_readonly_dsn")
    @classmethod
    def _postgres_address(cls, value: str) -> str:
        if not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("must start with postgresql://")
        return value

    @field_validator("pg_owner_dsn")
    @classmethod
    def _optional_postgres_address(cls, value: str) -> str:
        if value and not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("must be empty or start with postgresql://")
        return value

    @field_validator("mongo_url")
    @classmethod
    def _mongo_address(cls, value: str) -> str:
        if not value.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("must start with mongodb:// or mongodb+srv://")
        return value

    @field_validator("qdrant_url", "mcp_url")
    @classmethod
    def _http_address(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return value

    @model_validator(mode="after")
    def _pool_sizes(self) -> Settings:
        if self.pg_pool_min > self.pg_pool_max:
            raise ValueError("BI_PG_POOL_MIN is larger than BI_PG_POOL_MAX")
        return self

    @field_validator("fake_today", mode="before")
    @classmethod
    def _empty_means_unset(cls, value: object) -> object:
        # `BI_FAKE_TODAY=` in a .env file is an empty string, which means "not set".
        return None if value == "" else value

    @model_validator(mode="after")
    def _development_secret(self) -> Settings:
        # In development a missing key gets a random one for this process, so nothing
        # is hardcoded. Sessions end at restart; set BI_SECRET_KEY in .env to keep them.
        if not self.secret_key and self.relaxed:
            self.secret_key = secrets.token_urlsafe(48)
        return self

    @property
    def relaxed(self) -> bool:
        """True in development and tests, where demo conveniences are allowed."""
        return self.env in RELAXED_ENVS

    def problems(self, process: Process = "api") -> list[str]:
        """Settings that must not reach staging or production. Empty means safe."""
        found: list[str] = []

        if _pg_user(self.pg_dsn) == _pg_user(self.pg_readonly_dsn):
            # True everywhere: the read-only user is the lock on AI-written SQL.
            found.append("BI_PG_READONLY_DSN must be a different database user from BI_PG_DSN")

        if self.relaxed:
            return found

        if not self.secret_key:
            found.append("BI_SECRET_KEY is missing")
        elif PLACEHOLDER in self.secret_key:
            found.append("BI_SECRET_KEY is the placeholder from .env.example")
        elif len(self.secret_key) < MIN_SECRET_LENGTH:
            found.append(f"BI_SECRET_KEY is shorter than {MIN_SECRET_LENGTH} characters")
        for name, value in (
            ("BI_PG_DSN", self.pg_dsn),
            ("BI_PG_READONLY_DSN", self.pg_readonly_dsn),
            ("BI_PG_OWNER_DSN", self.pg_owner_dsn),
            ("BI_MONGO_URL", self.mongo_url),
        ):
            if PLACEHOLDER in value:
                found.append(f"{name} holds a placeholder password from .env.example")
        if self.seed_demo:
            found.append("BI_SEED_DEMO is on")
        if self.fake_today is not None:
            found.append("BI_FAKE_TODAY is set")
        if not self.openai_api_key:
            found.append("BI_OPENAI_API_KEY is missing")
        if not self.llm_model:
            found.append("BI_LLM_MODEL is missing")
        if self.openai_base_url and not self.openai_base_url.startswith("https://"):
            found.append("BI_OPENAI_BASE_URL is not an https address")
        if self.pg_owner_dsn and process != "migrate":
            found.append("BI_PG_OWNER_DSN is set for a process that is not the migration job")
        return found

    def validate_or_raise(self, process: Process = "api") -> None:
        found = self.problems(process)
        if found:
            raise UnsafeSettingsError(found)


class UnsafeSettingsError(RuntimeError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Refusing to start: " + "; ".join(problems))


def _pg_user(dsn: str) -> str:
    try:
        return str(conninfo_to_dict(dsn).get("user") or "")
    except Exception:  # noqa: BLE001 - an unparsable DSN is reported by the readiness check
        return ""


class InvalidSettingsError(RuntimeError):
    """A BI_* variable is missing or malformed. Names the variables, never their values."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid settings: " + "; ".join(problems))


def load_settings(**overrides: object) -> Settings:
    """Read the settings; turn a validation failure into a message that is safe to log.

    Pydantic's own error repeats the rejected value, which for a connection string
    would put a password in the log.
    """
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        problems = []
        for error in exc.errors(include_input=False, include_url=False):
            name = "BI_" + str(error["loc"][0]).upper() if error["loc"] else "settings"
            problems.append(f"{name}: {error['msg'].removeprefix('Value error, ')}")
        raise InvalidSettingsError(problems) from None


@lru_cache
def get_settings() -> Settings:
    return load_settings()
