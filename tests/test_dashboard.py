import inspect
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from schemas import NICHOS_CANONICOS
from src.dashboard import (
    ALTURA_VIDEO,
    BOTAO_DETALHES,
    COLUNAS_OFERTA,
    DIMENSAO_EMBEDDING,
    ESTIMATIVA_SOURCING,
    FILTRO_NOTA,
    FILTRO_TODOS,
    GANCHO_LIMITE,
    LARGURA_VIDEO,
    LEGENDA_BUSCA,
    LEGENDA_MARGEM,
    LIMITE_EMBEDDING_POR_MINUTO,
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
    SQL_OPERACAO_P95,
    SQL_OPERACAO_PRECOS,
    SQL_OPERACAO_TOKENS,
    TITULO_OFERTAS_RECENTES,
    TITULO_PAINEL,
    _aplicar_busca,
    _avaliar_cota_embedding,
    _carregar_proxima_pagina,
    _corpo_dialogo,
    _definir_triagem_lote,
    _descartar_credencial_na_url,
    _dialogo_oferta,
    _link_loja,
    _mostrar_cards,
    _normalizar_frase_busca,
    _opcoes_filtro,
    _reiniciar_listagem,
    _sincronizar_filtro,
    _vetor_embedding,
    _zerar_paginacao,
    aviso_destino_inseguro,
    checkout_detectado,
    consultar_ofertas_por_similaridade,
    consultar_ofertas_recentes,
    formatar_data_card,
    formatar_preco_br,
    gancho_curto,
    gerar_embedding_busca,
    html_regiao_viva,
    listar_nichos_concluidos,
    rotulo_acessivel_detalhes,
    rotulo_margem,
    rotulo_nicho,
    similaridade_percentual,
    texto_bloqueio_cota,
    texto_comercial,
    texto_comparacao_p95,
    texto_literal,
    texto_nota,
    texto_p95_usd,
    texto_preco,
    texto_precos_janela,
    texto_regiao_viva,
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
    assert FILTRO_NOTA == "Nota de 75 ou mais"
    assert NOTA_MINIMA == 75
    assert _opcoes_filtro(["Fitness e Esportes"]) == [
        "Todos",
        *NICHOS_CANONICOS,
        "Fitness e Esportes",
        "Nota de 75 ou mais",
    ]
    assert _opcoes_filtro(["Pet"]) == ["Todos", *NICHOS_CANONICOS, "Nota de 75 ou mais"]


def _conexao_com_linhas(linhas):
    cursor = MagicMock()
    cursor.fetchall.return_value = linhas
    conexao = MagicMock()
    conexao.cursor.return_value.__enter__.return_value = cursor
    return conexao, cursor


def test_http_avisa_dominio_externo_sem_criptografia():
    aviso = aviso_destino_inseguro("http://loja.example/produto")
    assert aviso is not None
    assert "loja.example" in aviso
    assert "sem criptografia" in aviso
    assert "domínio externo" in aviso
    assert aviso_destino_inseguro("https://loja.example/produto") is None
    assert aviso_destino_inseguro("HTTP://Loja.Example/item") is not None
    fonte = inspect.getsource(_link_loja)
    assert "aviso_destino_inseguro" in fonte
    assert "noopener noreferrer" in fonte
    assert "st.caption" in fonte


def test_cota_de_embedding_usa_janela_deslizante():
    historico = [float(indice) for indice in range(LIMITE_EMBEDDING_POR_MINUTO)]

    permitido, atual = _avaliar_cota_embedding(historico, agora=11.5)

    assert permitido is False
    assert atual == historico

    permitido, atual = _avaliar_cota_embedding(historico, agora=60.0)

    assert permitido is True
    assert atual[0] == 1.0
    assert atual[-1] == 60.0
    assert len(atual) == LIMITE_EMBEDDING_POR_MINUTO


def test_busca_bloqueada_nao_gera_embedding(monkeypatch):
    import src.dashboard as dashboard

    estado = {
        "embedding_buscas_em": [0.0] * LIMITE_EMBEDDING_POR_MINUTO,
        "ofertas_modo": "recentes",
        "ofertas_frase": "anterior",
        "ofertas_vetor": None,
    }
    monkeypatch.setattr(dashboard.st, "session_state", estado)
    embedding = MagicMock()
    monkeypatch.setattr(dashboard, "gerar_embedding_busca", embedding)

    _aplicar_busca("produto para dormir melhor", agora=10.0)

    embedding.assert_not_called()
    assert estado["busca_limitada"] is True
    assert estado["ofertas_frase"] == "anterior"
    assert estado["ofertas_modo"] == "recentes"
    assert len(estado["embedding_buscas_em"]) == LIMITE_EMBEDDING_POR_MINUTO


def test_nicho_canonico_nao_consome_cota_de_embedding(monkeypatch):
    import src.dashboard as dashboard

    historico = [0.0] * LIMITE_EMBEDDING_POR_MINUTO
    estado = {"embedding_buscas_em": list(historico)}
    monkeypatch.setattr(dashboard.st, "session_state", estado)
    embedding = MagicMock()
    monkeypatch.setattr(dashboard, "gerar_embedding_busca", embedding)

    _aplicar_busca("Pet", agora=10.0)

    embedding.assert_not_called()
    assert estado["filtro_ofertas"] == "Pet"
    assert estado["embedding_buscas_em"] == historico
    assert estado["busca_limitada"] is False
    assert estado["ofertas_modo"] == "recentes"


def test_rotulo_de_outros_inclui_sub_nicho():
    assert rotulo_nicho({"nicho": "Outros", "sub_nicho": "Cachorro"}) == "Outros: Cachorro"
    assert rotulo_nicho({"nicho": "Pet", "sub_nicho": "Gatos"}) == "Pet"
    assert rotulo_nicho({}) == "—"


def test_busca_por_nicho_canonico_nao_chama_embedding(monkeypatch):
    import src.dashboard as dashboard

    estado: dict = {}
    monkeypatch.setattr(dashboard.st, "session_state", estado)
    embedding = MagicMock()
    monkeypatch.setattr(dashboard, "gerar_embedding_busca", embedding)

    _aplicar_busca("  Saúde e Beleza ")

    embedding.assert_not_called()
    assert estado["filtro_ofertas"] == "Saúde e Beleza"
    assert estado["ofertas_modo"] == "recentes"
    assert estado["ofertas_vetor"] is None
    assert estado["ofertas_frase"] == ""


def test_busca_por_sinonimo_continua_vetorial(monkeypatch):
    import src.dashboard as dashboard

    estado: dict = {}
    monkeypatch.setattr(dashboard.st, "session_state", estado)
    embedding = MagicMock(return_value=[0.2] * DIMENSAO_EMBEDDING)
    monkeypatch.setattr(dashboard, "gerar_embedding_busca", embedding)

    _aplicar_busca("Pet Shop")

    embedding.assert_called_once_with("pet shop")
    assert estado["ofertas_modo"] == "busca"
    assert "filtro_ofertas" not in estado


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
    consulta, parametros = cursor_db.execute.call_args.args
    assert "(data_ultima_captura, id) < (%s, %s)" in consulta
    assert "triagem.estado = 'descartada'" in consulta
    assert "OFFSET" not in consulta
    assert parametros == (instante, 50, PAGE_SIZE + 1)


def test_busca_vetorial_e_cursor_sao_parametrizados():
    vetor = [0.1] * DIMENSAO_EMBEDDING
    linhas = [{"id": item, "distancia": item / 100} for item in range(PAGE_SIZE + 1)]
    conexao, cursor_db = _conexao_com_linhas(linhas)

    pagina, tem_mais = consultar_ofertas_por_similaridade(conexao, vetor, (0.25, 50))

    assert len(pagina) == PAGE_SIZE
    assert tem_mais is True
    consulta, parametros = cursor_db.execute.call_args.args
    assert "triagem.estado = 'descartada'" in consulta
    assert consulta.count("%s::vector") == 3
    assert "JOIN oferta_vetor AS vetor" in consulta
    assert "vetor.embedding <=>" in consulta
    assert "embedding_analise <=>" not in consulta
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
            "preco_moeda",
        ):
            assert coluna in consulta
            assert coluna in COLUNAS_OFERTA


