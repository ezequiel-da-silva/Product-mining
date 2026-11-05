"""Contratos das fronteiras operacionais: página, câmbio, CDP, DLQ e partições."""

import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from src.cambio import interpretar_awesome, interpretar_bacen, margem_em_brl
from src.landing import LeituraPagina, classificar_pagina
from src.midia_r2 import extrair_chave
from src.reprocesso_ia import classe_da_excecao


def _pagina(miolo: str) -> str:
    return miolo + (" " * 800)


def test_corpo_curto_ou_desafio_e_bloqueado():
    assert classificar_pagina("<p>R$ 10,00</p>").origem == "bloqueado"
    assert classificar_pagina(_pagina("cf-turnstile")).origem == "bloqueado"
    assert classificar_pagina(_pagina("challenges.cloudflare.com")).origem == "bloqueado"
    assert classificar_pagina(_pagina("g-recaptcha")).origem == "bloqueado"
    assert classificar_pagina(_pagina("hcaptcha")).origem == "bloqueado"
    assert classificar_pagina(_pagina("Just a moment")).origem == "bloqueado"


def test_json_ld_e_meta_divergentes_nao_gravam_preco():
    html = _pagina(
        '<script type="application/ld+json">{"offers":{"price":"10.00","priceCurrency":"BRL"}}</script>'
        '<meta property="product:price:amount" content="20.00">'
        '<meta property="product:price:currency" content="BRL">'
    )
    leitura = classificar_pagina(html)
    assert leitura == LeituraPagina("divergente")
    assert leitura.preco is None


def test_shopify_sem_moeda_permanece_ausente(monkeypatch):
    import src.landing as landing

    html = _pagina("<p>sem preço visível nesta vitrine grande o bastante</p>")
    monkeypatch.setattr(
        landing,
        "buscar_html",
        lambda url, _tentativa=0: html if "myshopify" in url and not url.endswith(".js") else '{"price":4990}',
    )

    leitura = landing.avaliar_destino("https://loja.myshopify.com/products/camiseta")

    assert leitura.origem == "ausente"
    assert leitura.preco is None


def test_shopify_com_moeda_entra_em_centavos(monkeypatch):
    import src.landing as landing

    html = _pagina("<p>sem preço visível nesta vitrine grande o bastante</p>")

    def html_de(url: str, _tentativa: int = 0):
        if url.endswith(".js"):
            return '{"price":4990,"currency":"BRL"}'
        return html

    monkeypatch.setattr(landing, "buscar_html", html_de)
    leitura = landing.avaliar_destino("https://loja.myshopify.com/products/camiseta")
    assert leitura.origem == "shopify_json"
    assert leitura.preco.valor == Decimal("49.90")
    assert leitura.preco.moeda == "BRL"


def test_bloqueado_nao_marca_consulta_e_a_fila_repete(monkeypatch):
    from src.comercial import SQL_CONSULTA_BLOQUEADA, SQL_PRECOS_AUSENTES, aplicar_preco_lido

    cursor = MagicMock()
    cursor.rowcount = 1
    conexao = MagicMock()
    conexao.cursor.return_value.__enter__.return_value = cursor
    monkeypatch.setattr("src.comercial.avaliar_destino", lambda _url: LeituraPagina("bloqueado"))

    assert aplicar_preco_lido(conexao, 3, "https://loja.example/p") is True
    consulta = cursor.execute.call_args.args[0]
    assert consulta == SQL_CONSULTA_BLOQUEADA
    assert "preco_pagina_consultado_em" not in consulta
    assert "preco_origem = 'bloqueado'" in SQL_PRECOS_AUSENTES


def test_paridade_e_margem_quantizam_sem_rede():
    assert interpretar_bacen('{"value":[{"cotacaoVenda":5.4321}]}') == Decimal("5.4321")
    assert interpretar_awesome('{"USDBRL":{"bid":"5.10"},"CNYBRL":{"bid":"0.70"}}')["CNY"] == Decimal("0.70")
    assert margem_em_brl(100, 40) == 60


def test_chave_de_review_entra_na_leitura():
    chave = "reviews/7/" + ("a" * 32) + ".webp"
    assert extrair_chave(chave) == chave
    assert extrair_chave("https://cdn.example/" + chave) == chave


