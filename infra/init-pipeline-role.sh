#!/bin/sh
set -eu

if [ -z "${PIPELINE_DB_PASSWORD:-}" ]; then
  echo "PIPELINE_DB_PASSWORD is required" >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v pipeline_password="$PIPELINE_DB_PASSWORD" \
  -v dbname="$POSTGRES_DB" <<'SQL'
SELECT set_config('app.pipeline_password', :'pipeline_password', false);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
    EXECUTE format(
      'CREATE USER pipeline_writer WITH PASSWORD %L',
      current_setting('app.pipeline_password')
    );
  ELSE
    EXECUTE format(
      'ALTER USER pipeline_writer WITH PASSWORD %L',
      current_setting('app.pipeline_password')
    );
  END IF;
END
$$;

GRANT CONNECT ON DATABASE :"dbname" TO pipeline_writer;
GRANT USAGE ON SCHEMA public TO pipeline_writer;
-- DELETE não é concedido: qualification/IA não apagam ofertas (ver lifecycle_writer).
-- UPDATE é por coluna: o papel não reescreve id, url, plataforma nem custo.
GRANT SELECT, INSERT ON TABLE ofertas TO pipeline_writer;
REVOKE UPDATE ON TABLE ofertas FROM pipeline_writer;
REVOKE DELETE ON TABLE ofertas FROM pipeline_writer;
GRANT UPDATE (
    vezes_avistado,
    data_ultima_captura,
    page_id,
    copy_texto,
    tipo_cta,
    url_video_r2,
    score,
    fadiga_dias,
    phash_criativo,
    phash_perceptual,
    transcricao_video,
    gancho_3s,
    nicho,
    sub_nicho,
    gatilho_mental,
    preco_oferta_estimado,
    preco_moeda,
    margem_estimada_pct,
    objecoes_quebradas,
    angulos_alternativos,
    embedding_analise,
    embedding_texto_hash,
    tokens_prompt_ia,
    tokens_saida_ia,
    dados_comerciais_ausentes,
    status_qualificacao,
    processamento_iniciado_em,
    processamento_token,
    falhas_ia,
    falhas_ia_em
) ON TABLE ofertas TO pipeline_writer;
SQL