def test_credencial_nao_permanece_na_url(monkeypatch):
    import src.dashboard as dashboard

    parametros = {"sessao": "1700000000.assinatura"}
    monkeypatch.setattr(dashboard.st, "query_params", parametros)

    _descartar_credencial_na_url()

    assert "sessao" not in parametros


def test_nota_margem_e_checkout_sao_explicitos():
    assert texto_nota(80) == "80/100"
    assert texto_nota(None) == "—"
    assert rotulo_margem(70, "BRL") == "Margem alta: 70%"
    assert rotulo_margem(45, "BRL") == "Margem média: 45%"
    assert rotulo_margem(10, "BRL") == "Margem baixa: 10%"
    assert rotulo_margem(None, "BRL") == "Margem não identificada"
    assert rotulo_margem(70, "USD") == "Margem não comparável"
    assert rotulo_margem(70) == "Margem não comparável"
    assert ESTIMATIVA_SOURCING == "Estimativa Sourcing (China)"
    assert "conciliação fiscal" in LEGENDA_MARGEM
    assert rotulo_acessivel_detalhes({"nicho": "Pet", "id": 7}) == "Ver detalhes da oferta Pet #7"
    assert texto_literal("Pet [loja](https://evil.example)") == "Pet \\[loja\\](https:\u2060//evil.example)"
    assert "texto_literal(nicho)" in inspect.getsource(rotulo_acessivel_detalhes)
    assert rotulo_acessivel_detalhes({"id": 3}) == "Ver detalhes da oferta sem nicho #3"
    assert checkout_detectado("https://loja.myshopify.com/products/x") == "Shopify"
    assert checkout_detectado("https://exemplo.com") is None
    assert texto_comercial({"dados_comerciais_ausentes": True}) == "Dados comerciais ausentes"
    assert texto_comercial({"preco_oferta_estimado": 10, "preco_moeda": "BRL"}) == "R$ 10,00"
    assert texto_comercial({"preco_oferta_estimado": 1299.9, "preco_moeda": "USD"}) == "US$ 1,299.90"
    assert texto_comercial({"preco_oferta_estimado": 49.90}) == "Moeda não identificada"


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
    assert "score >= %s" in consulta
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
    assert "score >= %s" in consulta
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
    fonte_lista = inspect.getsource(dashboard._painel_ofertas)
    fonte_cards = inspect.getsource(_mostrar_cards)
    fonte_dialogo = inspect.getsource(_corpo_dialogo)
    fonte_abertura = inspect.getsource(_dialogo_oferta)
    fonte_filtro = inspect.getsource(_sincronizar_filtro)

    assert "st.dataframe" not in fonte_main
    assert "st.dataframe" not in fonte_lista
    assert "st.pills" in fonte_filtro
    assert "format_func=texto_literal" in fonte_filtro
    assert "_sincronizar_filtro" in fonte_lista
    assert "_dialogo_oferta" in fonte_lista
    assert "st.columns(3" in fonte_cards
    assert "border=True" in fonte_cards
    assert "rotulo_acessivel_detalhes" in fonte_cards
    assert "ESTIMATIVA_SOURCING" in inspect.getsource(dashboard._mostrar_margem)
    assert 'st.expander("Transcrição do Criativo")' in fonte_dialogo
    assert "_mostrar_margem" in fonte_dialogo
    assert "_mostrar_margem" in fonte_cards
    assert "Assistir vídeo" not in fonte_dialogo
    assert "st.video" in fonte_dialogo
    assert "width=LARGURA_VIDEO" in fonte_dialogo
    assert "ALTURA_VIDEO" in fonte_dialogo
    assert "object-fit: contain" in fonte_dialogo
    assert 'height="stretch"' in fonte_dialogo
    assert 'width="stretch"' not in fonte_dialogo
    assert "st.columns(3" in fonte_dialogo
    assert "st.columns(2" in fonte_dialogo
    assert "_link_loja" in fonte_dialogo
    assert "noopener noreferrer" in inspect.getsource(dashboard._link_loja)
    assert "texto_comercial" in fonte_dialogo
    assert "st.text(nicho)" in fonte_cards
    assert "st.text(rotulo_nicho(oferta))" in fonte_dialogo
    assert "st.text(linha)" in inspect.getsource(dashboard._escrever_angulos)
    assert 'st.markdown(f"**{nicho}**")' not in fonte_cards
    assert "url_leitura" in fonte_dialogo
    assert "rotulo_acessivel_detalhes(oferta)" in fonte_abertura
    assert 'st.dialog(titulo, width="large")' in fonte_abertura
    assert '@st.dialog("Oferta"' not in fonte_abertura
    assert 'aria-live="polite"' in inspect.getsource(html_regiao_viva)
    assert "data-oferta-card" in fonte_cards
    assert "Selecionar a oferta" in fonte_cards
    assert "_aplicar_lote" in fonte_lista
    assert "_definir_triagem_lote" in inspect.getsource(dashboard._aplicar_lote)
    assert "LEGENDA_ATALHOS" in fonte_lista


