"""Estado da aba da Biblioteca. A publicação para; quem retoma é a pessoa no navegador."""

import logging
import os

logger = logging.getLogger(__name__)

PREFIXO_BIBLIOTECA = "https://www.facebook.com/ads/library"
CHAVE_ESTADO = "sessao:estado"
TTL_SEGUNDOS = 900


def classificar_sessao(urls: list[str]) -> str:
    biblioteca = [url for url in urls if url.startswith(PREFIXO_BIBLIOTECA)]
    if len(biblioteca) == 1:
        return "ok"
    if any("checkpoint" in url or "/login" in url for url in urls):
        return "sessao_pedida"
    return "aba_ausente"


def _cliente():
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis
    except ImportError:
        return None
    try:
        return redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.2, socket_timeout=0.2)
    except Exception:
        logger.info("Redis da sessão indisponível.")
        return None


def gravar_estado(estado: str) -> None:
    cliente = _cliente()
    if cliente is None:
        return
    try:
        cliente.set(CHAVE_ESTADO, estado, ex=TTL_SEGUNDOS)
    except Exception:
        logger.info("Não foi possível gravar sessao:estado.")


def estado_atual() -> str | None:
    cliente = _cliente()
    if cliente is None:
        return None
    try:
        valor = cliente.get(CHAVE_ESTADO)
    except Exception:
        logger.info("Não foi possível ler sessao:estado.")
        return None
    if valor is None:
        return None
    if isinstance(valor, bytes):
        return valor.decode("utf-8", errors="replace")
    return str(valor)


def sessao_permite_publicar() -> bool:
    """Sem Redis ou sem chave, a publicação segue. Chave diferente de ok pausa."""
    estado = estado_atual()
    return estado in {None, "ok"}
