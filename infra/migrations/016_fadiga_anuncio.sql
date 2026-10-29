-- fadiga_dias guarda quantos dias de queda já foram aplicados.
-- Um novo avistamento devolve esses pontos e zera o contador.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS fadiga_dias SMALLINT NOT NULL DEFAULT 0;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_fadiga_dias_check
        CHECK (fadiga_dias >= 0);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT UPDATE (fadiga_dias) ON TABLE ofertas TO pipeline_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lifecycle_writer') THEN
        GRANT UPDATE (processamento_token, score, fadiga_dias) ON TABLE ofertas TO lifecycle_writer;
    END IF;
END $$;
