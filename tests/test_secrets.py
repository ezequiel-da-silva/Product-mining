import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.secrets import ler_segredo


def test_arquivo_preenchido_tem_precedencia(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "valor-do-ambiente")
    (tmp_path / "GEMINI_API_KEY").write_text("valor-do-arquivo\n", encoding="utf-8")

    assert ler_segredo("GEMINI_API_KEY", diretorio=str(tmp_path)) == "valor-do-arquivo"


def test_arquivo_vazio_cai_no_ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_R2_SECRET_KEY", "valor-do-ambiente")
    (tmp_path / "CLOUDFLARE_R2_SECRET_KEY").write_text("\n", encoding="utf-8")

    assert ler_segredo("CLOUDFLARE_R2_SECRET_KEY", diretorio=str(tmp_path)) == "valor-do-ambiente"


def test_arquivo_ausente_cai_no_ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "bucket-do-ambiente")

    assert ler_segredo("CLOUDFLARE_R2_BUCKET", diretorio=str(tmp_path)) == "bucket-do-ambiente"


def test_arquivo_ausente_e_ambiente_vazio_devolvem_texto_vazio(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert ler_segredo("GEMINI_API_KEY", diretorio=str(tmp_path)) == ""
