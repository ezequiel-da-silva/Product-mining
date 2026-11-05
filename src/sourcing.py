"""Busca reversa por imagem no 1688 e no AliExpress e escolhe o COGS."""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import urlencode, urlsplit

from src.pagina_json import objetos_json
from src.provas_sociais import url_http_publica

LIMIAR_SIMILARIDADE = 0.82
TOPO_POR_MERCADO = 3
PRECO_MAXIMO = Decimal("1000000")
_CHAVES_URL = ("productUrl", "detailUrl", "offerUrl", "productDetailUrl", "url")
_CHAVES_PRECO = ("price", "priceAmount", "salePrice", "priceValue")
_CHAVES_MOEDA = ("currency", "priceCurrency", "currencyCode")
_CHAVES_SIM = ("similarity", "simScore")
_URL_PRODUTO = re.compile(r"https://(?:[\w.-]+\.)?(?:aliexpress\.com/item/\d+\.html|detail\.1688\.com/offer/\d+\.html)")
_OG_IMAGE = re.compile(
    r"<meta[^>]+(?:property|name)=[\"']og:image[\"'][^>]*>",
    re.IGNORECASE,
)
_CONTEUDO = re.compile(r"content=[\"']([^\"']+)[\"']", re.IGNORECASE)


@dataclass(frozen=True)
class Candidato:
    origem: str
    url: str
    preco: Decimal
    moeda: str
    similaridade: float | None


def origem_da_url(url: object) -> str | None:
    if not isinstance(url, str) or not url_http_publica(url):
        return None
    partes = urlsplit(url.strip())
    host = (partes.hostname or "").lower()
    caminho = partes.path or ""
    if (host == "1688.com" or host.endswith(".1688.com")) and "/offer/" in caminho:
        return "1688"
    if (host == "aliexpress.com" or host.endswith(".aliexpress.com")) and "/item/" in caminho:
        return "aliexpress"
    return None


def url_busca_imagem(origem: str, imagem: str) -> str | None:
    if not url_http_publica(imagem):
        return None
    if origem == "1688":
        base = "https://s.1688.com/youyuan/index.htm"
        consulta = {"tab": "imageSearch", "imageAddress": imagem}
    elif origem == "aliexpress":
        base = "https://www.aliexpress.com/w/wholesale-imageSearch.html"
        consulta = {"imageUrl": imagem}
    else:
        return None
    return f"{base}?{urlencode(consulta)}"


