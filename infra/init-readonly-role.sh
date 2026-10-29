#!/bin/sh
set -eu

if [ -z "${STREAMLIT_DB_PASSWORD:-}" ]; then
  echo "STREAMLIT_DB_PASSWORD is required" >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v readonly_password="$STREAMLIT_DB_PASSWORD" \
  -v dbname="$POSTGRES_DB" <<'SQL'
SELECT set_config('app.readonly_password', :'readonly_password', false);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'streamlit_readonly') THEN
    EXECUTE format(
      'CREATE USER streamlit_readonly WITH PASSWORD %L',
      current_setting('app.readonly_password')
    );
  ELSE
    EXECUTE format(
      'ALTER USER streamlit_readonly WITH PASSWORD %L',
      current_setting('app.readonly_password')
    );
  END IF;
END
$$;

GRANT CONNECT ON DATABASE :"dbname" TO streamlit_readonly;
GRANT USAGE ON SCHEMA public TO streamlit_readonly;
REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM streamlit_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM streamlit_readonly;

DO $$
BEGIN
  IF to_regclass('public.ofertas') IS NOT NULL THEN
    GRANT SELECT ON TABLE ofertas TO streamlit_readonly;
  END IF;
  IF to_regclass('public.triagem_oferta') IS NOT NULL THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE triagem_oferta TO streamlit_readonly;
  END IF;
  IF to_regclass('public.triagem_oferta_id_seq') IS NOT NULL THEN
    GRANT USAGE, SELECT ON SEQUENCE triagem_oferta_id_seq TO streamlit_readonly;
  END IF;
END $$;
SQL
