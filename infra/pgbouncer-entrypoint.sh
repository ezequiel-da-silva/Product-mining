#!/bin/sh
# O papel que conecta é pipeline_writer ou lifecycle_writer, não o superusuário.
# A imagem de um usuário só não autentica os dois. A rede dados é internal.
set -eu

diretorio=/tmp/pgbouncer
mkdir -p "$diretorio"
umask 077

db="${POSTGRES_DB:-mineracao_fb}"
: "${PIPELINE_DB_PASSWORD:?PIPELINE_DB_PASSWORD is required}"
: "${LIFECYCLE_DB_PASSWORD:?LIFECYCLE_DB_PASSWORD is required}"
# shellcheck disable=SC1091
. /opt/pgbouncer-userlist.sh
: > "$diretorio/userlist.txt"
pgbouncer_gravar_papel "$diretorio/userlist.txt" pipeline_writer "$PIPELINE_DB_PASSWORD"
pgbouncer_gravar_papel "$diretorio/userlist.txt" lifecycle_writer "$LIFECYCLE_DB_PASSWORD"

cat > "$diretorio/pgbouncer.ini" <<EOF
[databases]
${db} = host=postgres port=5432 dbname=${db}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = plain
auth_file = ${diretorio}/userlist.txt
pool_mode = transaction
max_client_conn = 400
default_pool_size = 20
server_reset_query = DISCARD ALL
ignore_startup_parameters = extra_float_digits
admin_users = pipeline_writer
stats_users = pipeline_writer
EOF

exec pgbouncer "$diretorio/pgbouncer.ini"
