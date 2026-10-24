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
GRANT UPDATE (processamento_token) ON TABLE ofertas TO lifecycle_writer;
SQL
