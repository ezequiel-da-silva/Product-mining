-- pipeline_writer (qualification + IA) não deve mais apagar linhas de ofertas;
-- apenas lifecycle_writer (celery-beat) tem DELETE, via init-lifecycle-role.sh.
REVOKE DELETE ON TABLE ofertas FROM pipeline_writer;
