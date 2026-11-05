from decimal import Decimal
from unittest.mock import MagicMock

from src.fornecedor import aplicar_fornecedor, resolver_imagem

DESTINO = '<meta property="og:image" content="https://cdn.example/og.jpg">'
BUSCA = """
<script type="application/json">
{"items":[
  {"productUrl":"https://detail.1688.com/offer/222.html","price":"8.50","currency":"CNY","similarity":0.93}
]}
</script>
"""
PRODUTO = """
<script type="application/ld+json">
{"@type":"Review","reviewRating":{"ratingValue":5},"reviewBody":"Tecido firme",
 "image":"https://img.example/cliente.jpg"}
</script>
<script type="application/json">
{"offers":{"url":"https://detail.1688.com/offer/222.html","price":"8.50","priceCurrency":"CNY"}}
</script>
"""


def test_imagem_publica_vence_og_de_host_privado(monkeypatch):
    import src.fornecedor as fornecedor

    def publico(host: str) -> bool:
        return host == "cdn.example"

    monkeypatch.setattr(fornecedor, "host_publico", publico)

    assert resolver_imagem("https://cdn.example/criativo.jpg", DESTINO) == "https://cdn.example/criativo.jpg"
    assert resolver_imagem("http://127.0.0.1/secreta.jpg", DESTINO) == "https://cdn.example/og.jpg"
    assert resolver_imagem(None, "<html></html>") is None


def test_aplicar_fornecedor_grava_cogs_e_depoimento(monkeypatch):
    import src.fornecedor as fornecedor

    def html(url: str):
        if url.startswith("https://loja.example"):
            return DESTINO
        if "1688.com/youyuan" in url:
            return BUSCA
        if "aliexpress.com/w/" in url:
            return "<html></html>"
        if url == "https://detail.1688.com/offer/222.html":
            return PRODUTO
        return None

    monkeypatch.setattr(fornecedor, "buscar_html", html)
    monkeypatch.setattr(fornecedor, "host_publico", lambda _host: True)
    monkeypatch.setattr(
        fornecedor,
        "arquivar_fotos",
        lambda _oferta_id, provas: [
            {chave: valor for chave, valor in prova.items() if chave != "foto_url"} for prova in provas
        ],
    )
    monkeypatch.setattr(fornecedor, "completar_brl", lambda *_args, **_kwargs: False)
    cursor = MagicMock()
    cursor.rowcount = 1
    conexao = MagicMock()
    conexao.cursor.return_value.__enter__.return_value = cursor

    assert aplicar_fornecedor(
        conexao,
        {
            "id": 7,
            "url_destino": "https://loja.example/produto",
            "url_imagem_criativo": None,
            "preco_oferta_estimado": 100,
            "preco_moeda": "BRL",
        },
    )

    _consulta, params = cursor.execute.call_args.args
    assert params["cogs"] == Decimal("8.50")
    assert params["moeda"] == "CNY"
    assert params["origem"] == "1688"
    assert params["url"] == "https://detail.1688.com/offer/222.html"
    assert params["margem"] is None
    assert params["imagem"] == "https://cdn.example/og.jpg"
    assert "Tecido firme" in params["provas"]
    assert "foto_url" not in params["provas"]
    assert "https://img.example/cliente.jpg" not in params["provas"]
    conexao.commit.assert_called_once()


def test_sem_imagem_ainda_registra_a_tentativa(monkeypatch):
    import src.fornecedor as fornecedor

    monkeypatch.setattr(fornecedor, "buscar_html", lambda _url: None)
    monkeypatch.setattr(fornecedor, "host_publico", lambda _host: True)
    cursor = MagicMock()
    cursor.rowcount = 1
    conexao = MagicMock()
    conexao.cursor.return_value.__enter__.return_value = cursor

    assert aplicar_fornecedor(
        conexao,
        {"id": 8, "url_destino": None, "url_imagem_criativo": None, "preco_moeda": "USD"},
    )

    _consulta, params = cursor.execute.call_args.args
    assert params["cogs"] is None
    assert params["moeda"] is None
    assert params["provas"] is None
    assert "fornecedor_consultado_em = CURRENT_TIMESTAMP" in _consulta
