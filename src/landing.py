"""Lê o preço público da página de destino, sem herdar o da oferta irmã."""

import ipaddress
import json
import logging
import os
import re
import socket
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
import urllib3

logger = logging.getLogger(__name__)

MAX_BYTES = 1_000_000
MAX_REDIRECIONAMENTOS = 3
PRECO_MAXIMO = Decimal("1000000")
TIMEOUT = (3, 5)
HOSTS_SEM_PROXY = frozenset({"olinda.bcb.gov.br", "economia.awesomeapi.com.br"})
_CURL: tuple[object, object] | None = None
_CURL_TENTADO = False
_JSON_LD = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_META_PRECO = re.compile(
    r"<meta[^>]+(?:property|name)=[\"'](?:product:price:amount|og:price:amount)[\"'][^>]*>",
    re.IGNORECASE,
)
_CONTEUDO_META = re.compile(r"content=[\"']([^\"']+)[\"']", re.IGNORECASE)
_PRECO_REAL = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d+\.\d{2})(?!\d)")
_PRECO_DOLAR = re.compile(r"(?<![A-Za-z])(?:US\$|\$)\s*(\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})(?!\d)")
_META_MOEDA = re.compile(
    r"<meta[^>]+(?:property|name)=[\"'](?:product:price:currency|og:price:currency)[\"'][^>]*>",
    re.IGNORECASE,
)
_HOSTS_PROIBIDOS = {"localhost", "metadata.google.internal"}
_MOEDAS = {"BRL", "USD"}


@dataclass(frozen=True)
class PrecoLido:
    valor: Decimal
    moeda: str


def _ip_proibido(texto: str) -> bool:
    try:
        endereco = ipaddress.ip_address(texto.split("%", 1)[0])
    except ValueError:
        return True
    return any(
        (
            endereco.is_private,
            endereco.is_loopback,
            endereco.is_link_local,
            endereco.is_reserved,
            endereco.is_multicast,
            endereco.is_unspecified,
        )
    )


def resolver_publico(hostname: str) -> list[str] | None:
    """Resolve uma vez. Recusa o nome se qualquer endereço não for público."""
    nome = hostname.strip(".").lower()
    if not nome or nome in _HOSTS_PROIBIDOS or nome.endswith(".local"):
        return None
    if _parece_ip(nome):
        literal = nome.split("%", 1)[0]
        if _ip_proibido(literal):
            return None
        return [literal]
    try:
        enderecos = socket.getaddrinfo(nome, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None
    ips: list[str] = []
    for item in enderecos:
        ip = item[4][0]
        if _ip_proibido(ip):
            return None
        if ip not in ips:
            ips.append(ip)
    if not ips:
        return None
    return ips


def host_publico(hostname: str) -> bool:
    return resolver_publico(hostname) is not None


def _conexao_no_ip(ip: str, base: type) -> type:
    class ConexaoNoIp(base):
        def _new_conn(self):  # type: ignore[no-untyped-def]
            nome = self._dns_host
            self._dns_host = ip
            try:
                return super()._new_conn()
            finally:
                self._dns_host = nome

    return ConexaoNoIp


class _GerenteIpFixo(urllib3.PoolManager):
    def __init__(self, ip: str, nome: str, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._ip = ip
        self._nome = nome

    def _new_pool(self, scheme, host, port, request_context=None):  # type: ignore[no-untyped-def]
        contexto = dict(request_context) if request_context is not None else None
        if scheme == "https":
            if contexto is None:
                contexto = dict(self.connection_pool_kw)
            contexto["server_hostname"] = self._nome
            contexto["assert_hostname"] = self._nome
        pool = super()._new_pool(scheme, host, port, request_context=contexto)
        pool.ConnectionCls = _conexao_no_ip(self._ip, pool.ConnectionCls)
        return pool


class _AdaptadorIpFixo(requests.adapters.HTTPAdapter):
    def __init__(self, ip: str, nome: str) -> None:
        self._ip = ip
        self._nome = nome
        super().__init__()

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):  # type: ignore[no-untyped-def]
        self._pool_connections = connections
        self._pool_maxsize = maxsize
        self._pool_block = block
        self.poolmanager = _GerenteIpFixo(
            self._ip,
            self._nome,
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )


def _proxies_egress() -> list[str]:
    bruto = os.environ.get("EGRESS_PROXIES", "")
    return [item.strip() for item in bruto.split(",") if item.strip().startswith(("http://", "https://"))]


def _proxy_para(hostname: str, tentativa: int) -> str | None:
    if hostname in HOSTS_SEM_PROXY:
        return None
    proxies = _proxies_egress()
    if not proxies:
        return None
    return proxies[(sum(hostname.encode()) + tentativa) % len(proxies)]


def mapa_resolve(hostname: str, url: str, ip: str) -> dict[str, str]:
    """libcurl exige HOST:PORT. A porta fica na chave, o IP no valor."""
    partes = urlsplit(url)
    if partes.port:
        porta = partes.port
    elif url.startswith("https"):
        porta = 443
    else:
        porta = 80
    return {f"{hostname}:{porta}": ip}


def _entradas_libcurl(mapa: dict[str, str]) -> list[str]:
    return [f"{chave}:{endereco}" for chave, endereco in mapa.items()]


def _modulo_curl() -> tuple[object, object] | None:
    global _CURL, _CURL_TENTADO
    if _CURL_TENTADO:
        return _CURL
    _CURL_TENTADO = True
    try:
        from curl_cffi import CurlOpt
        from curl_cffi import requests as curl
    except ImportError:
        _CURL = None
    else:
        _CURL = (curl, CurlOpt)
    return _CURL


def _cabecalhos_navegacao() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Sec-CH-UA": '"Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1",
    }


