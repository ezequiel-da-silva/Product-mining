import os
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.lifecycle import (  # noqa: E402
    OFERTAS_FRACAS_SQL,
    _apagar_objeto,
    _chave_objeto_r2,
    expirar_ofertas_fracas,
)


def test_chave_de_video_publico_ou_pre_assinado():
    assert _chave_objeto_r2("https://cdn.example/videos/ad-1.mp4") == "videos/ad-1.mp4"
    assinada = "https://account.r2.cloudflarestorage.com/bucket/videos/ad_1.mp4?X-Amz-Algorithm=AWS4"
    assert _chave_objeto_r2(assinada) == "videos/ad_1.mp4"
    assert _chave_objeto_r2("https://cdn.example/other/file.mp4") is None


def test_selecao_limita_ofertas_fracas_e_antigas():
    consulta = " ".join(OFERTAS_FRACAS_SQL.split())

    assert "score < 40" in consulta
    assert "INTERVAL '30 days'" in consulta
    assert "status_qualificacao <> 'processando_ia'" in consulta
    assert "LIMIT 100" in consulta


def test_url_compartilhada_preserva_o_arquivo(monkeypatch):
    import src.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_ofertas_fracas", lambda: [{"id": 7, "url_video_r2": "https://cdn/videos/a.mp4"}])
    monkeypatch.setattr(lifecycle, "_url_compartilhada", lambda *_args: True)
    apagar_objeto = MagicMock()
    monkeypatch.setattr(lifecycle, "_apagar_objeto", apagar_objeto)
    monkeypatch.setattr(lifecycle, "_apagar_linha", lambda oferta_id: oferta_id == 7)

    resultado = expirar_ofertas_fracas()

    assert resultado == {"status": "expired", "removidas": 1}
    apagar_objeto.assert_not_called()


def test_falha_no_r2_mantem_a_linha(monkeypatch):
    import src.lifecycle as lifecycle

    monkeypatch.setattr(
        lifecycle,
        "_ofertas_fracas",
        lambda: [{"id": 8, "url_video_r2": "https://cdn.example/videos/ad-8.mp4"}],
    )
    monkeypatch.setattr(lifecycle, "_url_compartilhada", lambda *_args: False)
    monkeypatch.setattr(lifecycle, "_apagar_objeto", lambda *_args: False)
    apagar_linha = MagicMock()
    monkeypatch.setattr(lifecycle, "_apagar_linha", apagar_linha)

    resultado = expirar_ofertas_fracas()

    assert resultado == {"status": "expired", "removidas": 0}
    apagar_linha.assert_not_called()


def test_migracao_003_higieniza_antes_das_constraints():
    caminho = os.path.join(
        os.path.dirname(__file__),
        "..",
        "infra",
        "migrations",
        "003_ofertas_constraints.sql",
    )
    with open(caminho, encoding="utf-8") as arquivo:
        sql = arquivo.read()

    posicao_status = sql.index("SET status_qualificacao = 'pendente'")
    posicao_score = sql.index("LEAST(GREATEST(COALESCE(score, 0), 0), 120)")
    posicao_margem = sql.index("LEAST(GREATEST(margem_estimada_pct, 0), 100)")
    posicao_preco = sql.index("SET preco_oferta_estimado = 0")
    posicao_constraint = sql.index("ADD CONSTRAINT")

    assert posicao_status < posicao_score < posicao_margem < posicao_preco < posicao_constraint
    assert "margem_estimada_pct IS NOT NULL" in sql
    assert "preco_oferta_estimado < 0" in sql


def test_expiracao_diaria_usa_a_fila_lifecycle():
    from core.celery_app import app

    agenda = app.conf.beat_schedule["expirar-ofertas-fracas"]

    assert agenda["task"] == "tasks.lifecycle.expirar_ofertas_fracas"
    assert app.conf.task_routes["tasks.lifecycle.expirar_ofertas_fracas"]["queue"] == "lifecycle_queue"


@pytest.mark.parametrize("codigo", ["404", "NoSuchKey", "NotFound"])
def test_codigos_de_objeto_ausente(codigo):
    cliente = MagicMock()
    cliente.delete_object.side_effect = ClientError(
        {"Error": {"Code": codigo, "Message": "ausente"}},
        "DeleteObject",
    )

    assert _apagar_objeto(cliente, "videos/ad-8.mp4") is True
