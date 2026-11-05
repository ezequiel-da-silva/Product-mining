-- Origem do preço da página. Bloqueio não conta como consulta encerrada.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS preco_origem VARCHAR(20);

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_preco_origem_check
        CHECK (
            preco_origem IS NULL
            OR preco_origem IN (
                'json_ld', 'shopify_json', 'html', 'bloqueado', 'divergente', 'ausente'
            )
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT UPDATE (preco_origem) ON TABLE ofertas TO pipeline_writer;
    END IF;
END $$;
