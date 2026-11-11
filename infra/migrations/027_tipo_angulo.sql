-- Arquétipo do criativo original. Linhas já analisadas ficam nulas.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS tipo_angulo TEXT;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_tipo_angulo_check
        CHECK (
            tipo_angulo IS NULL
            OR tipo_angulo IN ('dor_problema', 'demonstracao', 'prova_social_ugc', 'comparativo')
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT UPDATE (tipo_angulo) ON TABLE ofertas TO pipeline_writer;
    END IF;
END $$;
