import socket
from decimal import Decimal
from unittest.mock import MagicMock

from src.comercial import qualificar_pagina
from src.landing import buscar_preco, extrair_preco_html, host_publico


def test_extrai_preco_de_json_ld_e_de_real():
    html_ld = '<script type="application/ld+json">' '{"@type":"Product","offers":{"price":"49.90"}}' "</script>"
    assert extrair_preco_html(html_ld) == Decimal("49.90")
    assert extrair_preco_html("<p>por R$ 1.299,90 à vista</p>") == Decimal("1299.90")
    assert extrair_preco_html("<p>sem preço</p>") is None


def test_host_privado_e_metadata_sao_recusados(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 0))],
    )
    assert host_publico("loja.interna") is False
    assert host_publico("169.254.169.254") is False
    assert host_publico("localhost") is False


def test_busca_nao_chama_a_rede_quando_o_host_e_privado(monkeypatch):
    import src.landing as landing

    monkeypatch.setattr(landing, "host_publico", lambda _host: False)
    pedido = MagicMock()
    monkeypatch.setattr(landing.requests, "get", pedido)

    assert buscar_preco("http://127.0.0.1/admin") is None
    pedido.assert_not_called()


def test_qualificar_pagina_marca_dados_ausentes_quando_nao_ha_preco(monkeypatch):
    monkeypatch.setattr("src.comercial.buscar_preco", lambda _url: None)
    cursor = MagicMock()
    cursor.rowcount = 1
    conexao = MagicMock()
    conexao.cursor.return_value.__enter__.return_value = cursor

    assert qualificar_pagina(conexao, 7, "https://loja.example/produto") is True

    _consulta, params = cursor.execute.call_args.args
    assert params == {"preco": None, "ausentes": True, "id": 7}
    assert "dados_comerciais_ausentes" in cursor.execute.call_args.args[0]
    assert "pendente_lp" in cursor.execute.call_args.args[0]