def _abrir_chrome(url: str, hostname: str, ip: str, proxy: str | None):
    modulo = _modulo_curl()
    if modulo is None:
        raise RuntimeError("curl_cffi ausente")
    curl, curl_opt = modulo
    mapa = mapa_resolve(hostname, url, ip)
    opcoes = {curl_opt.RESOLVE: _entradas_libcurl(mapa)}
    pedido = {
        "impersonate": "chrome",
        "timeout": 5,
        "allow_redirects": False,
        "stream": True,
        "headers": _cabecalhos_navegacao(),
        "curl_options": opcoes,
    }
    if proxy:
        pedido["proxy"] = proxy
    return curl.get(url, **pedido)


def _abrir_requests(url: str, hostname: str, ip: str) -> requests.Response:
    """Abre o socket no IP já validado e mantém o SNI/Host do nome original."""
    sessao = requests.Session()
    sessao.trust_env = False
    adaptador = _AdaptadorIpFixo(ip, hostname)
    sessao.mount("https://", adaptador)
    sessao.mount("http://", adaptador)
    try:
        resposta = sessao.get(
            url,
            allow_redirects=False,
            timeout=TIMEOUT,
            stream=True,
            headers={"User-Agent": "ProductMining/1.0"},
        )
    except requests.RequestException:
        sessao.close()
        raise
    fechar_resposta = resposta.close

    def fechar() -> None:
        try:
            fechar_resposta()
        finally:
            sessao.close()

    resposta.close = fechar  # type: ignore[method-assign]
    return resposta


def _abrir_pagina(url: str, hostname: str, ip: str, tentativa: int = 0):
    proxy = _proxy_para(hostname, tentativa)
    if _modulo_curl() is not None:
        return _abrir_chrome(url, hostname, ip, proxy)
    if proxy:
        logger.info("Proxy de egress ignorado: curl_cffi ausente.")
    return _abrir_requests(url, hostname, ip)


