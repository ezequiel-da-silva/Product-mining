-- Nicho descritivo para "Outros", índice da busca por nicho e escrita da triagem.
-- A chave de triagem_oferta é oferta_id (o painel informa o id no INSERT).
-- Não existe triagem_oferta_id_seq; o GRANT da sequence só roda se ela passar a existir.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS sub_nicho VARCHAR(100);

CREATE INDEX IF NOT EXISTS idx_ofertas_nicho ON ofertas (nicho);

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE triagem_oferta TO streamlit_readonly;

DO $$
BEGIN
    IF to_regclass('public.triagem_oferta_id_seq') IS NOT NULL THEN
        GRANT USAGE, SELECT ON SEQUENCE triagem_oferta_id_seq TO streamlit_readonly;
    END IF;
END $$;
