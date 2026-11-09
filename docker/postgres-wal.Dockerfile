# Imagem filha opcional. O serviço postgres do Compose continua na imagem pgvector.
# O primeiro archive_mode=on exige restart. Até lá, o backup é o pg_dump do volume.
FROM pgvector/pgvector:pg16

USER root
ADD https://github.com/wal-g/wal-g/releases/download/v3.0.7/wal-g-pg-ubuntu-20.04-amd64 /usr/local/bin/wal-g
RUN chmod 755 /usr/local/bin/wal-g \
    && wal-g --version

# wal_level = replica
# archive_mode = on
# archive_timeout = 60
# archive_command = 'wal-g wal-push %p'
# WALG_S3_PREFIX aponta para um bucket R2 separado, sem permissão de apagar.
# Backup diário: wal-g backup-push /var/lib/postgresql/data
# Restauração: wal-g backup-fetch num volume novo e recovery.signal
# com restore_command = 'wal-g wal-fetch %f %p'