def test_classe_separa_estrutural_de_transporte():
    assert classe_da_excecao(json.JSONDecodeError("x", "x", 0)) == "estrutural"
    assert classe_da_excecao(ValueError("schema")) == "estrutural"
    assert classe_da_excecao(TimeoutError()) == "transporte"


def test_cdp_escolhe_a_unica_aba_da_biblioteca(monkeypatch):
    from src.producer import _escolher_pagina_cdp

    biblioteca = MagicMock(url="https://www.facebook.com/ads/library/?q=pet")
    outra = MagicMock(url="https://www.facebook.com/")
    contexto = MagicMock(pages=[outra, biblioteca])
    monkeypatch.delenv("CDP_TARGET_URL", raising=False)
    assert _escolher_pagina_cdp(contexto) is biblioteca

    contexto.pages = [biblioteca, MagicMock(url="https://www.facebook.com/ads/library/?q=casa")]
    with pytest.raises(RuntimeError, match="CDP_TARGET_URL"):
        _escolher_pagina_cdp(contexto)


def test_corpo_graphql_acima_do_teto_e_descartado():
    from src.producer import LIMITE_CORPO_GRAPHQL, _corpo_graphql

    resposta = MagicMock()
    resposta.body.return_value = b"x" * (LIMITE_CORPO_GRAPHQL + 1)
    assert _corpo_graphql(resposta) is None


def test_particoes_recriam_o_hnsw_na_mesma_funcao():
    from pathlib import Path

    sql = Path("infra/migrations/024_particoes_oferta_vetor.sql").read_text(encoding="utf-8")
    assert "SECURITY DEFINER" in sql
    assert "search_path = public" in sql
    assert "DROP INDEX IF EXISTS public.idx_oferta_vetor_embedding" in sql
    assert "CREATE INDEX" in sql and "hnsw" in sql
    assert "18 months" in sql
    assert "REVOKE ALL ON FUNCTION manter_particoes_oferta_vetor() FROM PUBLIC" in sql
    assert "GRANT EXECUTE ON FUNCTION manter_particoes_oferta_vetor() TO lifecycle_writer" in sql
    assert sql.index("DROP INDEX IF EXISTS public.idx_oferta_vetor_embedding") < sql.index("USING hnsw")


def test_busca_poda_dezoito_meses_sem_parametro_novo():
    from src.dashboard import OFERTAS_BUSCA_SQL

    assert "vetor.mes_captura >=" in OFERTAS_BUSCA_SQL
    assert "18 months" in OFERTAS_BUSCA_SQL
    assert OFERTAS_BUSCA_SQL.count("%s::vector") == 2


def test_exportacao_nasce_no_browser_sem_video_nem_foto():
    from src.dashboard import SQL_EXPORTACAO_ESTRELADAS, html_exportacao, texto_aderencia

    assert "url_video_r2" not in SQL_EXPORTACAO_ESTRELADAS
    assert "transcricao_video" not in SQL_EXPORTACAO_ESTRELADAS
    assert "foto_url" not in SQL_EXPORTACAO_ESTRELADAS
    html = html_exportacao([{"id": 1, "nicho": "Pet"}])
    assert "Baixar CSV" in html
    assert "Baixar JSON" in html
    assert "new Blob" in html
    assert texto_aderencia(3, 1) == "Aderência: 75.0% (3 aderentes, 1 estruturais)."


def test_alerta_acima_de_25_posta_texto(monkeypatch):
    import src.operacao as operacao

    enviados = {}

    def post(url, json=None, timeout=None, allow_redirects=None):
        enviados["url"] = url
        enviados["json"] = json
        return MagicMock(status_code=200)

    monkeypatch.setattr(operacao, "_pode_alertar", lambda: True)
    monkeypatch.setattr(
        operacao, "ler_segredo", lambda nome: "https://hooks.example/alerta" if nome == "ALERTA_WEBHOOK_URL" else ""
    )
    monkeypatch.setattr(operacao.requests, "post", post)
    operacao._postar_alerta(26)
    assert enviados["json"] == {"text": "DLQ de IA: 26 pendentes"}