def test_regiao_viva_anuncia_contagem_e_bloqueio_da_cota():
    bloqueio = texto_bloqueio_cota()
    assert "12" in bloqueio
    assert texto_regiao_viva(busca_limitada=True, total=4, em_busca=True, frase="sono", tem_mais=False) == bloqueio
    assert texto_regiao_viva(busca_limitada=False, total=0, em_busca=False, frase="", tem_mais=False) == (
        "Nenhuma oferta concluída encontrada."
    )
    assert (
        texto_regiao_viva(busca_limitada=False, total=3, em_busca=False, frase="", tem_mais=True)
        == "3 ofertas concluídas. Há mais ofertas."
    )
    assert texto_regiao_viva(busca_limitada=False, total=2, em_busca=True, frase="sono", tem_mais=False) == (
        '2 ofertas para "sono".'
    )
    html_anuncio = html_regiao_viva('2 ofertas para "a<b>"')
    assert 'aria-live="polite"' in html_anuncio
    assert 'role="status"' in html_anuncio
    assert "a&lt;b&gt;" in html_anuncio
    assert "<b>" not in html_anuncio


def test_lote_grava_o_mesmo_estado_numa_transacao():
    cursor = MagicMock()
    conexao = MagicMock()
    conexao.cursor.return_value.__enter__.return_value = cursor

    _definir_triagem_lote(conexao, [3, 8, True], "estrelada")

    conexao.transaction.assert_called_once()
    assert cursor.execute.call_count == 1
    consulta, params = cursor.execute.call_args.args
    assert "unnest" in consulta
    assert "ON CONFLICT (oferta_id)" in consulta
    assert params == {"ids": [3, 8], "estado": "estrelada"}
    conexao.commit.assert_not_called()

    cursor.reset_mock()
    _definir_triagem_lote(conexao, [], "estrelada")
    _definir_triagem_lote(conexao, [1], "arquivada")
    cursor.execute.assert_not_called()


