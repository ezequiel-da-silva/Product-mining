from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def test_painel_recebe_dml_na_triagem_e_na_sequence_se_existir():
    migracao = (RAIZ / "infra/migrations/012_nicho_canonico.sql").read_text(encoding="utf-8")
    papel = (RAIZ / "infra/init-readonly-role.sh").read_text(encoding="utf-8")
    concessao = "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE triagem_oferta TO streamlit_readonly"
    sequence = "GRANT USAGE, SELECT ON SEQUENCE triagem_oferta_id_seq TO streamlit_readonly"

    assert concessao in migracao
    assert concessao in papel
    assert sequence in migracao
    assert sequence in papel
    assert "to_regclass('public.triagem_oferta_id_seq')" in migracao
    assert "idx_ofertas_nicho" in migracao
    assert "sub_nicho" in migracao


def _bloco_update(sql: str) -> str:
    inicio = sql.index("GRANT UPDATE (")
    fim = sql.index(") ON TABLE ofertas TO pipeline_writer", inicio)
    return sql[inicio:fim]


def test_pipeline_atualiza_por_coluna_e_painel_le_tabela_a_tabela():
    pipeline = (RAIZ / "infra/init-pipeline-role.sh").read_text(encoding="utf-8")
    painel = (RAIZ / "infra/init-readonly-role.sh").read_text(encoding="utf-8")
    migracao = (RAIZ / "infra/migrations/013_privilegios_estreitos.sql").read_text(encoding="utf-8")

    for texto in (pipeline, migracao):
        bloco = _bloco_update(texto)
        assert "REVOKE UPDATE ON TABLE ofertas FROM pipeline_writer" in texto
        for coluna in (
            "url_video_r2",
            "preco_oferta_estimado",
            "tokens_saida_ia",
            "falhas_ia_em",
            "processamento_token",
            "angulos_alternativos",
        ):
            assert coluna in bloco
        for proibida in ("plataforma", "engajamento_reacoes", "url_destino"):
            assert proibida not in bloco

    bloco_antigo = _bloco_update(migracao)
    assert "custo_tokens_ia" not in bloco_antigo
    bloco_pipeline = _bloco_update(pipeline)
    for coluna in ("custo_tokens_ia", "modelo_ia", "versao_preco_ia", "analise_concluida_em"):
        assert coluna in bloco_pipeline
    mlops = (RAIZ / "infra/migrations/017_custo_modelo_e_vetor_mensal.sql").read_text(encoding="utf-8")
    assert "GRANT UPDATE (" in mlops
    assert "custo_tokens_ia" in mlops
    assert "PARTITION BY RANGE (mes_captura)" in mlops
    assert "DROP INDEX IF EXISTS idx_ofertas_embedding" in mlops
    assert "idx_oferta_vetor_embedding" in mlops
    assert "GRANT SELECT ON TABLE oferta_vetor TO streamlit_readonly" in mlops
    assert "GRANT SELECT ON TABLE oferta_vetor TO streamlit_readonly" in painel
    assert "GRANT SELECT ON TABLE preco_modelo_ia TO pipeline_writer" in pipeline
    init_db = (RAIZ / "infra/init-db.sql").read_text(encoding="utf-8")
    assert "USING hnsw (embedding_analise" not in init_db
    assert "USING hnsw (embedding vector_cosine_ops)" in init_db
    assert "PARTITION BY RANGE (mes_captura)" in init_db

    assert "GRANT SELECT, INSERT, UPDATE ON TABLE ofertas" not in pipeline
    assert "REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM streamlit_readonly" in painel
    assert "REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM streamlit_readonly" in migracao
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM streamlit_readonly" in painel
    assert "GRANT SELECT ON TABLES TO streamlit_readonly" not in painel
    assert "GRANT SELECT ON TABLE ofertas TO streamlit_readonly" in painel
    assert "GRANT SELECT ON TABLE ofertas TO streamlit_readonly" in migracao
    assert "preco_moeda" in _bloco_update(pipeline)
    moeda = (RAIZ / "infra/migrations/014_preco_moeda.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS preco_moeda" in moeda
    assert "GRANT UPDATE (preco_moeda)" in moeda
    assert "pg_roles" in moeda
    assert "phash_perceptual" in _bloco_update(pipeline)
    perceptual = (RAIZ / "infra/migrations/015_phash_perceptual.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS phash_perceptual BIT(64)" in perceptual
    assert "GRANT UPDATE (phash_perceptual)" in perceptual
    assert "pg_roles" in perceptual
    assert "fadiga_dias" in _bloco_update(pipeline)
    ciclo = (RAIZ / "infra/init-lifecycle-role.sh").read_text(encoding="utf-8")
    assert "GRANT UPDATE (processamento_token, score, fadiga_dias)" in ciclo
    fadiga = (RAIZ / "infra/migrations/016_fadiga_anuncio.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS fadiga_dias" in fadiga
    assert "GRANT UPDATE (processamento_token, score, fadiga_dias)" in fadiga
    assert "pg_roles" in fadiga


def test_security_md_publica_o_canal_de_reporte():
    texto = (RAIZ / "SECURITY.md").read_text(encoding="utf-8")
    assert "ezekisilva544@gmail.com" in texto
    assert "mailto:ezekisilva544@gmail.com" in texto
    assert "seu-email@dominio.com" not in texto
