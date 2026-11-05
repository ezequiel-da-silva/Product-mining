-- Visão, reconsulta e o claim com reaper de integracao_evento.
-- atualizado_em existe para o beat recuperar em_envio órfão depois de 15 minutos.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS legenda_queimada TEXT,
    ADD COLUMN IF NOT EXISTS cortes_por_segundo NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS fingerprint_audio JSONB,
    ADD COLUMN IF NOT EXISTS proxima_consulta_em TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS fornecedor_revisado_em TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS fornecedor_status VARCHAR(20) DEFAULT 'ok';

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_fornecedor_status_check
        CHECK (fornecedor_status IN ('ok', 'reajuste', 'indisponivel'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS avaliacao_amostral (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    oferta_id BIGINT NOT NULL REFERENCES ofertas (id) ON DELETE CASCADE,
    nicho_extraido VARCHAR(100) NOT NULL,
    nicho_juiz VARCHAR(100),
    concorda BOOLEAN,
    motivo TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT avaliacao_amostral_unica UNIQUE (oferta_id)
);

ALTER TABLE integracao_evento
    ADD COLUMN IF NOT EXISTS disponivel_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN IF NOT EXISTS atualizado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE integracao_evento DROP CONSTRAINT IF EXISTS integracao_evento_estado_check;
ALTER TABLE integracao_evento ADD CONSTRAINT integracao_evento_estado_check
    CHECK (estado IN ('pendente', 'em_envio', 'enviado', 'esgotada'));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT SELECT, INSERT, DELETE ON TABLE avaliacao_amostral TO pipeline_writer;
        GRANT UPDATE (nicho_juiz, concorda, motivo) ON TABLE avaliacao_amostral TO pipeline_writer;
        GRANT UPDATE (
            legenda_queimada,
            cortes_por_segundo,
            fingerprint_audio,
            proxima_consulta_em,
            fornecedor_revisado_em,
            fornecedor_status
        ) ON TABLE ofertas TO pipeline_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'streamlit_readonly') THEN
        GRANT SELECT ON TABLE avaliacao_amostral TO streamlit_readonly;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lifecycle_writer') THEN
        GRANT SELECT, UPDATE (disponivel_em, atualizado_em, estado, enviado_em, tentativas, corpo)
            ON TABLE integracao_evento TO lifecycle_writer;
    END IF;
    IF to_regclass('public.avaliacao_amostral_id_seq') IS NOT NULL
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT USAGE, SELECT ON SEQUENCE avaliacao_amostral_id_seq TO pipeline_writer;
    END IF;
END $$;
