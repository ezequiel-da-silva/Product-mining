"""Rascunho na Shopify. Sem token, a triagem segue só com o webhook."""

import os
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
from core.secrets import ler_segredo
from src.landing import host_publico
from src.midia_r2 import extrair_chave, url_leitura

MARKUP_PADRAO = Decimal("1.00")


def _markup() -> Decimal:
    bruto = os.environ.get("MARKUP_BRL", "").strip() or "1.00"
    try:
        valor = Decimal(bruto)
    except InvalidOperation:
        return MARKUP_PADRAO
    if not valor.is_finite() or valor <= 0:
        return MARKUP_PADRAO
    return valor


def _loja_publica(loja: str) -> str | None:
    nome = loja.strip().removeprefix("https://").removeprefix("http://").strip("/")
    host = urlsplit(f"https://{nome}").hostname
    if not host or not host_publico(host):
        return None
    return host


def fotos_assinadas(provas: object) -> list[str]:
    if not isinstance(provas, list):
        return []
    enderecos: list[str] = []
    for item in provas:
        bruto = item.get("foto_url") if isinstance(item, dict) else item
        if not isinstance(bruto, str):
            continue
        chave = extrair_chave(bruto.strip())
        if not chave:
            continue
        assinada = url_leitura(chave)
        if assinada:
            enderecos.append(assinada)
        if len(enderecos) == 5:
            break
    return enderecos


def rascunho_shopify(oferta: dict) -> str | None:
    """Cria um produto em rascunho. Devolve o id da loja, ou None se a loja não está configurada."""
    if os.environ.get("LOJA_DESTINO", "").strip() != "shopify":
        return None
    token = ler_segredo("SHOPIFY_ADMIN_TOKEN").strip()
    loja = _loja_publica(ler_segredo("LOJA_SHOPIFY_DOMINIO"))
    preco = oferta.get("venda_brl")
    if not token or not loja or preco is None:
        return None
    vendido = (Decimal(str(preco)) * _markup()).quantize(Decimal("0.01"))
    identificador_oferta = oferta.get("oferta_id", oferta.get("id"))
    corpo = {
        "product": {
            "title": str(oferta.get("nicho") or "Oferta")[:255],
            "body_html": str(oferta.get("copy_texto") or "")[:5000],
            "status": "draft",
            "variants": [{"price": str(vendido), "sku": f"oferta-{identificador_oferta}"}],
            "images": [{"src": url} for url in fotos_assinadas(oferta.get("provas_sociais"))],
        }
    }
    resposta = requests.post(
        f"https://{loja}/admin/api/2024-10/products.json",
        json=corpo,
        headers={"X-Shopify-Access-Token": token},
        timeout=(3, 15),
        allow_redirects=False,
    )
    if resposta.status_code not in {200, 201}:
        return None
    try:
        produto = resposta.json().get("product", {})
    except ValueError:
        return None
    identificador = produto.get("id") if isinstance(produto, dict) else None
    if identificador is None:
        return None
    return str(identificador)
