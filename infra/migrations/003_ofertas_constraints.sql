UPDATE ofertas
SET status_qualificacao = 'pendente'
WHERE status_qualificacao IS NULL
   OR status_qualificacao NOT IN ('pendente', 'processando_ia', 'concluido');

UPDATE ofertas
SET score = LEAST(GREATEST(COALESCE(score, 0), 0), 120);

UPDATE ofertas
SET margem_estimada_pct = LEAST(GREATEST(margem_estimada_pct, 0), 100)
WHERE margem_estimada_pct IS NOT NULL
  AND (margem_estimada_pct < 0 OR margem_estimada_pct > 100);

UPDATE ofertas
SET preco_oferta_estimado = 0
WHERE preco_oferta_estimado < 0;

ALTER TABLE ofertas
    ALTER COLUMN status_qualificacao SET NOT NULL;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_status_qualificacao_check
        CHECK (status_qualificacao IN ('pendente', 'processando_ia', 'concluido'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_score_check
        CHECK (score BETWEEN 0 AND 120);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_margem_estimada_pct_check
        CHECK (margem_estimada_pct IS NULL OR margem_estimada_pct BETWEEN 0 AND 100);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_preco_oferta_estimado_check
        CHECK (preco_oferta_estimado IS NULL OR preco_oferta_estimado >= 0);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
