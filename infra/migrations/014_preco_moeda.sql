-- Preço só entra com moeda BRL ou USD. Linhas antigas ficam com moeda nula.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS preco_moeda VARCHAR(3);

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_preco_moeda_check
        CHECK (preco_moeda IS NULL OR preco_moeda IN ('BRL', 'USD'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT UPDATE (preco_moeda) ON TABLE ofertas TO pipeline_writer;
    END IF;
END $$;
