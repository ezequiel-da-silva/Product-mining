-- O painel só grava a intenção. O beat envia e assina. Não há coluna de usuário.

CREATE TABLE IF NOT EXISTS integracao_evento (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    estado VARCHAR(20) NOT NULL DEFAULT 'pendente',
    corpo JSONB NOT NULL,
    tentativas SMALLINT NOT NULL DEFAULT 0,
    enviado_em TIMESTAMPTZ,
    CONSTRAINT integracao_evento_estado_check
        CHECK (estado IN ('pendente', 'enviado', 'esgotada')),
    CONSTRAINT integracao_evento_tentativas_check
        CHECK (tentativas >= 0)
);

CREATE INDEX IF NOT EXISTS idx_integracao_evento_pendente
    ON integracao_evento (id)
    WHERE estado = 'pendente';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'streamlit_readonly') THEN
        GRANT INSERT ON TABLE integracao_evento TO streamlit_readonly;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lifecycle_writer') THEN
        GRANT SELECT, UPDATE ON TABLE integracao_evento TO lifecycle_writer;
    END IF;
    IF to_regclass('public.integracao_evento_id_seq') IS NOT NULL
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'streamlit_readonly') THEN
        GRANT USAGE, SELECT ON SEQUENCE integracao_evento_id_seq TO streamlit_readonly;
    END IF;
END $$;
