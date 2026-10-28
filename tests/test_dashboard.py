import inspect
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.dashboard import (
    BOTAO_DETALHES,
    ALTURA_VIDEO,
    LARGURA_VIDEO,
    COLUNAS_OFERTA,
    DIMENSAO_EMBEDDING,
    FILTRO_NOTA,
    FILTRO_TODOS,
    GANCHO_LIMITE,
    LEGENDA_BUSCA,
    NICHOS_CONCLUIDOS_SQL,
    NOTA_MINIMA,
    OFERTAS_BUSCA_CURSOR_SQL,
    OFERTAS_BUSCA_SQL,
    OFERTAS_RECENTES_CURSOR_SQL,
    OFERTAS_RECENTES_LEGENDA,
    OFERTAS_RECENTES_SQL,
    PAGE_SIZE,
    PRECO_AUSENTE,
    ROTULOS_OFERTAS,
    SESSAO_SEGUNDOS,
    TITULO_OFERTAS_RECENTES,
    TITULO_PAINEL,
    _assinar_sessao,
    _dialogo_oferta,
    _mostrar_cards,
    _sincronizar_filtro,
    _normalizar_frase_busca,
    _opcoes_filtro,
    _reiniciar_listagem,
    _sessao_valida,
    _vetor_embedding,
    _zerar_paginacao,
    consultar_ofertas_por_similaridade,
    consultar_ofertas_recentes,
    formatar_data_card,
    formatar_preco_br,
    gancho_curto,
    gerar_embedding_busca,
    listar_nichos_concluidos,
    similaridade_percentual,
    texto_preco,
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
    assert "Biblioteca de Anúncios" in LEGENDA_BUSCA
    assert ROTULOS_OFERTAS["gancho_3s"] == "Gancho"
    assert ROTULOS_OFERTAS["url_destino"] == "Link da oferta"
    assert ROTULOS_OFERTAS["score"] == "Pontuação"
    assert ROTULOS_OFERTAS["nicho"] == "Nicho"
    assert ROTULOS_OFERTAS["data_ultima_captura"] == "Última captura"
    assert ROTULOS_OFERTAS["similaridade_pct"] == "Similaridade"
    assert ROTULOS_OFERTAS["preco_oferta_estimado"] == "Preço estimado"

    assert TITULO_OFERTAS_RECENTES == "Ofertas concluídas mais recentes"
    assert "#" not in TITULO_OFERTAS_RECENTES
    assert formatar_preco_br(1234.5) == "R$ 1.234,50"
    assert formatar_preco_br(None) is None
    assert texto_preco(None) == PRECO_AUSENTE
    assert PRECO_AUSENTE == "não identificado"
    assert BOTAO_DETALHES == "Ver detalhes"
    assert LARGURA_VIDEO == 420
    assert ALTURA_VIDEO == 280
    assert FILTRO_TODOS == "Todos"
    assert FILTRO_NOTA == "Nota maior que 70"
    assert NOTA_MINIMA == 70
    assert _opcoes_filtro(["Fitness e Esportes"]) == [
        "Todos",
        "Fitness e Esportes",
        "Nota maior que 70",
    ]


def _conexao_com_linhas(linhas):
    cursor = MagicMock()
    cursor.fetchall.return_value = linhas
    conexao = MagicMock()
    conexao.cursor.return_value.__enter__.return_value = cursor
    return conexao, cursor


def test_normalizar_frase_busca_ignora_espaco_e_maiuscula():
    assert _normalizar_frase_busca(" Emagrecimento ") == "emagrecimento"


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


def test_consultas_trazem_gancho_link_e_detalhe():
    for consulta in (
        OFERTAS_RECENTES_SQL,
        OFERTAS_RECENTES_CURSOR_SQL,
        OFERTAS_BUSCA_SQL,
        OFERTAS_BUSCA_CURSOR_SQL,
    ):
        for coluna in (
            "gancho_3s",
            "gatilho_mental",
            "url_destino",
            "copy_texto",
            "transcricao_video",
            "url_video_r2",
            "objecoes_quebradas",
            "angulos_alternativos",
        ):
            assert coluna in consulta
            assert coluna in COLUNAS_OFERTA


def test_sessao_assinada_vale_doze_horas_e_rejeita_alteracao():
    segredo = "senha-do-painel"
    agora = 1_700_000_000.0
    token = _assinar_sessao(segredo, int(agora) + SESSAO_SEGUNDOS)

    assert _sessao_valida(token, segredo, agora=agora) is True
    assert _sessao_valida(token, segredo, agora=agora + SESSAO_SEGUNDOS) is False
    assert _sessao_valida(token + "a", segredo, agora=agora) is False
    assert _sessao_valida(token, "outra-senha", agora=agora) is False


def test_similaridade_e_reset_da_paginacao(monkeypatch):
    import src.dashboard as dashboard

    assert similaridade_percentual(0.2) == pytest.approx(80.0)
    assert formatar_data_card(datetime(2026, 10, 27, 12, 45, tzinfo=timezone.utc)) == "27/10/2026 12:45"
    assert formatar_data_card(None) == "—"

    estado = {
        "ofertas_resultados": [{"id": 99}],
        "ofertas_frase": "sono",
        "ofertas_vetor": [0.1],
        "filtro_ofertas": FILTRO_NOTA,
    }
    monkeypatch.setattr(dashboard.st, "session_state", estado)
    _zerar_paginacao()

    assert estado["ofertas_resultados"] == []
    assert estado["ofertas_cursor"] is None
    assert estado["ofertas_inicializadas"] is False
    assert estado["ofertas_frase"] == "sono"
    assert estado["filtro_ofertas"] == FILTRO_NOTA

    _reiniciar_listagem("sono", [0.1] * DIMENSAO_EMBEDDING)

    assert estado["ofertas_modo"] == "busca"
    assert estado["ofertas_resultados"] == []
    assert estado["ofertas_cursor"] is None
    assert estado["ofertas_inicializadas"] is False
    assert estado["filtro_ofertas"] == FILTRO_NOTA


def test_filtro_de_nota_parametriza_o_score():
    conexao, cursor_db = _conexao_com_linhas([])

    consultar_ofertas_recentes(conexao, filtro=FILTRO_NOTA)

    consulta, parametros = cursor_db.execute.call_args.args
    assert "score > %s" in consulta
    assert "ORDER BY data_ultima_captura DESC, id DESC" in consulta
    assert "OFFSET" not in consulta
    assert parametros == (NOTA_MINIMA, PAGE_SIZE + 1)
    assert FILTRO_NOTA not in consulta


def test_filtro_de_nicho_nao_interpola_o_valor():
    nicho = "Fitness' OR 1=1 --"
    instante = datetime(2026, 10, 27, tzinfo=timezone.utc)
    conexao, cursor_db = _conexao_com_linhas([])

    consultar_ofertas_recentes(conexao, (instante, 9), filtro=nicho)

    consulta, parametros = cursor_db.execute.call_args.args
    assert "nicho = %s" in consulta
    assert nicho not in consulta
    assert parametros == (instante, 9, nicho, PAGE_SIZE + 1)


def test_busca_com_filtro_de_nota_preserva_o_cursor_vetorial():
    vetor = [0.1] * DIMENSAO_EMBEDDING
    conexao, cursor_db = _conexao_com_linhas([])

    consultar_ofertas_por_similaridade(conexao, vetor, (0.25, 50), filtro=FILTRO_NOTA)

    consulta, parametros = cursor_db.execute.call_args.args
    assert "score > %s" in consulta
    assert consulta.count("%s::vector") == 3
    assert parametros[4] == NOTA_MINIMA
    assert parametros[0] == parametros[1] == parametros[5]
    assert parametros[2:] == (0.25, 50, NOTA_MINIMA, parametros[0], PAGE_SIZE + 1)


def test_gancho_curto_trunca_texto_longo():
    assert gancho_curto("  medalha  ") == "medalha"
    assert gancho_curto(None) == "—"
    assert gancho_curto("   ") == "—"
    longo = "a" * (GANCHO_LIMITE + 40)
    curto = gancho_curto(longo)
    assert len(curto) <= GANCHO_LIMITE
    assert curto.endswith("…")
    assert curto[:-1] == "a" * (GANCHO_LIMITE - 1)


def test_nichos_concluidos_sao_distintos_e_sem_interpolacao():
    conexao, cursor_db = _conexao_com_linhas([{"nicho": "Fitness e Esportes"}, {"nicho": ""}])

    assert listar_nichos_concluidos(conexao) == ["Fitness e Esportes"]
    cursor_db.execute.assert_called_once_with(NICHOS_CONCLUIDOS_SQL)
    assert "SELECT DISTINCT nicho" in NICHOS_CONCLUIDOS_SQL
    assert "status_qualificacao = 'concluido'" in NICHOS_CONCLUIDOS_SQL


def test_cards_e_modal_substituem_a_tabela():
    import src.dashboard as dashboard

    fonte_main = inspect.getsource(dashboard.main)
    fonte_cards = inspect.getsource(_mostrar_cards)
    fonte_dialogo = inspect.getsource(_dialogo_oferta)
    fonte_filtro = inspect.getsource(_sincronizar_filtro)

    assert "st.dataframe" not in fonte_main
    assert "st.pills" in fonte_filtro
    assert "_sincronizar_filtro" in fonte_main
    assert "_dialogo_oferta" in fonte_main
    assert "st.columns(3" in fonte_cards
    assert "border=True" in fonte_cards
    assert "BOTAO_DETALHES" in fonte_cards
    assert "Assistir vídeo" not in fonte_dialogo
    assert "st.video" in fonte_dialogo
    assert "width=LARGURA_VIDEO" in fonte_dialogo
    assert "ALTURA_VIDEO" in fonte_dialogo
    assert "object-fit: contain" in fonte_dialogo
    assert 'height="stretch"' in fonte_dialogo
    assert 'width="stretch"' not in fonte_dialogo
    assert "st.columns(3" in fonte_dialogo
    assert "st.columns(2" in fonte_dialogo
    assert "st.link_button" in fonte_dialogo
    assert "texto_preco" in fonte_dialogo
