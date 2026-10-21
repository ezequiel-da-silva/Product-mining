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
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE ofertas TO pipeline_writer;
SQL