def test_atalho_do_card_focado_usa_s_d_e_t():
    import src.dashboard as dashboard

    html_atalho = dashboard._html_atalhos_triagem()
    assert 's: "Salvar"' in html_atalho
    assert 'd: "Descartar"' in html_atalho
    assert 't: "Testando"' in html_atalho
    assert 'querySelectorAll("[data-oferta-card]")' in html_atalho
    assert "stVerticalBlockBorderWrapper" not in html_atalho
    assert "pointerdown" in html_atalho
    assert "window.__atalhosTriagem" in html_atalho
    assert "textarea" in html_atalho
    assert "unsafe_allow_javascript=True" in inspect.getsource(dashboard._registrar_atalhos_triagem)


def test_mesma_frase_reusa_o_vetor_sem_gastar_cota(monkeypatch):
    import src.dashboard as dashboard

    vetor = [0.3] * DIMENSAO_EMBEDDING
    historico = [0.0] * LIMITE_EMBEDDING_POR_MINUTO
    estado = {
        "ofertas_frase": "pet shop",
        "ofertas_vetor": list(vetor),
        "embedding_buscas_em": list(historico),
        "ofertas_modo": "busca",
    }
    monkeypatch.setattr(dashboard.st, "session_state", estado)
    embedding = MagicMock()
    monkeypatch.setattr(dashboard, "gerar_embedding_busca", embedding)

    _aplicar_busca("  Pet Shop ", agora=10.0)

    embedding.assert_not_called()
    assert estado["embedding_buscas_em"] == historico
    assert estado["busca_limitada"] is False
    assert estado["ofertas_vetor"] == vetor
    assert estado["ofertas_modo"] == "busca"
    assert estado["ofertas_inicializadas"] is False


