#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

user="${POSTGRES_USER:-spy_admin}"
db="${POSTGRES_DB:-mineracao_fb}"

# No bash do Windows, o docker do Linux não fala com o Rancher e rejeita o -T.
# O docker.exe é o cliente que enxerga os containers.
docker_bin=docker
if ! docker compose version >/dev/null 2>&1; then
  if command -v docker.exe >/dev/null 2>&1; then
    docker_bin=docker.exe
  fi
fi

psql_exec() {
  "$docker_bin" compose exec -T postgres \
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
  infra/migrations/013_privilegios_estreitos.sql \
  infra/migrations/014_preco_moeda.sql \
  infra/migrations/015_phash_perceptual.sql \
  infra/migrations/016_fadiga_anuncio.sql \
  infra/migrations/017_custo_modelo_e_vetor_mensal.sql \
  infra/migrations/018_preco_pagina_e_grupo.sql \
  infra/migrations/019_cogs_fornecedor_e_provas.sql \
  infra/migrations/020_falha_ia_definitiva.sql \
  infra/migrations/021_preco_origem.sql \
  infra/migrations/022_paridade_cambio.sql \
  infra/migrations/023_integracao_evento.sql \
  infra/migrations/024_particoes_oferta_vetor.sql \
  infra/migrations/025_escala_visao_e_eventos.sql \
  infra/migrations/026_viabilidade_funil.sql
do
  versao=$(basename "$file" .sql)
  applied=$(
    "$docker_bin" compose exec -T postgres \
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
