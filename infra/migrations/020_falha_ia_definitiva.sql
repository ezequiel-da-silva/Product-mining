-- Falha definitiva de IA sai do Redis e fica na tabela, que é o reprocesso oficial.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS falhas_ia_transporte SMALLINT NOT NULL DEFAULT 0;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS resposta_ia_bruta TEXT;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS resposta_ia_motivo VARCHAR(500);

CREATE TABLE IF NOT EXISTS falha_ia_definitiva (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    oferta_id BIGINT NOT NULL,
    video_url_r2 TEXT,
    ad_copy_texto TEXT,
    analise JSONB,
    uso_tokens JSONB,
    erro TEXT,
    classe VARCHAR(20) NOT NULL,
    estado VARCHAR(20) NOT NULL DEFAULT 'pendente',
    tentativas SMALLINT NOT NULL DEFAULT 0,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reprocessado_em TIMESTAMPTZ,
    CONSTRAINT falha_ia_definitiva_oferta_fk
        FOREIGN KEY (oferta_id) REFERENCES ofertas (id) ON DELETE CASCADE,
    CONSTRAINT falha_ia_definitiva_classe_check
        CHECK (classe IN ('transporte', 'estrutural')),
    CONSTRAINT falha_ia_definitiva_estado_check
        CHECK (estado IN ('pendente', 'em_reprocesso', 'resolvida', 'esgotada')),
    CONSTRAINT falha_ia_definitiva_tentativas_check
        CHECK (tentativas >= 0)
);

CREATE INDEX IF NOT EXISTS idx_falha_ia_definitiva_pendente
    ON falha_ia_definitiva (id)
    WHERE estado = 'pendente';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT UPDATE (
            falhas_ia_transporte,
            resposta_ia_bruta,
            resposta_ia_motivo
        ) ON TABLE ofertas TO pipeline_writer;
        GRANT SELECT, INSERT, UPDATE ON TABLE falha_ia_definitiva TO pipeline_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lifecycle_writer') THEN
        GRANT SELECT, UPDATE ON TABLE falha_ia_definitiva TO lifecycle_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'streamlit_readonly') THEN
        GRANT SELECT ON TABLE falha_ia_definitiva TO streamlit_readonly;
    END IF;
    IF to_regclass('public.falha_ia_definitiva_id_seq') IS NOT NULL
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT USAGE, SELECT ON SEQUENCE falha_ia_definitiva_id_seq TO pipeline_writer;
    END IF;
END $$;
