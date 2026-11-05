-- Paridade do dia para margem em BRL. Não reescreve preço nem COGS de origem.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS venda_brl NUMERIC(12, 2);

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS cogs_brl NUMERIC(12, 2);

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS paridade_vigencia DATE;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS paridade_fonte VARCHAR(20);

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_paridade_fonte_check
        CHECK (paridade_fonte IS NULL OR paridade_fonte IN ('bacen', 'awesomeapi'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_venda_brl_check
        CHECK (venda_brl IS NULL OR venda_brl >= 0);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_cogs_brl_check
        CHECK (cogs_brl IS NULL OR cogs_brl >= 0);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS paridade_cambio (
    moeda_origem VARCHAR(3) NOT NULL,
    moeda_destino VARCHAR(3) NOT NULL DEFAULT 'BRL',
    taxa NUMERIC(18, 8) NOT NULL,
    fonte VARCHAR(20) NOT NULL,
    vigencia DATE NOT NULL,
    PRIMARY KEY (moeda_origem, moeda_destino, vigencia, fonte),
    CONSTRAINT paridade_cambio_par_check CHECK (
        moeda_destino = 'BRL' AND moeda_origem IN ('USD', 'CNY')
    ),
    CONSTRAINT paridade_cambio_fonte_check CHECK (fonte IN ('bacen', 'awesomeapi')),
    CONSTRAINT paridade_cambio_taxa_check CHECK (taxa > 0)
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT UPDATE (
            venda_brl,
            cogs_brl,
            paridade_vigencia,
            paridade_fonte
        ) ON TABLE ofertas TO pipeline_writer;
        GRANT SELECT ON TABLE paridade_cambio TO pipeline_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lifecycle_writer') THEN
        GRANT INSERT ON TABLE paridade_cambio TO lifecycle_writer;
        GRANT SELECT ON TABLE paridade_cambio TO lifecycle_writer;
        GRANT UPDATE (
            venda_brl,
            cogs_brl,
            paridade_vigencia,
            paridade_fonte
        ) ON TABLE ofertas TO lifecycle_writer;
    END IF;
END $$;
