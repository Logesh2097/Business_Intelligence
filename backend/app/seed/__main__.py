from __future__ import annotations

import argparse
import sys
from datetime import date

from app.core.clock import today
from app.core.config import load_settings
from app.seed.load import seed


def main() -> int:
    parser = argparse.ArgumentParser(description="Load the demo data (replaces existing rows).")
    parser.add_argument("--as-of", type=date.fromisoformat, help="data ends the day before this")
    parser.add_argument("--days", type=int, default=120)
    args = parser.parse_args()

    settings = load_settings()
    if not settings.relaxed or not settings.seed_demo:
        print(
            "Refused: the demo data loads only in dev or test with BI_SEED_DEMO=true.",
            file=sys.stderr,
        )
        return 1
    if not settings.pg_owner_dsn:
        print("BI_PG_OWNER_DSN is not set: the seed runs as the database owner.", file=sys.stderr)
        return 1
    as_of = args.as_of or today(settings)
    counts = seed(settings.pg_owner_dsn, as_of, args.days)
    print(f"demo data up to {as_of} ({args.days} days):")
    for table, count in counts.items():
        print(f"  {table:<28} {count:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
