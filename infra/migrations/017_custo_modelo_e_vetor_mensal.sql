-- Preço versionado por modelo, custo gravado na oferta e HNSW só nas concluídas.
-- oferta_vetor é mensal. Pendente, em processamento e expirada ficam fora do índice.

ALTER TABLE ofertas
    ALTER COLUMN custo_tokens_ia TYPE NUMERIC(14, 6);

ALTER TABLE ofertas
    ALTER COLUMN custo_tokens_ia DROP DEFAULT;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS modelo_ia VARCHAR(80);

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS versao_preco_ia VARCHAR(40);

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS analise_concluida_em TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS preco_modelo_ia (
    modelo VARCHAR(80) NOT NULL,
    versao VARCHAR(40) NOT NULL,
    usd_por_milhao_prompt NUMERIC(12, 6) NOT NULL,
    usd_por_milhao_saida NUMERIC(12, 6) NOT NULL,
    vigente_desde TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (modelo, versao),
    CONSTRAINT preco_modelo_ia_valores_check CHECK (
        usd_por_milhao_prompt >= 0
        AND usd_por_milhao_saida >= 0
    )
);

INSERT INTO preco_modelo_ia (
    modelo,
    versao,
    usd_por_milhao_prompt,
    usd_por_milhao_saida,
    vigente_desde
) VALUES
    ('gemini-3.8-flash', '2026-10', 0.300000, 2.500000, TIMESTAMPTZ '2026-10-01 00:00:00+00'),
    ('gemini-embedding-2', '2026-10', 0.150000, 0.000000, TIMESTAMPTZ '2026-10-01 00:00:00+00')
ON CONFLICT (modelo, versao) DO NOTHING;

CREATE TABLE IF NOT EXISTS oferta_vetor (
    mes_captura DATE NOT NULL,
    oferta_id BIGINT NOT NULL,
    embedding vector(768) NOT NULL,
    PRIMARY KEY (mes_captura, oferta_id),
    CONSTRAINT oferta_vetor_oferta_fk
        FOREIGN KEY (oferta_id) REFERENCES ofertas (id) ON DELETE RESTRICT
) PARTITION BY RANGE (mes_captura);

DO $$
DECLARE
    inicio date := DATE '2024-01-01';
    proximo date;
    nome text;
BEGIN
    WHILE inicio < DATE '2028-01-01' LOOP
        proximo := (inicio + INTERVAL '1 month')::date;
        nome := format('oferta_vetor_%s', to_char(inicio, 'YYYY_MM'));
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF oferta_vetor FOR VALUES FROM (%L) TO (%L)',
            nome,
            inicio,
            proximo
        );
        inicio := proximo;
    END LOOP;
END $$;

CREATE TABLE IF NOT EXISTS oferta_vetor_default PARTITION OF oferta_vetor DEFAULT;

CREATE INDEX IF NOT EXISTS idx_oferta_vetor_oferta
    ON oferta_vetor (oferta_id);

CREATE INDEX IF NOT EXISTS idx_oferta_vetor_embedding
    ON oferta_vetor USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

DROP INDEX IF EXISTS idx_ofertas_embedding;

CREATE INDEX IF NOT EXISTS idx_ofertas_custo_analise
    ON ofertas (analise_concluida_em)
    WHERE custo_tokens_ia IS NOT NULL;

CREATE OR REPLACE FUNCTION mes_captura_oferta(captura TIMESTAMPTZ)
RETURNS date
LANGUAGE sql
STABLE
AS $$
    SELECT date_trunc(
        'month',
        COALESCE(captura, CURRENT_TIMESTAMP) AT TIME ZONE 'America/Sao_Paulo'
    )::date;
$$;

CREATE OR REPLACE FUNCTION sincronizar_oferta_vetor()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    mes date;
BEGIN
    IF NEW.status_qualificacao = 'concluido' AND NEW.embedding_analise IS NOT NULL THEN
        mes := mes_captura_oferta(NEW.data_primeira_captura);
        DELETE FROM oferta_vetor
        WHERE oferta_id = NEW.id
          AND mes_captura <> mes;
        INSERT INTO oferta_vetor (mes_captura, oferta_id, embedding)
        VALUES (mes, NEW.id, NEW.embedding_analise)
        ON CONFLICT (mes_captura, oferta_id) DO UPDATE
        SET embedding = EXCLUDED.embedding;
    ELSE
        DELETE FROM oferta_vetor WHERE oferta_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION remover_oferta_vetor()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    DELETE FROM oferta_vetor WHERE oferta_id = OLD.id;
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_ofertas_sincronizar_vetor ON ofertas;
CREATE TRIGGER trg_ofertas_sincronizar_vetor
    AFTER INSERT OR UPDATE OF status_qualificacao, embedding_analise, data_primeira_captura
    ON ofertas
    FOR EACH ROW
    EXECUTE FUNCTION sincronizar_oferta_vetor();

DROP TRIGGER IF EXISTS trg_ofertas_remover_vetor ON ofertas;
CREATE TRIGGER trg_ofertas_remover_vetor
    BEFORE DELETE ON ofertas
    FOR EACH ROW
    EXECUTE FUNCTION remover_oferta_vetor();

REVOKE ALL ON FUNCTION sincronizar_oferta_vetor() FROM PUBLIC;
REVOKE ALL ON FUNCTION remover_oferta_vetor() FROM PUBLIC;
REVOKE ALL ON FUNCTION mes_captura_oferta(timestamptz) FROM PUBLIC;

INSERT INTO oferta_vetor (mes_captura, oferta_id, embedding)
SELECT mes_captura_oferta(data_primeira_captura), id, embedding_analise
FROM ofertas
WHERE status_qualificacao = 'concluido'
  AND embedding_analise IS NOT NULL
ON CONFLICT (mes_captura, oferta_id) DO UPDATE
SET embedding = EXCLUDED.embedding;

DELETE FROM oferta_vetor AS vetor
WHERE NOT EXISTS (
    SELECT 1
    FROM ofertas
    WHERE ofertas.id = vetor.oferta_id
      AND ofertas.status_qualificacao = 'concluido'
      AND ofertas.embedding_analise IS NOT NULL
);

DO $$
DECLARE
    particao regclass;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT UPDATE (
            custo_tokens_ia,
            modelo_ia,
            versao_preco_ia,
            analise_concluida_em
        ) ON TABLE ofertas TO pipeline_writer;
        GRANT SELECT ON TABLE preco_modelo_ia TO pipeline_writer;
        GRANT EXECUTE ON FUNCTION sincronizar_oferta_vetor() TO pipeline_writer;
        GRANT EXECUTE ON FUNCTION mes_captura_oferta(TIMESTAMPTZ) TO pipeline_writer;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lifecycle_writer') THEN
        GRANT EXECUTE ON FUNCTION remover_oferta_vetor() TO lifecycle_writer;
        GRANT EXECUTE ON FUNCTION mes_captura_oferta(TIMESTAMPTZ) TO lifecycle_writer;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'streamlit_readonly') THEN
        GRANT SELECT ON TABLE preco_modelo_ia TO streamlit_readonly;
        GRANT SELECT ON TABLE oferta_vetor TO streamlit_readonly;
        FOR particao IN
            SELECT inhrelid
            FROM pg_inherits
            WHERE inhparent = 'public.oferta_vetor'::regclass
        LOOP
            EXECUTE format('GRANT SELECT ON TABLE %s TO streamlit_readonly', particao);
        END LOOP;
    END IF;
END $$;
