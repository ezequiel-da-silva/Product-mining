-- Preço lido na página de destino e grupo de criativos do mesmo produto.
-- A canônica fica com oferta_canonica_id nulo. As irmãs apontam para ela.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS preco_pagina_consultado_em TIMESTAMPTZ;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS oferta_canonica_id BIGINT;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_oferta_canonica_id_fkey
        FOREIGN KEY (oferta_canonica_id) REFERENCES ofertas (id) ON DELETE SET NULL;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_canonica_diferente_check
        CHECK (oferta_canonica_id IS NULL OR oferta_canonica_id <> id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_ofertas_canonica
    ON ofertas (oferta_canonica_id)
    WHERE oferta_canonica_id IS NOT NULL;

DO $$
DECLARE
    particao regclass;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT UPDATE (
            preco_pagina_consultado_em,
            oferta_canonica_id
        ) ON TABLE ofertas TO pipeline_writer;
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE triagem_oferta TO pipeline_writer;
        IF to_regclass('public.oferta_vetor') IS NOT NULL THEN
            GRANT SELECT ON TABLE oferta_vetor TO pipeline_writer;
            FOR particao IN
                SELECT inhrelid
                FROM pg_inherits
                WHERE inhparent = 'public.oferta_vetor'::regclass
            LOOP
                EXECUTE format('GRANT SELECT ON TABLE %s TO pipeline_writer', particao);
            END LOOP;
        END IF;
    END IF;
END $$;
