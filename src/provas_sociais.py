"""Depoimentos de 5 estrelas e fotos de clientes na página do fornecedor."""

import ipaddress
import re
from urllib.parse import urlsplit

from src.pagina_json import objetos_json

MAX_PROVAS = 5
MAX_TEXTO = 280
_HOSTS_LOCAIS = {"localhost", "metadata.google.internal"}
_TAGS = re.compile(r"<[^>]+>")
_NOTA_TEXTO = re.compile(r"\d+(?:\.0+)?")


def _ip_literal_proibido(host: str) -> bool:
    try:
        endereco = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
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


def url_http_publica(url: object) -> bool:
    """Aceita http(s) sem usuário, sem host local e sem IP privado literal."""
    if not isinstance(url, str) or not url.strip() or len(url) > 2000:
        return False
    partes = urlsplit(url.strip())
    if partes.scheme not in {"http", "https"} or not partes.hostname:
        return False
    if partes.username or partes.password:
        return False
    host = partes.hostname.lower().rstrip(".")
    if host in _HOSTS_LOCAIS or host.endswith(".local"):
        return False
    return not _ip_literal_proibido(host)


def _nota(valor: object) -> int | None:
    if isinstance(valor, dict):
        valor = valor.get("ratingValue")
    if isinstance(valor, str) and _NOTA_TEXTO.fullmatch(valor.strip()):
        valor = float(valor)
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    if float(valor) == 5.0:
        return 5
    return None


def _estrelas(item: dict) -> int | None:
    for chave in ("reviewRating", "ratingValue", "star", "stars"):
        nota = _nota(item.get(chave))
        if nota is not None:
            return nota
    return None


def _texto_limpo(valor: str) -> str:
    sem_tag = _TAGS.sub(" ", valor)
    return " ".join(sem_tag.split())[:MAX_TEXTO]


def _texto(item: dict) -> str:
    for chave in ("reviewBody", "content", "comment"):
        valor = item.get(chave)
        if isinstance(valor, str) and valor.strip():
            return _texto_limpo(valor)
    return ""


def _url_em(valor: object) -> str | None:
    if isinstance(valor, str) and url_http_publica(valor):
        return valor.strip()
    if isinstance(valor, dict):
        for chave in ("url", "contentUrl"):
            endereco = _url_em(valor.get(chave))
            if endereco:
                return endereco
        return None
    if isinstance(valor, list):
        for item in valor:
            endereco = _url_em(item)
            if endereco:
                return endereco
    return None


def _foto(item: dict) -> str | None:
    for chave in ("image", "photo", "picture", "images", "photos"):
        endereco = _url_em(item.get(chave))
        if endereco:
            return endereco
    return None


def _autor(item: dict) -> str | None:
    autor = item.get("author")
    if isinstance(autor, dict):
        autor = autor.get("name")
    if not isinstance(autor, str):
        return None
    limpo = " ".join(autor.split())[:80]
    return limpo or None


def _parece_review(item: dict) -> bool:
    tipo = item.get("@type")
    if isinstance(tipo, str) and tipo.lower() == "review":
        return True
    return any(chave in item for chave in ("reviewBody", "comment"))


def _prova(item: dict) -> dict | None:
    if not _parece_review(item) or _estrelas(item) != 5:
        return None
    texto = _texto(item)
    foto = _foto(item)
    if not texto and not foto:
        return None
    prova = {"estrelas": 5, "texto": texto}
    if foto:
        prova["foto_url"] = foto
    autor = _autor(item)
    if autor:
        prova["autor"] = autor
    return prova


def _coletar(node: object, achados: list[dict], profundidade: int) -> None:
    if profundidade > 8:
        return
    if isinstance(node, dict):
        prova = _prova(node)
        if prova is not None:
            achados.append(prova)
        for valor in node.values():
            _coletar(valor, achados, profundidade + 1)
        return
    if isinstance(node, list):
        for item in node:
            _coletar(item, achados, profundidade + 1)


def extrair_provas(html: str) -> list[dict]:
    """Até cinco depoimentos com nota exata de 5 e texto ou foto de cliente."""
    achados: list[dict] = []
    for dados in objetos_json(html):
        _coletar(dados, achados, 0)
    unicas: list[dict] = []
    vistos: set[tuple[str, str]] = set()
    for prova in achados:
        chave = (prova.get("texto", ""), prova.get("foto_url", ""))
        if chave in vistos:
            continue
        vistos.add(chave)
        unicas.append(prova)
        if len(unicas) == MAX_PROVAS:
            break
    return unicas
