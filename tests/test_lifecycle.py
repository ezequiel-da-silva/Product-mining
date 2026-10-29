import os
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.lifecycle import (  # noqa: E402
    OFERTA_ELEGIVEL_TRAVADA_SQL,
    OFERTAS_FRACAS_SQL,
    _apagar_objeto,
    _chave_objeto_r2,
    _expirar_uma,
)


def test_chave_de_video_publico_ou_pre_assinado():
    assert _chave_objeto_r2("https://cdn.example/videos/ad-1.mp4") == "videos/ad-1.mp4"
    assert _chave_objeto_r2("videos/ad-1.mp4") == "videos/ad-1.mp4"
    assinada = "https://account.r2.cloudflarestorage.com/bucket/videos/ad_1.mp4?X-Amz-Algorithm=AWS4"
    assert _chave_objeto_r2(assinada) == "videos/ad_1.mp4"
    assert _chave_objeto_r2("https://cdn.example/other/file.mp4") is None


def test_selecao_limita_ofertas_fracas_e_antigas():
    consulta = " ".join(OFERTAS_FRACAS_SQL.split())

    assert "score < 40" in consulta
    assert "INTERVAL '30 days'" in consulta
    assert "status_qualificacao <> 'processando_ia'" in consulta
    assert "LIMIT 100" in consulta


def _pool_com_cursor(monkeypatch, cursor):
    import src.lifecycle as lifecycle

    conexao = MagicMock()
    conexao.cursor.return_value.__enter__.return_value = cursor
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conexao
    monkeypatch.setattr(lifecycle, "pool_db", pool)
    return conexao


def test_oferta_nao_e_expirada_se_deixou_de_ser_elegivel(monkeypatch):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    conexao = _pool_com_cursor(monkeypatch, cursor)

    assert _expirar_uma({"id": 7}, MagicMock()) is False

    assert "FOR UPDATE SKIP LOCKED" in OFERTA_ELEGIVEL_TRAVADA_SQL
    conexao.rollback.assert_called_once()
    assert not any("DELETE FROM ofertas" in call.args[0] for call in cursor.execute.call_args_list)


def test_url_compartilhada_preserva_o_arquivo(monkeypatch):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 7, "url_video_r2": "https://cdn/videos/a.mp4"},
        {"id": 8},
    ]
    conexao = _pool_com_cursor(monkeypatch, cursor)
    apagar_objeto = MagicMock()
    monkeypatch.setattr("src.lifecycle._apagar_objeto", apagar_objeto)
    cursor.rowcount = 1

    resultado = _expirar_uma({"id": 7}, MagicMock())

    assert resultado is True
    apagar_objeto.assert_not_called()
    assert any("DELETE FROM ofertas" in call.args[0] for call in cursor.execute.call_args_list)
    conexao.commit.assert_called_once()


def test_falha_no_r2_mantem_a_linha(monkeypatch):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 8, "url_video_r2": "https://cdn.example/videos/ad-8.mp4"},
        None,
    ]
    conexao = _pool_com_cursor(monkeypatch, cursor)
    monkeypatch.setattr("src.lifecycle._apagar_objeto", lambda *_args: False)

    resultado = _expirar_uma({"id": 8}, MagicMock())

    assert resultado is False
    conexao.rollback.assert_called_once()
    assert not any("DELETE FROM ofertas" in call.args[0] for call in cursor.execute.call_args_list)


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
