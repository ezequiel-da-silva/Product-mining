-- Ativação da extensão vetorial
CREATE EXTENSION IF NOT EXISTS vector;

-- Tabela principal de ofertas validadas e séries temporais
CREATE TABLE IF NOT EXISTS ofertas (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    url_destino TEXT UNIQUE NOT NULL,
    page_id TEXT,
    data_primeira_captura TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    data_ultima_captura TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    vezes_avistado INTEGER DEFAULT 1,
    copy_texto TEXT,
    tipo_cta VARCHAR(50),
    engajamento_reacoes INTEGER DEFAULT 0,
    plataforma VARCHAR(50),
    gateway_pagamento VARCHAR(50),
    tipo_checkout VARCHAR(50),
    tem_pixel BOOLEAN DEFAULT FALSE,
    phash_criativo VARCHAR(64),
    url_video_r2 TEXT,

    -- Camada de Inteligência Artificial Multimodal
    transcricao_video TEXT,
    gancho_3s TEXT,
    nicho VARCHAR(100),
    gatilho_mental VARCHAR(100),
    preco_oferta_estimado NUMERIC(10, 2),
    margem_estimada_pct SMALLINT,
    catalogo_mais_vendidos JSONB,
    objecoes_quebradas JSONB,       -- Lista estruturada de objeções resolvidas
    angulos_alternativos JSONB,     -- 3 ângulos sugeridos pela IA para testes A/B
    custo_tokens_ia NUMERIC(8, 4) DEFAULT 0.0000,
    embedding_analise vector(768),
    embedding_texto_hash VARCHAR(64),

    anuncios_ativos_pagina INTEGER DEFAULT 0,
    score SMALLINT DEFAULT 0,
    status_qualificacao VARCHAR(30) DEFAULT 'pendente' NOT NULL,
    processamento_iniciado_em TIMESTAMPTZ,
    processamento_token UUID,
    falhas_ia SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT ofertas_status_qualificacao_check
        CHECK (status_qualificacao IN ('pendente', 'processando_ia', 'concluido')),
    CONSTRAINT ofertas_score_check
        CHECK (score BETWEEN 0 AND 120),
    CONSTRAINT ofertas_margem_estimada_pct_check
        CHECK (margem_estimada_pct IS NULL OR margem_estimada_pct BETWEEN 0 AND 100),
    CONSTRAINT ofertas_preco_oferta_estimado_check
        CHECK (preco_oferta_estimado IS NULL OR preco_oferta_estimado >= 0)
);

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS processamento_iniciado_em TIMESTAMPTZ;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS processamento_token UUID;

-- Índices B-Tree para consultas de alta frequência
CREATE INDEX IF NOT EXISTS idx_ofertas_score ON ofertas (score DESC);
CREATE INDEX IF NOT EXISTS idx_ofertas_status ON ofertas (status_qualificacao);
CREATE INDEX IF NOT EXISTS idx_ofertas_page_id ON ofertas (page_id);
CREATE INDEX IF NOT EXISTS idx_ofertas_phash ON ofertas (phash_criativo);
CREATE INDEX IF NOT EXISTS idx_ofertas_concluidas_captura
    ON ofertas (data_ultima_captura DESC, id DESC)
    WHERE status_qualificacao = 'concluido';

-- Índice HNSW calibrado para busca vetorial de alta precisão
CREATE INDEX IF NOT EXISTS idx_ofertas_embedding ON ofertas USING hnsw (embedding_analise vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_ofertas_embedding_texto_hash
    ON ofertas (embedding_texto_hash)
    WHERE embedding_analise IS NOT NULL;

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

CREATE TABLE IF NOT EXISTS schema_migrations (
    versao TEXT PRIMARY KEY,
    aplicada_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO schema_migrations (versao) VALUES
    ('001_processing_lease'),
    ('002_processing_token'),
    ('003_ofertas_constraints'),
    ('004_dashboard_pagination'),
    ('005_embedding_texto_hash'),
    ('006_ofertas_delete'),
    ('007_falhas_ia')
ON CONFLICT (versao) DO NOTHING;
