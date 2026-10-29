"""Lê o preço público da página de destino, sem herdar o da oferta irmã."""

import ipaddress
import json
import logging
import re
import socket
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
import urllib3

logger = logging.getLogger(__name__)

MAX_BYTES = 1_000_000
MAX_REDIRECIONAMENTOS = 3
PRECO_MAXIMO = Decimal("1000000")
TIMEOUT = (3, 5)
_JSON_LD = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_META_PRECO = re.compile(
    r"<meta[^>]+(?:property|name)=[\"'](?:product:price:amount|og:price:amount)[\"'][^>]*>",
    re.IGNORECASE,
)
_CONTEUDO_META = re.compile(r"content=[\"']([^\"']+)[\"']", re.IGNORECASE)
_PRECO_REAL = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2})")
_HOSTS_PROIBIDOS = {"localhost", "metadata.google.internal"}


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


def _abrir_pagina(url: str, hostname: str, ip: str) -> requests.Response:
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


def _parece_ip(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        return False
    return True


def _decimal_preco(valor: object) -> Decimal | None:
    if isinstance(valor, bool) or isinstance(valor, (list, dict)):
        return None
    if isinstance(valor, (int, float)):
        texto = str(valor)
    elif isinstance(valor, str):
        texto = valor.strip().replace("R$", "").replace(" ", "")
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{2}", texto) or re.fullmatch(r"\d+,\d{2}", texto):
            texto = texto.replace(".", "").replace(",", ".")
    else:
        return None
    try:
        numero = Decimal(texto)
    except InvalidOperation:
        return None
    if not numero.is_finite() or numero <= 0 or numero > PRECO_MAXIMO:
        return None
    return numero.quantize(Decimal("0.01"))


def _preco_no_objeto(node: object, profundidade: int = 0) -> Decimal | None:
    if profundidade > 6:
        return None
    if isinstance(node, dict):
        if "price" in node:
            preco = _decimal_preco(node.get("price"))
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


def extrair_preco_html(html: str) -> Decimal | None:
    for bloco in _JSON_LD.findall(html):
        try:
            dados = json.loads(bloco)
        except json.JSONDecodeError:
            continue
        preco = _preco_no_objeto(dados)
        if preco is not None:
            return preco
    for meta in _META_PRECO.findall(html):
        conteudo = _CONTEUDO_META.search(meta)
        if conteudo:
            preco = _decimal_preco(conteudo.group(1))
            if preco is not None:
                return preco
    encontrado = _PRECO_REAL.search(html)
    if not encontrado:
        return None
    return _decimal_preco(encontrado.group(1))


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


def buscar_preco(url: str) -> Decimal | None:
    """Busca o preço na URL desta oferta. Recusa rede local e não segue redirect às cegas."""
    atual = url
    for _ in range(MAX_REDIRECIONAMENTOS + 1):
        partes = urlsplit(atual)
        if partes.scheme not in {"http", "https"} or not partes.hostname:
            return None
        ips = resolver_publico(partes.hostname)
        if not ips:
            logger.info("Página de destino recusada por host não público.")
            return None
        try:
            resposta = _abrir_pagina(atual, partes.hostname, ips[0])
        except requests.RequestException:
            logger.info("Falha ao ler a página de destino.")
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
            corpo = _ler_corpo(resposta)
        finally:
            resposta.close()
        if not corpo:
            return None
        return extrair_preco_html(corpo)
    return None