def test_cursor_da_busca_reusa_o_vetor_sem_novo_embedding(monkeypatch):
    import src.dashboard as dashboard

    vetor = [0.2] * DIMENSAO_EMBEDDING
    estado = {
        "ofertas_modo": "busca",
        "ofertas_frase": "pet shop",
        "ofertas_vetor": vetor,
        "ofertas_cursor": None,
        "ofertas_resultados": [],
        "ofertas_tem_mais": True,
        "ofertas_inicializadas": False,
    }
    monkeypatch.setattr(dashboard.st, "session_state", estado)
    embedding = MagicMock()
    monkeypatch.setattr(dashboard, "gerar_embedding_busca", embedding)
    linhas = [{"id": 1, "distancia": 0.1, "data_ultima_captura": None}]
    conexao, cursor_db = _conexao_com_linhas(linhas)

    _carregar_proxima_pagina(conexao, None)

    embedding.assert_not_called()
    consulta, parametros = cursor_db.execute.call_args.args
    assert "JOIN oferta_vetor AS vetor" in consulta
    assert parametros[0] == _vetor_para_pg_teste(vetor)
    assert estado["ofertas_cursor"] == (0.1, 1)


def _vetor_para_pg_teste(vetor):
    return "[" + ",".join(format(valor, ".17g") for valor in vetor) + "]"


def test_operacao_compara_p95_de_sete_dias_e_mantem_o_mes():
    assert "percentile_cont(0.95)" in SQL_OPERACAO_P95
    assert "INTERVAL '7 days'" in SQL_OPERACAO_P95
    assert "INTERVAL '14 days'" in SQL_OPERACAO_P95
    assert "analise_concluida_em" in SQL_OPERACAO_P95
    assert "date_trunc('month', CURRENT_TIMESTAMP)" in SQL_OPERACAO_TOKENS
    assert "modelo_ia" in SQL_OPERACAO_PRECOS
    assert texto_p95_usd(None) == "sem amostras"
    assert texto_p95_usd("0.00185") == "US$ 0.001850"
    assert texto_comparacao_p95("0.002000", "0.001000") == "Diferença do p95: +0.001000 USD."
    assert texto_comparacao_p95(None, "0.1") is None
    assert (
        texto_precos_janela([{"modelo_ia": "gemini-3.8-flash", "versao_preco_ia": "2026-10", "total": 2}])
        == "Preços usados nestes 14 dias: gemini-3.8-flash 2026-10 (2)."
    )