def _parece_ip(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        return False
    return True


def _moeda_explicita(texto: object) -> str | None:
    if not isinstance(texto, str) or not texto.strip():
        return None
    return texto.strip().upper()


def _prefixo_moeda(texto: str) -> tuple[str | None, str]:
    cru = texto.strip()
    if cru.upper().startswith("R$"):
        return "BRL", cru[2:]
    if cru.upper().startswith("US$"):
        return "USD", cru[3:]
    if cru.startswith("$"):
        return "USD", cru[1:]
    return None, cru


def _classificar_numero(texto: str) -> tuple[Decimal, str] | None:
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{2}", texto) or re.fullmatch(r"\d+,\d{2}", texto):
        return Decimal(texto.replace(".", "").replace(",", ".")), "br"
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d{2}", texto):
        return Decimal(texto.replace(",", "")), "us"
    if re.fullmatch(r"\d+\.\d{2}", texto):
        return Decimal(texto), "decimal"
    if re.fullmatch(r"\d+", texto):
        return Decimal(texto), "inteiro"
    return None


def _combinar(numero: Decimal, forma: str, moeda: str | None) -> PrecoLido | None:
    if moeda is not None and moeda not in _MOEDAS:
        return None
    if forma == "br":
        if moeda not in {None, "BRL"}:
            return None
        moeda = "BRL"
    elif forma == "us":
        if moeda not in {None, "USD"}:
            return None
        moeda = "USD"
    elif moeda not in _MOEDAS:
        return None
    if not numero.is_finite() or numero <= 0 or numero > PRECO_MAXIMO:
        return None
    return PrecoLido(numero.quantize(Decimal("0.01")), moeda)


def ler_preco(valor: object, moeda: str | None = None) -> PrecoLido | None:
    """Aceita BRL ou USD quando o número e a moeda não disputam o mesmo valor."""
    if isinstance(valor, bool) or isinstance(valor, (list, dict)):
        return None
    if isinstance(valor, (int, float)):
        try:
            numero = Decimal(str(valor))
        except InvalidOperation:
            return None
        forma = "inteiro" if numero == numero.to_integral_value() else "decimal"
        return _combinar(numero, forma, moeda)
    if not isinstance(valor, str):
        return None
    prefixo, resto = _prefixo_moeda(valor)
    if prefixo and moeda and prefixo != moeda:
        return None
    classificado = _classificar_numero(resto.strip().replace(" ", ""))
    if classificado is None:
        return None
    numero, forma = classificado
    return _combinar(numero, forma, moeda or prefixo)


def _preco_no_objeto(node: object, profundidade: int = 0) -> PrecoLido | None:
    if profundidade > 6:
        return None
    if isinstance(node, dict):
        if "price" in node:
            preco = ler_preco(node.get("price"), _moeda_explicita(node.get("priceCurrency")))
            if preco is not None:
                return preco
        for chave in ("offers", "offer", "@graph"):
            if chave in node:
                preco = _preco_no_objeto(node[chave], profundidade + 1)
                if preco is not None:
                    return preco
    if isinstance(node, list):
        for item in node:
            preco = _preco_no_objeto(item, profundidade + 1)
            if preco is not None:
                return preco
    return None


def _moeda_na_meta(html: str) -> str | None:
    for meta in _META_MOEDA.findall(html):
        conteudo = _CONTEUDO_META.search(meta)
        if conteudo:
            return _moeda_explicita(conteudo.group(1))
    return None


def extrair_preco_html(html: str) -> PrecoLido | None:
    for bloco in _JSON_LD.findall(html):
        try:
            dados = json.loads(bloco)
        except json.JSONDecodeError:
            continue
        preco = _preco_no_objeto(dados)
        if preco is not None:
            return preco
    moeda_meta = _moeda_na_meta(html)
    for meta in _META_PRECO.findall(html):
        conteudo = _CONTEUDO_META.search(meta)
        if conteudo:
            preco = ler_preco(conteudo.group(1), moeda_meta)
            if preco is not None:
                return preco
    for encontrado in _PRECO_REAL.finditer(html):
        antes = html[max(0, encontrado.start() - 12) : encontrado.start()].casefold()
        if "doar" in antes:
            continue
        preco = ler_preco(encontrado.group(1), "BRL")
        if preco is not None:
            return preco
    dolar = _PRECO_DOLAR.search(html)
    if dolar:
        return ler_preco(dolar.group(0))
    return None


def _ler_corpo(resposta: requests.Response) -> str | None:
    tipo = (resposta.headers.get("Content-Type") or "").lower()
    if tipo and not any(parte in tipo for parte in ("html", "json", "text")):
        return None
    pedacos: list[bytes] = []
    total = 0
    for pedaco in resposta.iter_content(16_384):
        if not pedaco:
            continue
        total += len(pedaco)
        if total > MAX_BYTES:
            return None
        pedacos.append(pedaco)
    if not pedacos:
        return None
    return b"".join(pedacos).decode("utf-8", errors="replace")


def buscar_html(url: str, tentativa: int = 0) -> str | None:
    """Lê o HTML no IP já validado. Recusa rede local e não segue redirect às cegas."""
    atual = url
    for _ in range(MAX_REDIRECIONAMENTOS + 1):
        partes = urlsplit(atual)
        if partes.scheme not in {"http", "https"} or not partes.hostname:
            return None
        ips = resolver_publico(partes.hostname)
        if not ips:
            logger.info("Página recusada por host não público.")
            return None
        try:
            resposta = _abrir_pagina(atual, partes.hostname, ips[0], tentativa)
        except Exception:
            logger.info("Falha ao ler a página.")
            return None
        try:
            if resposta.is_redirect:
                destino = resposta.headers.get("Location")
                if not destino:
                    return None
                atual = (
                    destino if destino.startswith(("http://", "https://")) else requests.compat.urljoin(atual, destino)
                )
                continue
            if resposta.status_code != 200:
                return None
            return _ler_corpo(resposta)
        finally:
            resposta.close()
    return None


def codigo_http(url: str) -> int | None:
    """Status da URL, sem corpo. 404 e 410 do fornecedor usam este caminho."""
    partes = urlsplit(url)
    if partes.scheme not in {"http", "https"} or not partes.hostname:
        return None
    ips = resolver_publico(partes.hostname)
    if not ips:
        return None
    try:
        resposta = _abrir_pagina(url, partes.hostname, ips[0])
    except Exception:
        logger.info("Falha ao ler o status da página.")
        return None
    try:
        return int(resposta.status_code)
    finally:
        resposta.close()


def buscar_preco(url: str) -> PrecoLido | None:
    """Busca o preço na URL desta oferta."""
    corpo = buscar_html(url)
    if not corpo:
        return None
    return extrair_preco_html(corpo)


MIN_CORPO_PAGINA = 800
_MARCAS_BLOQUEIO = (
    "cf-turnstile",
    "challenges.cloudflare.com",
    "g-recaptcha",
    "hcaptcha",
    "just a moment",
)
_PRODUTO_SHOPIFY = re.compile(r"/products/([^/?#]+)")


@dataclass(frozen=True)
class LeituraPagina:
    origem: str
    preco: PrecoLido | None = None


def _preco_json_ld(html: str) -> PrecoLido | None:
    for bloco in _JSON_LD.findall(html):
        try:
            dados = json.loads(bloco)
        except json.JSONDecodeError:
            continue
        preco = _preco_no_objeto(dados)
        if preco is not None:
            return preco
    return None


def _preco_meta(html: str) -> PrecoLido | None:
    moeda_meta = _moeda_na_meta(html)
    for meta in _META_PRECO.findall(html):
        conteudo = _CONTEUDO_META.search(meta)
        if conteudo:
            preco = ler_preco(conteudo.group(1), moeda_meta)
            if preco is not None:
                return preco
    return None


def _pagina_bloqueada(html: str) -> bool:
    if len(html.encode("utf-8")) < MIN_CORPO_PAGINA:
        return True
    texto = html.casefold()
    return any(marca in texto for marca in _MARCAS_BLOQUEIO)


def classificar_pagina(html: str) -> LeituraPagina:
    """Corpo curto ou desafio vira bloqueado. JSON-LD e meta divergentes não gravam preço."""
    if not isinstance(html, str) or _pagina_bloqueada(html):
        return LeituraPagina("bloqueado")
    json_ld = _preco_json_ld(html)
    meta = _preco_meta(html)
    if json_ld is not None and meta is not None and (json_ld.valor != meta.valor or json_ld.moeda != meta.moeda):
        return LeituraPagina("divergente")
    if json_ld is not None:
        return LeituraPagina("json_ld", json_ld)
    if meta is not None:
        return LeituraPagina("html", meta)
    texto = extrair_preco_html(html)
    if texto is not None:
        return LeituraPagina("html", texto)
    return LeituraPagina("ausente")


def _e_shopify(url: str) -> bool:
    return "shopify" in url.casefold()


def _url_produto_js(url: str) -> str | None:
    partes = urlsplit(url)
    encontrado = _PRODUTO_SHOPIFY.search(partes.path or "")
    if not encontrado or not partes.hostname:
        return None
    handle = encontrado.group(1)
    if handle.endswith(".js"):
        handle = handle[:-3]
    if not handle:
        return None
    return f"https://{partes.hostname}/products/{handle}.js"


def _moeda_do_json(dados: dict) -> str | None:
    for chave in ("currency", "priceCurrency", "currencyCode", "price_currency"):
        moeda = _moeda_explicita(dados.get(chave))
        if moeda in _MOEDAS:
            return moeda
    return None


def _preco_centavos(valor: object, moeda: str) -> PrecoLido | None:
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, str) and re.fullmatch(r"\d+", valor.strip()):
        valor = int(valor.strip())
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    if isinstance(valor, int):
        texto = f"{(Decimal(valor) / Decimal(100)):.2f}"
        return ler_preco(texto, moeda)
    if isinstance(valor, str):
        return ler_preco(valor, moeda)
    return None


