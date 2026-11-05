from decimal import Decimal

from src.provas_sociais import extrair_provas, url_http_publica
from src.sourcing import (
    escolher_cogs,
    extrair_fornecedores,
    extrair_og_image,
    ler_cogs,
    margem_com_cogs,
    origem_da_url,
    url_busca_imagem,
    urls_de_produto,
)

BUSCA = """
<script type="application/json">
{"items":[
  {"productUrl":"https://detail.1688.com/offer/111.html","price":"12.00","currency":"CNY","similarity":0.91},
  {"productUrl":"https://detail.1688.com/offer/222.html","price":"8.50","currency":"CNY","similarity":0.93},
  {"productUrl":"https://detail.1688.com/offer/333.html","price":"7.00","currency":"CNY","similarity":0.20},
  {"productUrl":"https://www.aliexpress.com/item/999.html","price":"3.10","currency":"USD","similarity":0.99},
  {"url":"https://detail.1688.com/offer/444.html","price":true,"currency":"CNY"}
]}
</script>
<a href="https://detail.1688.com/offer/555.html">ficha</a>
"""

PAGINA = """
<script type="application/ld+json">
{"@type":"Review","reviewRating":{"ratingValue":"5"},"reviewBody":"<b>Chegou</b> inteiro",
 "author":{"name":"Ana"},"image":"https://img.example/cliente.jpg"}
</script>
<script type="application/ld+json">
{"@type":"Review","reviewRating":{"ratingValue":"4"},"reviewBody":"mais ou menos"}
</script>
<script type="application/ld+json">
{"@type":"Review","reviewRating":{"ratingValue":"5"},"author":{"name":"Lia"},
 "image":"javascript:alert(1)"}
</script>
"""


def test_busca_reversa_prefere_o_menor_cogs_de_fabrica():
    candidatos = extrair_fornecedores(BUSCA)

    escolhido = escolher_cogs(candidatos)

    assert escolhido is not None
    assert escolhido.origem == "1688"
    assert escolhido.url == "https://detail.1688.com/offer/222.html"
    assert escolhido.preco == Decimal("8.50")
    assert escolhido.moeda == "CNY"
    assert origem_da_url("https://s.1688.com/youyuan/index.htm") is None
    assert "imageAddress=" in url_busca_imagem("1688", "https://cdn.example/foto.jpg")
    assert "imageUrl=" in url_busca_imagem("aliexpress", "https://cdn.example/foto.jpg")
    assert urls_de_produto(BUSCA)[-1] == "https://detail.1688.com/offer/555.html"


def test_cogs_descarta_moeda_ambigua_e_so_recalcula_na_mesma_moeda():
    assert ler_cogs("8.50", None, "1688") == (Decimal("8.50"), "CNY")
    assert ler_cogs("¥ 4,50", "USD", "1688") is None
    assert ler_cogs("3.10", "BRL", "aliexpress") is None
    assert ler_cogs(True, "USD", "aliexpress") is None
    assert margem_com_cogs(20, "USD", Decimal("5.00"), "USD") == 75
    assert margem_com_cogs(20, "BRL", Decimal("8.50"), "CNY") is None
    assert margem_com_cogs(4, "USD", Decimal("9.00"), "USD") == 0


def test_provas_guardam_so_cinco_estrelas_com_foto_ou_texto():
    provas = extrair_provas(PAGINA)

    assert provas == [
        {
            "estrelas": 5,
            "texto": "Chegou inteiro",
            "foto_url": "https://img.example/cliente.jpg",
            "autor": "Ana",
        }
    ]
    assert url_http_publica("https://img.example/cliente.jpg")
    assert not url_http_publica("javascript:alert(1)")
    assert not url_http_publica("http://127.0.0.1/foto.jpg")
    assert not url_http_publica("http://localhost/foto.jpg")
    html = '<meta property="og:image" content="https://cdn.example/og.jpg">'
    assert extrair_og_image(html) == "https://cdn.example/og.jpg"
    assert extrair_og_image('<meta property="og:image" content="file:///tmp/a.jpg">') is None
