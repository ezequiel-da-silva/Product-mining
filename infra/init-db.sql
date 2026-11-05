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
    url_imagem_criativo TEXT,

    -- Camada de Inteligência Artificial Multimodal
    transcricao_video TEXT,
    gancho_3s TEXT,
    nicho VARCHAR(100),
    sub_nicho VARCHAR(100),
    gatilho_mental VARCHAR(100),
    preco_oferta_estimado NUMERIC(10, 2),
    preco_moeda VARCHAR(3),
    preco_pagina_consultado_em TIMESTAMPTZ,
    oferta_canonica_id BIGINT REFERENCES ofertas (id) ON DELETE SET NULL,
    margem_estimada_pct SMALLINT,
    cogs_estimado NUMERIC(10, 2),
    cogs_moeda VARCHAR(3),
    fornecedor_url TEXT,
    fornecedor_origem VARCHAR(20),
    fornecedor_consultado_em TIMESTAMPTZ,
    provas_sociais JSONB,
    legenda_queimada TEXT,
    cortes_por_segundo NUMERIC(6,3),
    fingerprint_audio JSONB,
    proxima_consulta_em TIMESTAMPTZ,
    fornecedor_revisado_em TIMESTAMPTZ,
    fornecedor_status VARCHAR(20) DEFAULT 'ok',
    catalogo_mais_vendidos JSONB,
    objecoes_quebradas JSONB,       -- Lista estruturada de objeções resolvidas
    angulos_alternativos JSONB,     -- um ângulo, gerado quando a oferta está em teste
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
    falhas_ia_transporte SMALLINT NOT NULL DEFAULT 0,
    resposta_ia_bruta TEXT,
    resposta_ia_motivo VARCHAR(500),
    preco_origem VARCHAR(20),
    venda_brl NUMERIC(12, 2),
    cogs_brl NUMERIC(12, 2),
    paridade_vigencia DATE,
    paridade_fonte VARCHAR(20),
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
    CONSTRAINT ofertas_cogs_estimado_check
        CHECK (cogs_estimado IS NULL OR cogs_estimado >= 0),
    CONSTRAINT ofertas_cogs_moeda_check
        CHECK (cogs_moeda IS NULL OR cogs_moeda IN ('USD', 'CNY')),
    CONSTRAINT ofertas_cogs_par_check
        CHECK ((cogs_estimado IS NULL) = (cogs_moeda IS NULL)),
    CONSTRAINT ofertas_fornecedor_origem_check
        CHECK (fornecedor_origem IS NULL OR fornecedor_origem IN ('1688', 'aliexpress')),
    CONSTRAINT ofertas_canonica_diferente_check
        CHECK (oferta_canonica_id IS NULL OR oferta_canonica_id <> id),
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
CREATE INDEX IF NOT EXISTS idx_ofertas_canonica
    ON ofertas (oferta_canonica_id)
    WHERE oferta_canonica_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ofertas_nicho ON ofertas (nicho);
CREATE INDEX IF NOT EXISTS idx_ofertas_concluidas_captura
    ON ofertas (data_ultima_captura DESC, id DESC)
    WHERE status_qualificacao = 'concluido';
CREATE INDEX IF NOT EXISTS idx_ofertas_sem_fornecedor
    ON ofertas (id)
    WHERE status_qualificacao = 'concluido'
      AND oferta_canonica_id IS NULL
      AND fornecedor_consultado_em IS NULL;

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

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS falhas_ia_transporte SMALLINT NOT NULL DEFAULT 0;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS resposta_ia_bruta TEXT;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS resposta_ia_motivo VARCHAR(500);

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS preco_origem VARCHAR(20);

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

CREATE TABLE IF NOT EXISTS falha_ia_definitiva (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    oferta_id BIGINT NOT NULL,
    video_url_r2 TEXT,
    ad_copy_texto TEXT,
    analise JSONB,
    uso_tokens JSONB,
    erro TEXT,
    classe VARCHAR(20) NOT NULL,
    estado VARCHAR(20) NOT NULL DEFAULT 'pendente',
    tentativas SMALLINT NOT NULL DEFAULT 0,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reprocessado_em TIMESTAMPTZ,
    CONSTRAINT falha_ia_definitiva_oferta_fk
        FOREIGN KEY (oferta_id) REFERENCES ofertas (id) ON DELETE CASCADE,
    CONSTRAINT falha_ia_definitiva_classe_check
        CHECK (classe IN ('transporte', 'estrutural')),
    CONSTRAINT falha_ia_definitiva_estado_check
        CHECK (estado IN ('pendente', 'em_reprocesso', 'resolvida', 'esgotada')),
    CONSTRAINT falha_ia_definitiva_tentativas_check
        CHECK (tentativas >= 0)
);