def _preco_shopify_json(texto: str) -> PrecoLido | None:
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError:
        return None
    if not isinstance(dados, dict):
        return None
    moeda = _moeda_do_json(dados)
    bruto = dados.get("price")
    if moeda is None and isinstance(dados.get("variants"), list):
        for variante in dados["variants"]:
            if isinstance(variante, dict):
                moeda = _moeda_do_json(variante)
                bruto = variante.get("price")
                if moeda:
                    break
    if moeda not in _MOEDAS:
        return None
    return _preco_centavos(bruto, moeda)


def _shopify_quando_falha(url: str, leitura: LeituraPagina) -> LeituraPagina:
    if leitura.origem not in {"ausente", "bloqueado"} or not _e_shopify(url):
        return leitura
    destino = _url_produto_js(url)
    if not destino:
        return leitura
    corpo = buscar_html(destino)
    if not isinstance(corpo, str):
        return leitura
    preco = _preco_shopify_json(corpo)
    if preco is None:
        return leitura
    return LeituraPagina("shopify_json", preco)


def _rodadas_egress(url: str) -> int:
    host = urlsplit(url).hostname or ""
    if host in HOSTS_SEM_PROXY:
        return 1
    return max(1, len(_proxies_egress()))


def avaliar_destino(url: str) -> LeituraPagina:
    """Classifica a página. Shopify .js só entra se a página veio ausente ou bloqueada."""
    if not isinstance(url, str) or not url.strip():
        return LeituraPagina("ausente")
    endereco = url.strip()
    leitura = LeituraPagina("bloqueado")
    for tentativa in range(_rodadas_egress(endereco)):
        corpo = buscar_html(endereco, tentativa)
        if not isinstance(corpo, str):
            leitura = LeituraPagina("bloqueado")
            continue
        leitura = classificar_pagina(corpo)
        if leitura.origem != "bloqueado":
            break
    return _shopify_quando_falha(endereco, leitura)
