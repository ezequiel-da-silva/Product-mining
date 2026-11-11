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
-- UPDATE é por coluna: o papel não reescreve id, url nem plataforma.
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
    tipo_angulo,
    preco_oferta_estimado,
    preco_moeda,
    preco_pagina_consultado_em,
    oferta_canonica_id,
    url_imagem_criativo,
    cogs_estimado,
    cogs_moeda,
    fornecedor_url,
    fornecedor_origem,
    fornecedor_consultado_em,
    provas_sociais,
    margem_estimada_pct,
    objecoes_quebradas,
    angulos_alternativos,
    embedding_analise,
    embedding_texto_hash,
    custo_tokens_ia,
    modelo_ia,
    versao_preco_ia,
    analise_concluida_em,
    tokens_prompt_ia,
    tokens_saida_ia,
    dados_comerciais_ausentes,
    status_qualificacao,
    processamento_iniciado_em,
    processamento_token,
    falhas_ia,
    falhas_ia_em,
    falhas_ia_transporte,
    resposta_ia_bruta,
    resposta_ia_motivo,
    preco_origem,
    venda_brl,
    cogs_brl,
    paridade_vigencia,
    paridade_fonte,
    legenda_queimada,
    cortes_por_segundo,
    fingerprint_audio,
    proxima_consulta_em,
    fornecedor_revisado_em,
    fornecedor_status,
    frete_estimado_brl,
    imposto_estimado_brl,
    margem_liquida_brl,
    tem_order_bump,
    preco_bump
) ON TABLE ofertas TO pipeline_writer;

DO $$
BEGIN
  IF to_regclass('public.preco_modelo_ia') IS NOT NULL THEN
    GRANT SELECT ON TABLE preco_modelo_ia TO pipeline_writer;
  END IF;
  IF to_regprocedure('public.sincronizar_oferta_vetor()') IS NOT NULL THEN
    GRANT EXECUTE ON FUNCTION sincronizar_oferta_vetor() TO pipeline_writer;
  END IF;
  IF to_regprocedure('public.mes_captura_oferta(timestamptz)') IS NOT NULL THEN
    GRANT EXECUTE ON FUNCTION mes_captura_oferta(TIMESTAMPTZ) TO pipeline_writer;
  END IF;
  IF to_regclass('public.triagem_oferta') IS NOT NULL THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE triagem_oferta TO pipeline_writer;
  END IF;
  IF to_regclass('public.oferta_vetor') IS NOT NULL THEN
    GRANT SELECT ON TABLE oferta_vetor TO pipeline_writer;
  END IF;
  IF to_regclass('public.falha_ia_definitiva') IS NOT NULL THEN
    GRANT SELECT, INSERT, UPDATE ON TABLE falha_ia_definitiva TO pipeline_writer;
  END IF;
  IF to_regclass('public.falha_ia_definitiva_id_seq') IS NOT NULL THEN
    GRANT USAGE, SELECT ON SEQUENCE falha_ia_definitiva_id_seq TO pipeline_writer;
  END IF;
  IF to_regclass('public.paridade_cambio') IS NOT NULL THEN
    GRANT SELECT ON TABLE paridade_cambio TO pipeline_writer;
  END IF;
  IF to_regclass('public.avaliacao_amostral') IS NOT NULL THEN
    GRANT SELECT, INSERT, DELETE ON TABLE avaliacao_amostral TO pipeline_writer;
    GRANT UPDATE (nicho_juiz, concorda, motivo) ON TABLE avaliacao_amostral TO pipeline_writer;
  END IF;
  IF to_regclass('public.avaliacao_amostral_id_seq') IS NOT NULL THEN
    GRANT USAGE, SELECT ON SEQUENCE avaliacao_amostral_id_seq TO pipeline_writer;
  END IF;
  IF to_regclass('public.parametro_viabilidade') IS NOT NULL THEN
    GRANT SELECT ON TABLE parametro_viabilidade TO pipeline_writer;
  END IF;
  IF to_regclass('public.faixa_frete_nicho') IS NOT NULL THEN
    GRANT SELECT ON TABLE faixa_frete_nicho TO pipeline_writer;
  END IF;
  IF to_regclass('public.loja_pixel') IS NOT NULL THEN
    GRANT SELECT, INSERT ON TABLE loja_pixel TO pipeline_writer;
  END IF;
  IF to_regclass('public.variacao_copy') IS NOT NULL THEN
    GRANT SELECT, INSERT ON TABLE variacao_copy TO pipeline_writer;
  END IF;
  IF to_regclass('public.variacao_copy_id_seq') IS NOT NULL THEN
    GRANT USAGE, SELECT ON SEQUENCE variacao_copy_id_seq TO pipeline_writer;
  END IF;
END $$;

DO $$
DECLARE
  particao regclass;
BEGIN
  IF to_regclass('public.oferta_vetor') IS NULL THEN
    RETURN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
    RETURN;
  END IF;
  FOR particao IN
    SELECT inhrelid
    FROM pg_inherits
    WHERE inhparent = 'public.oferta_vetor'::regclass
  LOOP
    EXECUTE format('GRANT SELECT ON TABLE %s TO pipeline_writer', particao);
  END LOOP;
END $$;
SQL