CREATE INDEX IF NOT EXISTS idx_falha_ia_definitiva_pendente
    ON falha_ia_definitiva (id)
    WHERE estado = 'pendente';

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

CREATE TABLE IF NOT EXISTS integracao_evento (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    disponivel_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    estado VARCHAR(20) NOT NULL DEFAULT 'pendente',
    corpo JSONB NOT NULL,
    tentativas SMALLINT NOT NULL DEFAULT 0,
    enviado_em TIMESTAMPTZ,
    CONSTRAINT integracao_evento_estado_check
        CHECK (estado IN ('pendente', 'em_envio', 'enviado', 'esgotada')),
    CONSTRAINT integracao_evento_tentativas_check
        CHECK (tentativas >= 0)
);

CREATE TABLE IF NOT EXISTS avaliacao_amostral (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    oferta_id BIGINT NOT NULL REFERENCES ofertas (id) ON DELETE CASCADE,
    nicho_extraido VARCHAR(100) NOT NULL,
    nicho_juiz VARCHAR(100),
    concorda BOOLEAN,
    motivo TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT avaliacao_amostral_unica UNIQUE (oferta_id)
);

CREATE INDEX IF NOT EXISTS idx_integracao_evento_pendente
    ON integracao_evento (id)
    WHERE estado = 'pendente';

CREATE OR REPLACE FUNCTION manter_particoes_oferta_vetor()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    inicio date;
    proximo date;
    nome text;
    particao regclass;
    relname text;
    mes date;
    corte date;
    idx text;
BEGIN
    -- A poda do painel usa o mesmo corte: meses com menos de 18 meses ficam indexados.
    corte := (
        date_trunc('month', (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo'))::date
        - INTERVAL '18 months'
    )::date;
    inicio := (
        date_trunc('month', (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo'))::date
        + INTERVAL '1 month'
    )::date;

    FOR passo IN 1..2 LOOP
        proximo := (inicio + INTERVAL '1 month')::date;
        nome := format('oferta_vetor_%s', to_char(inicio, 'YYYY_MM'));
        IF to_regclass(format('public.%I', nome)) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF oferta_vetor FOR VALUES FROM (%L) TO (%L)',
                nome,
                inicio,
                proximo
            );
        END IF;
        inicio := proximo;
    END LOOP;

    FOR particao IN
        SELECT inhrelid
        FROM pg_inherits
        WHERE inhparent = 'public.oferta_vetor'::regclass
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'streamlit_readonly') THEN
            EXECUTE format('GRANT SELECT ON TABLE %s TO streamlit_readonly', particao);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
            EXECUTE format('GRANT SELECT ON TABLE %s TO pipeline_writer', particao);
        END IF;
    END LOOP;

    -- O índice pai e os filhos nascem de novo na mesma transação: o commit não
    -- deixa a busca só com varredura sequencial.
    IF to_regclass('public.idx_oferta_vetor_embedding') IS NOT NULL THEN
        EXECUTE 'DROP INDEX IF EXISTS public.idx_oferta_vetor_embedding';
    END IF;

    FOR particao IN
        SELECT inhrelid
        FROM pg_inherits
        WHERE inhparent = 'public.oferta_vetor'::regclass
    LOOP
        relname := replace(particao::text, '"', '');
        IF position('.' in relname) > 0 THEN
            relname := split_part(relname, '.', 2);
        END IF;
        idx := format('idx_%s_hnsw', relname);
        IF relname ~ '^oferta_vetor_[0-9]{4}_[0-9]{2}$' THEN
            mes := to_date(replace(substring(relname from '[0-9]{4}_[0-9]{2}$'), '_', '-'), 'YYYY-MM');
            IF mes >= corte THEN
                IF to_regclass(format('public.%I', idx)) IS NULL THEN
                    EXECUTE format(
                        'CREATE INDEX %I ON %s USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)',
                        idx,
                        particao
                    );
                END IF;
            ELSE
                EXECUTE format('DROP INDEX IF EXISTS public.%I', idx);
            END IF;
        ELSE
            EXECUTE format('DROP INDEX IF EXISTS public.%I', idx);
        END IF;
    END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION manter_particoes_oferta_vetor() FROM PUBLIC;

DO $$
BEGIN
    ALTER TABLE ofertas
        ADD CONSTRAINT ofertas_fornecedor_status_check
        CHECK (fornecedor_status IN ('ok', 'reajuste', 'indisponivel'));
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
    ('017_custo_modelo_e_vetor_mensal'),
    ('018_preco_pagina_e_grupo'),
    ('019_cogs_fornecedor_e_provas'),
    ('020_falha_ia_definitiva'),
    ('021_preco_origem'),
    ('022_paridade_cambio'),
    ('023_integracao_evento'),
    ('024_particoes_oferta_vetor'),
    ('025_escala_visao_e_eventos')
ON CONFLICT (versao) DO NOTHING;
