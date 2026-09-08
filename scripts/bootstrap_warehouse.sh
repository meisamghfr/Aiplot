#!/usr/bin/env bash
set -euo pipefail

warehouse_host="${AIPLOT_WAREHOUSE_HOST:-127.0.0.1}"
warehouse_port="${AIPLOT_WAREHOUSE_PORT:-5432}"
warehouse_db="${AIPLOT_WAREHOUSE_DB:-aiplot_warehouse}"
warehouse_user="${AIPLOT_WAREHOUSE_USER:-$(id -un)}"

if ! psql -h "$warehouse_host" -p "$warehouse_port" -d postgres -Atc \
  "select 1 from pg_database where datname = '$warehouse_db'" | grep -qx 1; then
  createdb -h "$warehouse_host" -p "$warehouse_port" "$warehouse_db"
fi

psql -h "$warehouse_host" -p "$warehouse_port" -d "$warehouse_db" \
  -v ON_ERROR_STOP=1 -f warehouse/bootstrap.sql

AIPLOT_WAREHOUSE_USER="$warehouse_user" .venv/bin/dbt build \
  --project-dir dbt_analytics --profiles-dir dbt_analytics
