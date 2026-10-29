#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

user="${POSTGRES_USER:-spy_admin}"
db="${POSTGRES_DB:-mineracao_fb}"

psql_exec() {
  docker compose exec -T postgres \
    psql -v ON_ERROR_STOP=1 \
    --username "$user" \
    --dbname "$db"
}

psql_exec <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    versao TEXT PRIMARY KEY,
    aplicada_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
SQL

for file in \
  infra/migrations/001_processing_lease.sql \
  infra/migrations/002_processing_token.sql \
  infra/migrations/003_ofertas_constraints.sql \
  infra/migrations/004_dashboard_pagination.sql \
  infra/migrations/005_embedding_texto_hash.sql \
  infra/migrations/006_ofertas_delete.sql \
  infra/migrations/007_falhas_ia.sql \
  infra/migrations/008_falhas_ia_em.sql \
  infra/migrations/009_revogar_delete_pipeline.sql \
  infra/migrations/010_tokens_ia.sql \
  infra/migrations/011_criativo_lp_score.sql \
  infra/migrations/012_nicho_canonico.sql \
  infra/migrations/013_privilegios_estreitos.sql
do
  versao=$(basename "$file" .sql)
  applied=$(
    docker compose exec -T postgres \
      psql -tA -v ON_ERROR_STOP=1 \
      --username "$user" \
      --dbname "$db" \
      -c "SELECT 1 FROM schema_migrations WHERE versao = '${versao}'"
  )
  applied=$(printf '%s' "$applied" | tr -d '[:space:]')
  if [ "$applied" = "1" ]; then
    echo "já aplicada: $versao"
    continue
  fi

  {
    printf 'BEGIN;\n'
    printf "SET LOCAL lock_timeout = '%s';\n" "${MIGRATION_LOCK_TIMEOUT:-5s}"
    cat "$file"
    printf "INSERT INTO schema_migrations (versao) VALUES ('%s');\n" "$versao"
    printf 'COMMIT;\n'
  } | psql_exec
  echo "aplicada: $versao"
done
