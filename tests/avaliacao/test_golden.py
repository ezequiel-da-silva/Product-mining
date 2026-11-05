"""Golden set sem rede: extração, ângulo e fornecedor."""

import json

import pytest
from pydantic import ValidationError
from schemas import NICHOS_CANONICOS, AnaliseAnuncioSchema, AnguloVenda, GeracaoAnguloSchema
from src.provas_sociais import extrair_provas
from src.sourcing import escolher_cogs, extrair_fornecedores


def _analise(**extra) -> dict:
    dados = {
        "transcricao_video": "A locução apresenta o produto.",
        "gancho_3s": "O produto aparece na mão.",
        "nicho": "Pet",
        "gatilho_mental": "Prova social",
        "objecoes_quebradas": ["frete", "prazo", "confiança"],
        "preco_oferta_estimado": 49.9,
        "preco_moeda": "BRL",
        "margem_estimada_pct": 55,
    }
    dados.update(extra)
    return dados


def test_extracao_aceita_um_caso_por_nicho_e_as_objecoes():
    for nicho in NICHOS_CANONICOS:
        extra = {"nicho": nicho}
        if nicho == "Outros":
            extra["sub_nicho"] = "Acessório de viagem"
        esquema = AnaliseAnuncioSchema(**_analise(**extra))
        assert esquema.nicho == nicho
        assert esquema.objecoes_quebradas == ["frete", "prazo", "confiança"]


def test_eur_descarta_o_preco_e_nicho_ou_fornecedor_rejeitam():
    euro = AnaliseAnuncioSchema(**_analise(preco_moeda="EUR"))
    assert euro.preco_oferta_estimado is None
    assert euro.preco_moeda is None
    with pytest.raises(ValidationError):
        AnaliseAnuncioSchema(**_analise(nicho="Pet Shop"))
    with pytest.raises(ValidationError):
        AnaliseAnuncioSchema(**_analise(fornecedor="https://detail.1688.com/offer/1.html"))


def test_angulo_aceita_um_e_rejeita_dois():
    angulo = AnguloVenda(titulo="Tempo", foco_emocional="Praticidade", copy_sugerida="Ganhe o dia.")
    gerado = GeracaoAnguloSchema(angulos_alternativos=[angulo])
    assert len(gerado.angulos_alternativos) == 1
    with pytest.raises(ValidationError):
        GeracaoAnguloSchema(angulos_alternativos=[angulo, angulo])


def test_fornecedor_prefere_1688_e_limita_provas():
    html = """
    <script type="application/json">
    {"items":[
      {"productUrl":"https://detail.1688.com/offer/222.html","price":"12.00","currency":"CNY","similarity":0.90},
      {"productUrl":"https://www.aliexpress.com/item/333.html","price":"3.00","currency":"USD","similarity":0.95},
      {"productUrl":"https://detail.1688.com/offer/111.html","price":"1.00","currency":"CNY","similarity":0.50}
    ]}
    </script>
    """
    escolhido = escolher_cogs(extrair_fornecedores(html))
    assert escolhido is not None
    assert escolhido.origem == "1688"
    assert escolhido.moeda == "CNY"
    assert escolhido.url.endswith("/222.html")
    reviews = [
        {"@type": "Review", "reviewRating": {"ratingValue": 5}, "reviewBody": f"ok {indice}"} for indice in range(6)
    ]
    pagina = '<script type="application/ld+json">' + json.dumps(reviews) + "</script>"
    assert len(extrair_provas(pagina)) == 5
