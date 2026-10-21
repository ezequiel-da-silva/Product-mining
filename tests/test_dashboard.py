import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.dashboard import (
    DIMENSAO_EMBEDDING,
    FORMATO_DATA,
    FORMATO_MARGEM,
    OFERTAS_BUSCA_CURSOR_SQL,
    OFERTAS_BUSCA_SQL,
    OFERTAS_RECENTES_CURSOR_SQL,
    OFERTAS_RECENTES_LEGENDA,
    OFERTAS_RECENTES_SQL,
    PAGE_SIZE,
    ROTULOS_OFERTAS,
    TITULO_OFERTAS_RECENTES,
    TITULO_PAINEL,
    _reiniciar_listagem,
    _vetor_embedding,
    colunas_ofertas_recentes,
    consultar_ofertas_por_similaridade,
    consultar_ofertas_recentes,
    formatar_preco_br,
    gerar_embedding_busca,
    ofertas_para_exibicao,
)


def test_ofertas_recentes_ordena_pela_captura_mais_recente():
    consulta = " ".join(OFERTAS_RECENTES_SQL.split())

    assert "data_ultima_captura" in consulta
    assert "ORDER BY data_ultima_captura DESC, id DESC" in consulta
    assert "LIMIT %s" in consulta
    assert "OFFSET" not in consulta


def test_dashboard_tem_rotulos_formato_e_titulo_sem_emoji():
    assert "🎯" not in TITULO_PAINEL
    assert OFERTAS_RECENTES_LEGENDA == ("Ofertas concluídas, da captura mais recente para a mais antiga.")
    assert ROTULOS_OFERTAS == {
        "id": "Identificador",
        "nicho": "Nicho",
        "preco_oferta_estimado": "Preço estimado",
        "score": "Pontuação",
        "margem_estimada_pct": "Margem estimada",
        "data_ultima_captura": "Última captura",
        "similaridade_pct": "Similaridade",
    }

    assert TITULO_OFERTAS_RECENTES == "Ofertas concluídas mais recentes"
    assert "#" not in TITULO_OFERTAS_RECENTES
    assert formatar_preco_br(1234.5) == "R$ 1.234,50"
    assert formatar_preco_br(None) is None

    colunas = colunas_ofertas_recentes()
    assert colunas["preco_oferta_estimado"]["type_config"]["type"] == "text"
    assert colunas["margem_estimada_pct"]["type_config"]["format"] == FORMATO_MARGEM
    assert colunas["data_ultima_captura"]["type_config"]["format"] == FORMATO_DATA
    assert colunas["nicho"]["label"] == "Nicho"


def _conexao_com_linhas(linhas):
    cursor = MagicMock()
    cursor.fetchall.return_value = linhas
    conexao = MagicMock()
    conexao.cursor.return_value.__enter__.return_value = cursor
    return conexao, cursor


def test_embedding_de_busca_usa_retrieval_query_e_768_dimensoes(monkeypatch):
    import src.dashboard as dashboard

    resposta = MagicMock()
    resposta.embeddings = [MagicMock(values=[0.1] * DIMENSAO_EMBEDDING)]
    cliente = MagicMock()
    cliente.models.embed_content.return_value = resposta
    monkeypatch.setattr(dashboard, "init_embedding_client", lambda: cliente)

    vetor = gerar_embedding_busca("produto para dormir melhor")

    assert len(vetor) == DIMENSAO_EMBEDDING
    chamada = cliente.models.embed_content.call_args.kwargs
    assert chamada["model"] == "gemini-embedding-2"
    assert chamada["contents"] == "produto para dormir melhor"
    assert chamada["config"].task_type == "RETRIEVAL_QUERY"
    assert chamada["config"].output_dimensionality == DIMENSAO_EMBEDDING


def test_embedding_de_busca_rejeita_dimensao_errada():
    resposta = MagicMock()
    resposta.embeddings = [MagicMock(values=[0.1, 0.2])]

    with pytest.raises(ValueError, match="768"):
        _vetor_embedding(resposta)


def test_paginacao_recente_usa_cursor_sem_offset():
    instante = datetime(2026, 10, 21, tzinfo=timezone.utc)
    linhas = [{"id": item, "data_ultima_captura": instante} for item in range(PAGE_SIZE + 1)]
    conexao, cursor_db = _conexao_com_linhas(linhas)

    pagina, tem_mais = consultar_ofertas_recentes(conexao, (instante, 50))

    assert len(pagina) == PAGE_SIZE
    assert tem_mais is True
    cursor_db.execute.assert_called_once_with(OFERTAS_RECENTES_CURSOR_SQL, (instante, 50, PAGE_SIZE + 1))
    assert "OFFSET" not in OFERTAS_RECENTES_CURSOR_SQL


def test_busca_vetorial_e_cursor_sao_parametrizados():
    vetor = [0.1] * DIMENSAO_EMBEDDING
    linhas = [{"id": item, "distancia": item / 100} for item in range(PAGE_SIZE + 1)]
    conexao, cursor_db = _conexao_com_linhas(linhas)

    pagina, tem_mais = consultar_ofertas_por_similaridade(conexao, vetor, (0.25, 50))

    assert len(pagina) == PAGE_SIZE
    assert tem_mais is True
    consulta, parametros = cursor_db.execute.call_args.args
    assert consulta == OFERTAS_BUSCA_CURSOR_SQL
    assert consulta.count("%s::vector") == 3
    assert "embedding_analise <=>" in consulta
    assert "OFFSET" not in consulta
    assert parametros[2:] == (0.25, 50, parametros[0], PAGE_SIZE + 1)
    assert parametros[0].startswith("[")
    assert parametros[0] == parametros[1] == parametros[4]
    assert "%s::vector" in OFERTAS_BUSCA_SQL


def test_similaridade_e_reset_da_paginacao(monkeypatch):
    import src.dashboard as dashboard

    exibicao = ofertas_para_exibicao([{"id": 1, "preco_oferta_estimado": 1234.5, "distancia": 0.2}])
    assert exibicao == [
        {
            "id": 1,
            "preco_oferta_estimado": "R$ 1.234,50",
            "similaridade_pct": pytest.approx(80.0),
        }
    ]

    estado = {"ofertas_resultados": [{"id": 99}]}
    monkeypatch.setattr(dashboard.st, "session_state", estado)
    _reiniciar_listagem("sono", [0.1] * DIMENSAO_EMBEDDING)

    assert estado["ofertas_modo"] == "busca"
    assert estado["ofertas_resultados"] == []
    assert estado["ofertas_cursor"] is None
    assert estado["ofertas_inicializadas"] is False
