-- Custo real do fornecedor (AliExpress/1688) e depoimentos de 5 estrelas.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS url_imagem_criativo TEXT;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS cogs_estimado NUMERIC(10, 2);

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS cogs_moeda VARCHAR(3);

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS fornecedor_url TEXT;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS fornecedor_origem VARCHAR(20);

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS fornecedor_consultado_em TIMESTAMPTZ;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS provas_sociais JSONB;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_cogs_estimado_check
        CHECK (cogs_estimado IS NULL OR cogs_estimado >= 0);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_cogs_moeda_check
        CHECK (cogs_moeda IS NULL OR cogs_moeda IN ('USD', 'CNY'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_cogs_par_check
        CHECK ((cogs_estimado IS NULL) = (cogs_moeda IS NULL));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_fornecedor_origem_check
        CHECK (fornecedor_origem IS NULL OR fornecedor_origem IN ('1688', 'aliexpress'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_ofertas_sem_fornecedor
    ON ofertas (id)
    WHERE status_qualificacao = 'concluido'
      AND oferta_canonica_id IS NULL
      AND fornecedor_consultado_em IS NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT UPDATE (
            url_imagem_criativo,
            cogs_estimado,
            cogs_moeda,
            fornecedor_url,
            fornecedor_origem,
            fornecedor_consultado_em,
            provas_sociais
        ) ON TABLE ofertas TO pipeline_writer;
    END IF;
END $$;
