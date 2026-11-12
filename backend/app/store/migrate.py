"""Apply the numbered .sql files in `migrations/`, in order, once each.

Run as bi_owner (BI_PG_OWNER_DSN):   python -m app.store.migrate
Each file runs in one transaction and is recorded in public.schema_migrations.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import psycopg

from app.core.config import load_settings

log = logging.getLogger("bi.migrate")
MIGRATIONS = Path(__file__).parent / "migrations"


def pending(conn: psycopg.Connection) -> list[Path]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS public.schema_migrations "
        "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {row[0] for row in conn.execute("SELECT version FROM public.schema_migrations")}
    return [f for f in sorted(MIGRATIONS.glob("*.sql")) if f.stem not in done]


def migrate(owner_dsn: str) -> list[str]:
    """Apply what is pending. Returns the versions applied."""
    applied = []
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        for file in pending(conn):
            with conn.transaction():
                conn.execute(file.read_text())
                conn.execute(
                    "INSERT INTO public.schema_migrations (version) VALUES (%s)", (file.stem,)
                )
            applied.append(file.stem)
    return applied


def main() -> int:
    settings = load_settings()
    if not settings.pg_owner_dsn:
        print("BI_PG_OWNER_DSN is not set: migrations run as the database owner.", file=sys.stderr)
        return 1
    applied = migrate(settings.pg_owner_dsn)
    print("applied: " + (", ".join(applied) if applied else "nothing (already up to date)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