def _decimal_simples(texto: str) -> Decimal | None:
    cru = texto.strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d{2}", cru) or re.fullmatch(r"\d+\.\d{2}", cru):
        return Decimal(cru.replace(",", ""))
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{2}", cru) or re.fullmatch(r"\d+,\d{2}", cru):
        return Decimal(cru.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d+", cru):
        return Decimal(cru)
    return None


def _moeda_explicita(texto: object) -> str | None:
    if not isinstance(texto, str) or not texto.strip():
        return None
    moeda = texto.strip().upper()
    if moeda in {"USD", "CNY"}:
        return moeda
    return None


def ler_cogs(valor: object, moeda: str | None, origem: str) -> tuple[Decimal, str] | None:
    """1688 só entra em CNY. AliExpress só entra em USD. Moeda ambígua é descartada."""
    explicita = _moeda_explicita(moeda)
    if isinstance(moeda, str) and moeda.strip() and explicita is None:
        return None
    numero = _numero_cogs(valor, explicita)
    if numero is None:
        return None
    numero, explicita = numero
    if explicita is None:
        explicita = "CNY" if origem == "1688" else "USD"
    if origem == "1688" and explicita != "CNY":
        return None
    if origem == "aliexpress" and explicita != "USD":
        return None
    if not numero.is_finite() or numero <= 0 or numero > PRECO_MAXIMO:
        return None
    return numero.quantize(Decimal("0.01")), explicita


def _numero_cogs(valor: object, explicita: str | None) -> tuple[Decimal, str | None] | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        try:
            return Decimal(str(valor)), explicita
        except InvalidOperation:
            return None
    if not isinstance(valor, str):
        return None
    cru = valor.strip()
    if cru.startswith(("¥", "￥")) or cru.upper().startswith("CN¥"):
        if explicita not in {None, "CNY"}:
            return None
        cru = cru.lstrip("¥￥")
        if cru.upper().startswith("CN¥"):
            cru = cru[3:]
        explicita = "CNY"
    elif cru.upper().startswith("US$") or cru.startswith("$"):
        if explicita not in {None, "USD"}:
            return None
        cru = cru[3:] if cru.upper().startswith("US$") else cru[1:]
        explicita = "USD"
    numero = _decimal_simples(cru.strip())
    if numero is None:
        return None
    return numero, explicita


def _similaridade(item: dict) -> float | None:
    for chave in _CHAVES_SIM:
        valor = item.get(chave)
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            continue
        if valor < 0 or valor > 1:
            continue
        return float(valor)
    return None


def _moeda_do_item(item: dict) -> str | None:
    for chave in _CHAVES_MOEDA:
        moeda = _moeda_explicita(item.get(chave))
        if moeda:
            return moeda
    return None


def _url_do_item(item: dict) -> str | None:
    for chave in _CHAVES_URL:
        valor = item.get(chave)
        if origem_da_url(valor):
            return str(valor).strip()
    return None


def _candidato(item: dict) -> Candidato | None:
    url = _url_do_item(item)
    if url is None:
        return None
    origem = origem_da_url(url)
    if origem is None:
        return None
    lido = None
    for chave in _CHAVES_PRECO:
        if chave in item:
            lido = ler_cogs(item.get(chave), _moeda_do_item(item), origem)
            if lido is not None:
                break
    if lido is None:
        return None
    preco, moeda = lido
    return Candidato(origem, url, preco, moeda, _similaridade(item))


def _coletar(node: object, achados: list[Candidato], profundidade: int) -> None:
    if profundidade > 8:
        return
    if isinstance(node, dict):
        candidato = _candidato(node)
        if candidato is not None:
            achados.append(candidato)
        for valor in node.values():
            _coletar(valor, achados, profundidade + 1)
        return
    if isinstance(node, list):
        for item in node:
            _coletar(item, achados, profundidade + 1)


def extrair_fornecedores(html: str) -> list[Candidato]:
    achados: list[Candidato] = []
    for dados in objetos_json(html):
        _coletar(dados, achados, 0)
    unicos: list[Candidato] = []
    vistos: set[str] = set()
    for candidato in achados:
        if candidato.url in vistos:
            continue
        vistos.add(candidato.url)
        unicos.append(candidato)
    return unicos


def urls_de_produto(html: str) -> list[str]:
    achados: list[str] = []
    for endereco in _URL_PRODUTO.findall(html):
        if endereco not in achados and origem_da_url(endereco):
            achados.append(endereco)
    return achados


def _melhor(candidatos: list[Candidato], origem: str) -> Candidato | None:
    do_mercado = [candidato for candidato in candidatos if candidato.origem == origem][:TOPO_POR_MERCADO]
    if not do_mercado:
        return None
    return min(do_mercado, key=lambda candidato: (candidato.preco, candidato.url))


def escolher_cogs(candidatos: list[Candidato]) -> Candidato | None:
    """Prefere a fábrica no 1688. Dentro do mercado, o menor preço entre os três primeiros."""
    aceitos = [
        candidato
        for candidato in candidatos
        if candidato.similaridade is None or candidato.similaridade >= LIMIAR_SIMILARIDADE
    ]
    return _melhor(aceitos, "1688") or _melhor(aceitos, "aliexpress")


def extrair_og_image(html: str) -> str | None:
    for meta in _OG_IMAGE.findall(html):
        conteudo = _CONTEUDO.search(meta)
        if conteudo and url_http_publica(conteudo.group(1)):
            return conteudo.group(1).strip()
    return None


def margem_com_cogs(venda: object, moeda_venda: object, cogs: Decimal, moeda_cogs: str) -> int | None:
    """Só recalcula a margem quando a venda e o COGS estão na mesma moeda."""
    if not isinstance(moeda_venda, str) or moeda_venda != moeda_cogs or isinstance(venda, bool):
        return None
    try:
        preco = Decimal(str(venda))
    except InvalidOperation:
        return None
    if not preco.is_finite() or preco <= 0 or cogs <= 0:
        return None
    if cogs >= preco:
        return 0
    pontos = ((preco - cogs) / preco * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return max(0, min(100, int(pontos)))
