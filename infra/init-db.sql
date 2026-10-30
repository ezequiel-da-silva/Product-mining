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
    phash_perceptual BIT(64),
    url_video_r2 TEXT,

    -- Camada de Inteligência Artificial Multimodal
    transcricao_video TEXT,
    gancho_3s TEXT,
    nicho VARCHAR(100),
    sub_nicho VARCHAR(100),
    gatilho_mental VARCHAR(100),
    preco_oferta_estimado NUMERIC(10, 2),
    preco_moeda VARCHAR(3),
    margem_estimada_pct SMALLINT,
    catalogo_mais_vendidos JSONB,
    objecoes_quebradas JSONB,       -- Lista estruturada de objeções resolvidas
    angulos_alternativos JSONB,     -- 3 ângulos sugeridos pela IA para testes A/B
    custo_tokens_ia NUMERIC(14, 6),
    modelo_ia VARCHAR(80),
    versao_preco_ia VARCHAR(40),
    analise_concluida_em TIMESTAMPTZ,
    tokens_prompt_ia INTEGER,
    tokens_saida_ia INTEGER,
    embedding_analise vector(768),
    embedding_texto_hash VARCHAR(64),

    anuncios_ativos_pagina INTEGER DEFAULT 0,
    score SMALLINT DEFAULT 0,
    fadiga_dias SMALLINT NOT NULL DEFAULT 0,
    dados_comerciais_ausentes BOOLEAN NOT NULL DEFAULT FALSE,
    status_qualificacao VARCHAR(30) DEFAULT 'pendente' NOT NULL,
    processamento_iniciado_em TIMESTAMPTZ,
    processamento_token UUID,
    falhas_ia SMALLINT NOT NULL DEFAULT 0,
    falhas_ia_em TIMESTAMPTZ,
    CONSTRAINT ofertas_status_qualificacao_check
        CHECK (status_qualificacao IN ('pendente', 'pendente_lp', 'processando_ia', 'concluido')),
    CONSTRAINT ofertas_score_check
        CHECK (score BETWEEN 0 AND 100),
    CONSTRAINT ofertas_margem_estimada_pct_check
        CHECK (margem_estimada_pct IS NULL OR margem_estimada_pct BETWEEN 0 AND 100),
    CONSTRAINT ofertas_preco_oferta_estimado_check
        CHECK (preco_oferta_estimado IS NULL OR preco_oferta_estimado >= 0),
    CONSTRAINT ofertas_preco_moeda_check
        CHECK (preco_moeda IS NULL OR preco_moeda IN ('BRL', 'USD')),
    CONSTRAINT ofertas_fadiga_dias_check
        CHECK (fadiga_dias >= 0)
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
CREATE INDEX IF NOT EXISTS idx_ofertas_nicho ON ofertas (nicho);
CREATE INDEX IF NOT EXISTS idx_ofertas_concluidas_captura
    ON ofertas (data_ultima_captura DESC, id DESC)
    WHERE status_qualificacao = 'concluido';

CREATE TABLE IF NOT EXISTS triagem_oferta (
    oferta_id BIGINT PRIMARY KEY REFERENCES ofertas (id) ON DELETE CASCADE,
    estado VARCHAR(20) NOT NULL,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT triagem_oferta_estado_check
        CHECK (estado IN ('estrelada', 'descartada', 'testando'))
);

CREATE INDEX IF NOT EXISTS idx_ofertas_embedding_texto_hash
    ON ofertas (embedding_texto_hash)
    WHERE embedding_analise IS NOT NULL;

ALTER TABLE ofertas
    ALTER COLUMN status_qualificacao SET NOT NULL;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_status_qualificacao_check
        CHECK (status_qualificacao IN ('pendente', 'pendente_lp', 'processando_ia', 'concluido'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

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

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS preco_moeda VARCHAR(3);

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS phash_perceptual BIT(64);

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS fadiga_dias SMALLINT NOT NULL DEFAULT 0;

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
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_fadiga_dias_check
        CHECK (fadiga_dias >= 0);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

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
    ('007_falhas_ia'),
    ('008_falhas_ia_em'),
    ('009_revogar_delete_pipeline'),
    ('010_tokens_ia'),
    ('011_criativo_lp_score'),
    ('012_nicho_canonico'),
    ('013_privilegios_estreitos'),
    ('014_preco_moeda'),
    ('015_phash_perceptual'),
    ('016_fadiga_anuncio'),
    ('017_custo_modelo_e_vetor_mensal')
ON CONFLICT (versao) DO NOTHING;
