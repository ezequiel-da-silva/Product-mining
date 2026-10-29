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


def test_security_md_publica_o_canal_de_reporte():
    texto = (RAIZ / "SECURITY.md").read_text(encoding="utf-8")
    assert "ezekisilva544@gmail.com" in texto
    assert "mailto:ezekisilva544@gmail.com" in texto
    assert "seu-email@dominio.com" not in texto
