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

    monkeypatch.setattr(landing, "resolver_publico", lambda _host: None)
    pedido = MagicMock()
    monkeypatch.setattr(landing, "_abrir_pagina", pedido)

    assert buscar_preco("http://127.0.0.1/admin") is None
    pedido.assert_not_called()


def test_socket_abre_no_ip_validado_mesmo_se_o_dns_mudar(monkeypatch):
    import urllib3.util.connection as conexao_urllib

    consultas: list[str] = []

    def getaddrinfo(host, _port, *_args, **_kwargs):
        consultas.append(host)
        if host == "loja.example":
            ip = "1.1.1.1" if consultas.count("loja.example") == 1 else "169.254.169.254"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 0))]

    destinos: list[str] = []

    def criar(address, *_args, **_kwargs):
        destinos.append(address[0])
        raise OSError("sem rede")

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(conexao_urllib, "create_connection", criar)

    assert buscar_preco("https://loja.example/produto") is None
    assert destinos == ["1.1.1.1"]
    assert consultas.count("loja.example") == 1


def test_sni_permanece_o_nome_original(monkeypatch):
    import requests
    import urllib3.connection as conexao_https
    import urllib3.util.connection as conexao_urllib

    def getaddrinfo(host, _port, *_args, **_kwargs):
        if host == "loja.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 0))]

    def criar(_address, *_args, **_kwargs):
        return MagicMock()

    vistos: dict[str, str] = {}

    def embrulho(**kwargs):
        vistos["sni"] = kwargs["server_hostname"]
        vistos["nome"] = kwargs["assert_hostname"]
        raise requests.ConnectionError("sem tls")

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(conexao_urllib, "create_connection", criar)
    monkeypatch.setattr(conexao_https, "_ssl_wrap_socket_and_match_hostname", embrulho)

    assert buscar_preco("https://loja.example/produto") is None
    assert vistos == {"sni": "loja.example", "nome": "loja.example"}


def test_redirect_repete_a_checagem_e_nao_abre_host_privado(monkeypatch):
    import src.landing as landing

    def resolver(hostname: str):
        if hostname == "loja.example":
            return ["1.1.1.1"]
        return None

    aberturas: list[tuple[str, str]] = []

    def abrir(url, hostname, ip):
        aberturas.append((url, hostname, ip))
        resposta = MagicMock()
        resposta.is_redirect = True
        resposta.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        return resposta

    monkeypatch.setattr(landing, "resolver_publico", resolver)
    monkeypatch.setattr(landing, "_abrir_pagina", abrir)

    assert buscar_preco("https://loja.example/produto") is None
    assert aberturas == [("https://loja.example/produto", "loja.example", "1.1.1.1")]


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
