#!/bin/sh
set -eu

role="${1:?role: pipeline_writer, lifecycle_writer ou streamlit_readonly}"
password="${2:?nova senha}"

case "$role" in
  pipeline_writer|lifecycle_writer|streamlit_readonly) ;;
  *) echo "role não permitida" >&2; exit 1 ;;
esac

docker compose exec -T postgres \
  psql -v ON_ERROR_STOP=1 \
       --username "${POSTGRES_USER:-spy_admin}" \
       --dbname "${POSTGRES_DB:-mineracao_fb}" \
       -v role="$role" \
       -v new_password="$password" <<'SQL'
SELECT format(
  'ALTER ROLE %I WITH PASSWORD %L',
  :'role',
  :'new_password'
) AS statement \gexec
SQL
