#!/usr/bin/env bash
# A project-local PostgreSQL for machines without Docker.
#
#   scripts/local-postgres.sh up       create (first time) and start it on port 5610
#   scripts/local-postgres.sh down     stop it
#   scripts/local-postgres.sh status   is it running?
#   scripts/local-postgres.sh psql     open psql as bi_owner
#   scripts/local-postgres.sh wipe     stop it and delete its data folder
#
# It gives the same result as `docker compose up -d postgres`: database `bi`, users
# bi_owner, bi_app and bi_readonly, with the passwords from .env. The data lives in
# .pgdata/ (not in git). Use Docker instead when you have it.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=5610
DATA="$PWD/.pgdata"
BIN="${BI_PG_BIN:-$(ls -d /usr/lib/postgresql/16/bin 2>/dev/null || true)}"

if [ -z "$BIN" ] || [ ! -x "$BIN/initdb" ]; then
  echo "PostgreSQL 16 binaries not found. Set BI_PG_BIN to their folder, or use Docker." >&2
  exit 1
fi
if [ ! -f .env ]; then
  echo "No .env file. Run 'make setup' first." >&2
  exit 1
fi

# Read the three passwords from .env without printing them.
value() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }
OWNER_PASSWORD="$(value BI_PG_OWNER_PASSWORD)"
export BI_PG_APP_PASSWORD="$(value BI_PG_APP_PASSWORD)"
export BI_PG_READONLY_PASSWORD="$(value BI_PG_READONLY_PASSWORD)"
: "${OWNER_PASSWORD:?BI_PG_OWNER_PASSWORD is not set in .env}"

running() { "$BIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; }

case "${1:-}" in
  up)
    if [ ! -d "$DATA" ]; then
      pwfile="$(mktemp)"; trap 'rm -f "$pwfile"' EXIT
      printf '%s' "$OWNER_PASSWORD" > "$pwfile"
      "$BIN/initdb" -D "$DATA" -U bi_owner --pwfile="$pwfile" \
        --auth=scram-sha-256 --encoding=UTF8 --locale=C >/dev/null
      created=1
    fi
    if ! running; then
      "$BIN/pg_ctl" -D "$DATA" -w -l "$DATA/server.log" \
        -o "-p $PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories='$DATA'" start >/dev/null
    fi
    if [ "${created:-0}" = 1 ]; then
      export PGPASSWORD="$OWNER_PASSWORD" PGHOST=127.0.0.1 PGPORT=$PORT
      "$BIN/createdb" -U bi_owner bi
      # the same script the Docker image runs on its first start
      POSTGRES_USER=bi_owner POSTGRES_DB=bi PATH="$BIN:$PATH" deploy/postgres/init-roles.sh >/dev/null
    fi
    echo "PostgreSQL is running on 127.0.0.1:$PORT (database bi)"
    ;;
  down)
    if running; then "$BIN/pg_ctl" -D "$DATA" -m fast stop >/dev/null; fi
    echo "PostgreSQL is stopped"
    ;;
  status)
    if running; then echo "running on 127.0.0.1:$PORT"; else echo "stopped"; exit 3; fi
    ;;
  psql)
    PGPASSWORD="$OWNER_PASSWORD" exec "$BIN/psql" -h 127.0.0.1 -p $PORT -U bi_owner bi
    ;;
  wipe)
    if running; then "$BIN/pg_ctl" -D "$DATA" -m fast stop >/dev/null; fi
    if [ -d "$DATA" ] && [ -f "$DATA/PG_VERSION" ]; then rm -rf -- "$PWD/.pgdata"; fi
    echo "Local PostgreSQL data deleted"
    ;;
  *)
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
