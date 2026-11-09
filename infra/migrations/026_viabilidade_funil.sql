-- Custo de aterrissagem, pixel compartilhado e variações de copy.
-- Alíquotas de exemplo: o operador troca a linha quando a regra mudar.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS frete_estimado_brl NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS imposto_estimado_brl NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS margem_liquida_brl NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS tem_order_bump BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS preco_bump NUMERIC(10, 2);

ALTER TABLE ofertas DROP CONSTRAINT IF EXISTS ofertas_status_qualificacao_check;
ALTER TABLE ofertas ADD CONSTRAINT ofertas_status_qualificacao_check
    CHECK (status_qualificacao IN (
        'pendente', 'pendente_lp', 'processando_ia', 'concluido', 'descartado_local'
    ));

CREATE TABLE IF NOT EXISTS parametro_viabilidade (
    nome VARCHAR(40) PRIMARY KEY,
    valor NUMERIC(12, 6) NOT NULL,
    vigente_em DATE NOT NULL DEFAULT CURRENT_DATE
);

INSERT INTO parametro_viabilidade (nome, valor) VALUES
    ('ii', 0.20),
    ('icms', 0.17),
    ('gateway_percentual', 0.0499),
    ('gateway_fixo_brl', 1.00),
    ('chargeback', 0.01)
ON CONFLICT (nome) DO NOTHING;

CREATE TABLE IF NOT EXISTS faixa_frete_nicho (
    nicho VARCHAR(100) PRIMARY KEY,
    peso_gramas INTEGER NOT NULL CHECK (peso_gramas > 0),
    frete_brl NUMERIC(10, 2) NOT NULL CHECK (frete_brl >= 0)
);

CREATE TABLE IF NOT EXISTS loja_pixel (
    pixel_id VARCHAR(20) NOT NULL,
    dominio TEXT NOT NULL,
    visto_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (pixel_id, dominio)
);

CREATE TABLE IF NOT EXISTS variacao_copy (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    oferta_id BIGINT NOT NULL REFERENCES ofertas (id) ON DELETE CASCADE,
    peca VARCHAR(20) NOT NULL,
    texto TEXT NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT variacao_copy_peca_check CHECK (peca IN ('headline', 'ugc', 'stories')),
    CONSTRAINT variacao_copy_unica UNIQUE (oferta_id, peca)
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT UPDATE (
            frete_estimado_brl,
            imposto_estimado_brl,
            margem_liquida_brl,
            tem_order_bump,
            preco_bump,
            status_qualificacao
        ) ON TABLE ofertas TO pipeline_writer;
        GRANT SELECT ON TABLE parametro_viabilidade TO pipeline_writer;
        GRANT SELECT ON TABLE faixa_frete_nicho TO pipeline_writer;
        GRANT SELECT, INSERT ON TABLE loja_pixel TO pipeline_writer;
        GRANT SELECT, INSERT ON TABLE variacao_copy TO pipeline_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lifecycle_writer') THEN
        GRANT UPDATE (
            frete_estimado_brl,
            imposto_estimado_brl,
            margem_liquida_brl
        ) ON TABLE ofertas TO lifecycle_writer;
        GRANT SELECT ON TABLE parametro_viabilidade TO lifecycle_writer;
        GRANT SELECT ON TABLE faixa_frete_nicho TO lifecycle_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'streamlit_readonly') THEN
        GRANT SELECT ON TABLE parametro_viabilidade TO streamlit_readonly;
        GRANT SELECT ON TABLE faixa_frete_nicho TO streamlit_readonly;
        GRANT SELECT ON TABLE loja_pixel TO streamlit_readonly;
        GRANT SELECT ON TABLE variacao_copy TO streamlit_readonly;
    END IF;
    IF to_regclass('public.variacao_copy_id_seq') IS NOT NULL
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT USAGE, SELECT ON SEQUENCE variacao_copy_id_seq TO pipeline_writer;
    END IF;
END $$;
