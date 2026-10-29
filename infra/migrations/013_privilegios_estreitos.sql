-- pipeline_writer só atualiza as colunas que os workers gravam.
-- streamlit_readonly lê ofertas e a triagem, sem herdar SELECT de tabela nova.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        REVOKE UPDATE ON TABLE ofertas FROM pipeline_writer;
        GRANT UPDATE (
            vezes_avistado,
            data_ultima_captura,
            page_id,
            copy_texto,
            tipo_cta,
            url_video_r2,
            score,
            phash_criativo,
            transcricao_video,
            gancho_3s,
            nicho,
            sub_nicho,
            gatilho_mental,
            preco_oferta_estimado,
            margem_estimada_pct,
            objecoes_quebradas,
            angulos_alternativos,
            embedding_analise,
            embedding_texto_hash,
            tokens_prompt_ia,
            tokens_saida_ia,
            dados_comerciais_ausentes,
            status_qualificacao,
            processamento_iniciado_em,
            processamento_token,
            falhas_ia,
            falhas_ia_em
        ) ON TABLE ofertas TO pipeline_writer;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'streamlit_readonly') THEN
        REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM streamlit_readonly;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM streamlit_readonly;
        GRANT SELECT ON TABLE ofertas TO streamlit_readonly;
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE triagem_oferta TO streamlit_readonly;
    END IF;
END $$;
