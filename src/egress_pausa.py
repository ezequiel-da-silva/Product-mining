"""Pausa o egress quando o bloqueio passa de 30% em cinco minutos. Não troca de proxy."""

import logging
import os
import time
import uuid

logger = logging.getLogger(__name__)

CHAVE_JANELA = "egress:janela"
CHAVE_PAUSA = "egress:pausa"
JANELA_SEGUNDOS = 300
MIN_AMOSTRAS = 20
LIMIAR = 0.30
PAUSA_SEGUNDOS = 900


def _cliente():
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis
    except ImportError:
        return None
    try:
        return redis.Redis.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.2)
    except Exception:
        logger.info("Redis do egress indisponível.")
        return None


def pausa_ativa() -> bool:
    cliente = _cliente()
    if cliente is None:
        return False
    try:
        return bool(cliente.get(CHAVE_PAUSA))
    except Exception:
        logger.info("Não foi possível ler a pausa do egress.")
        return False


def registrar_resultado(bloqueou: bool) -> None:
    if pausa_ativa():
        return
    cliente = _cliente()
    if cliente is None:
        return
    try:
        agora = time.time()
        cliente.zadd(CHAVE_JANELA, {f"{agora}:{int(bloqueou)}:{uuid.uuid4().hex}": agora})
        cliente.zremrangebyscore(CHAVE_JANELA, 0, agora - JANELA_SEGUNDOS)
        membros = cliente.zrange(CHAVE_JANELA, 0, -1)
        if len(membros) < MIN_AMOSTRAS:
            return
        bloqueios = sum(1 for item in membros if b":1:" in item)
        if bloqueios / len(membros) > LIMIAR:
            cliente.set(CHAVE_PAUSA, "1", ex=PAUSA_SEGUNDOS)
            logger.warning("Egress pausado: bloqueios acima de 30%% em 5 minutos.")
    except Exception:
        logger.info("Não foi possível registrar o egress.")
