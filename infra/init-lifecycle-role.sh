#!/bin/sh
set -eu

if [ -z "${LIFECYCLE_DB_PASSWORD:-}" ]; then
  echo "LIFECYCLE_DB_PASSWORD is required" >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v lifecycle_password="$LIFECYCLE_DB_PASSWORD" \
  -v dbname="$POSTGRES_DB" <<'SQL'
SELECT set_config('app.lifecycle_password', :'lifecycle_password', false);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lifecycle_writer') THEN
    EXECUTE format(
      'CREATE USER lifecycle_writer WITH PASSWORD %L',
      current_setting('app.lifecycle_password')
    );
  ELSE
    EXECUTE format(
      'ALTER USER lifecycle_writer WITH PASSWORD %L',
      current_setting('app.lifecycle_password')
    );
  END IF;
END
$$;

GRANT CONNECT ON DATABASE :"dbname" TO lifecycle_writer;
GRANT USAGE ON SCHEMA public TO lifecycle_writer;
GRANT SELECT, DELETE ON TABLE ofertas TO lifecycle_writer;
GRANT UPDATE (processamento_token, score, fadiga_dias) ON TABLE ofertas TO lifecycle_writer;
GRANT UPDATE (
    venda_brl,
    cogs_brl,
    paridade_vigencia,
    paridade_fonte
) ON TABLE ofertas TO lifecycle_writer;

DO $$
BEGIN
  IF to_regprocedure('public.remover_oferta_vetor()') IS NOT NULL THEN
    GRANT EXECUTE ON FUNCTION remover_oferta_vetor() TO lifecycle_writer;
  END IF;
  IF to_regprocedure('public.mes_captura_oferta(timestamptz)') IS NOT NULL THEN
    GRANT EXECUTE ON FUNCTION mes_captura_oferta(TIMESTAMPTZ) TO lifecycle_writer;
  END IF;
  IF to_regprocedure('public.manter_particoes_oferta_vetor()') IS NOT NULL THEN
    GRANT EXECUTE ON FUNCTION manter_particoes_oferta_vetor() TO lifecycle_writer;
  END IF;
  IF to_regclass('public.falha_ia_definitiva') IS NOT NULL THEN
    GRANT SELECT, UPDATE ON TABLE falha_ia_definitiva TO lifecycle_writer;
  END IF;
  IF to_regclass('public.paridade_cambio') IS NOT NULL THEN
    GRANT SELECT, INSERT ON TABLE paridade_cambio TO lifecycle_writer;
  END IF;
  IF to_regclass('public.integracao_evento') IS NOT NULL THEN
    GRANT SELECT, UPDATE ON TABLE integracao_evento TO lifecycle_writer;
  END IF;
END $$;
SQL
