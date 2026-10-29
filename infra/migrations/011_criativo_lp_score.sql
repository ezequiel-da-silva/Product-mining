-- Separa a análise do criativo da qualificação comercial da página
-- e limita a pontuação composta a 100.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS dados_comerciais_ausentes BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE ofertas
SET score = LEAST(GREATEST(COALESCE(score, 0), 0), 100)
WHERE score IS NULL OR score < 0 OR score > 100;

ALTER TABLE ofertas DROP CONSTRAINT IF EXISTS ofertas_score_check;
ALTER TABLE ofertas DROP CONSTRAINT IF EXISTS ofertas_status_qualificacao_check;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_score_check
        CHECK (score BETWEEN 0 AND 100);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_status_qualificacao_check
        CHECK (status_qualificacao IN ('pendente', 'pendente_lp', 'processando_ia', 'concluido'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS triagem_oferta (
    oferta_id BIGINT PRIMARY KEY REFERENCES ofertas (id) ON DELETE CASCADE,
    estado VARCHAR(20) NOT NULL,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT triagem_oferta_estado_check
        CHECK (estado IN ('estrelada', 'descartada', 'testando'))
);

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE triagem_oferta TO streamlit_readonly;
