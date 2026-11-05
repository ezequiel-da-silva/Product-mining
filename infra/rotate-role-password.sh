#!/bin/sh
set -eu

role="${1:?role: pipeline_writer, lifecycle_writer ou streamlit_readonly}"
password="${2:?nova senha}"

case "$role" in
  pipeline_writer|lifecycle_writer|streamlit_readonly) ;;
  *) echo "role não permitida" >&2; exit 1 ;;
esac

if [ "$(printf '%s' "$password" | tr -d '\n')" != "$password" ]; then
  echo "senha com quebra de linha não entra no userlist do PgBouncer" >&2
  exit 1
fi

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

if [ "$role" = "streamlit_readonly" ]; then
  echo "streamlit_readonly fala direto com o Postgres. Atualize STREAMLIT_DB_PASSWORD no .env e recrie o dashboard-ui."
  exit 0
fi

raiz=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
if ! docker compose exec -T pgbouncer /bin/sh -c 'test -f /tmp/pgbouncer/userlist.txt'; then
  echo "A senha do Postgres já mudou, mas o userlist do PgBouncer não foi reescrito." >&2
  echo "Suba o pgbouncer, atualize a senha no .env e rode este script de novo." >&2
  exit 1
fi

{
  cat "$raiz/pgbouncer-userlist.sh"
  echo 'pgbouncer_gravar_papel /tmp/pgbouncer/userlist.txt "$ROTATE_ROLE" "$ROTATE_PASSWORD"'
  echo 'kill -HUP 1'
} | docker compose exec -T \
  -e ROTATE_ROLE="$role" \
  -e ROTATE_PASSWORD="$password" \
  pgbouncer /bin/sh -s

echo "userlist do PgBouncer recarregado. Grave a mesma senha no .env antes de recriar o spy_pgbouncer."
