#!/usr/bin/env bash
# Runs once, when the development database is first created.
# Creates the two database users the API connects as. bi_owner is created by the
# container itself. Grants on tables arrive with the schema; here the users only
# exist and can connect. Users and their jobs: docs/design/data-model.md §2.
#
# The passwords come from the environment (BI_PG_APP_PASSWORD, BI_PG_READONLY_PASSWORD),
# never from this file. psql's :'name' form quotes them safely.
set -euo pipefail

: "${BI_PG_APP_PASSWORD:?BI_PG_APP_PASSWORD is not set}"
: "${BI_PG_READONLY_PASSWORD:?BI_PG_READONLY_PASSWORD is not set}"

psql -v ON_ERROR_STOP=1 \
     -v app_password="$BI_PG_APP_PASSWORD" \
     -v readonly_password="$BI_PG_READONLY_PASSWORD" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE bi_app LOGIN PASSWORD :'app_password';
CREATE ROLE bi_readonly LOGIN PASSWORD :'readonly_password';

REVOKE ALL ON DATABASE bi FROM PUBLIC;
GRANT CONNECT ON DATABASE bi TO bi_app, bi_readonly;

-- Nobody but the owner creates objects in the default schema.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
