"""Tarefas do beat na lifecycle_queue: DLQ, câmbio, integração e partições."""

import hashlib
import hmac
import json
import logging
import os
import time

import requests
from core.celery_app import app
from core.secrets import ler_segredo
from src.cambio import coletar_paridades, completar_brl, gravar_paridades
from src.reprocesso_ia import revalidar_texto

logger = logging.getLogger(__name__)

LIMITE_LOTE = 20
LIMIAR_ALERTA = 25
_carimbo_alerta = 0.0


def _pool():
    from src.lifecycle import get_pool

    return get_pool()


def _linhas(cur) -> list[dict]:
    linhas = cur.fetchall()
    if not isinstance(linhas, list):
        return []
    return [linha for linha in linhas if isinstance(linha, dict)]


@app.task(name="tasks.lifecycle.reprocessar_falhas_ia")
def reprocessar_falhas_ia() -> dict:
    """Trava um lote. Estrutural sem texto válido não chama o Gemini."""
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE falha_ia_definitiva
                SET estado = 'pendente'
                WHERE estado = 'em_reprocesso'
                  AND reprocessado_em < CURRENT_TIMESTAMP - INTERVAL '30 minutes'
                """
            )
            cur.execute(
                """
                WITH lote AS (
                    SELECT id
                    FROM falha_ia_definitiva
                    WHERE estado = 'pendente'
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %(limite)s
                )
                UPDATE falha_ia_definitiva AS falha
                SET estado = 'em_reprocesso',
                    reprocessado_em = CURRENT_TIMESTAMP
                FROM lote
                WHERE falha.id = lote.id
                RETURNING falha.id, falha.oferta_id, falha.classe, falha.tentativas
                """,
                {"limite": LIMITE_LOTE},
            )
            lote = _linhas(cur)
        conn.commit()
    enfileiradas = 0
    esgotadas = 0
    for linha in lote:
        if linha.get("classe") == "estrutural" and not _estrutural_revalidavel(int(linha["oferta_id"])):
            _marcar(int(linha["id"]), "esgotada")
            esgotadas += 1
            continue
        if linha.get("classe") == "transporte" and int(linha.get("tentativas") or 0) >= 3:
            _marcar(int(linha["id"]), "esgotada")
            esgotadas += 1
            continue
        if _enviar_reprocesso(int(linha["id"])):
            enfileiradas += 1
        else:
            _marcar(int(linha["id"]), "pendente")
    return {"lidas": len(lote), "enfileiradas": enfileiradas, "esgotadas": esgotadas}


@app.task(name="tasks.lifecycle.alertar_dlq_ia")
def alertar_dlq_ia() -> dict:
    """Conta pendências na tabela. LLEN só se o cliente Redis responder."""
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM falha_ia_definitiva WHERE estado = 'pendente'")
            linha = cur.fetchone() or {}
        conn.commit()
    total = int(linha.get("total") or 0)
    fila = _llen_dead_letter()
    if fila is None:
        logger.info("DLQ de IA: %s pendentes na tabela.", total)
    else:
        logger.info("DLQ de IA: %s pendentes na tabela; LLEN ai_dead_letter=%s.", total, fila)
    if total > LIMIAR_ALERTA:
        _postar_alerta(total)
    return {"pendentes": total, "fila": fila}


@app.task(name="tasks.lifecycle.coletar_paridade_cambio")
def coletar_paridade_cambio() -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    vigencia = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    linhas = coletar_paridades(vigencia)
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            gravar_paridades(cur, vigencia, linhas)
            atualizadas = _preencher_brl_pendente(cur)
        conn.commit()
    return {"vigencia": vigencia.isoformat(), "linhas": len(linhas), "ofertas": atualizadas}


@app.task(name="tasks.lifecycle.enviar_integracao_evento")
def enviar_integracao_evento() -> dict:
    segredo = ler_segredo("ERP_WEBHOOK_SEGREDO").strip()
    url = ler_segredo("ERP_WEBHOOK_URL").strip()
    if not segredo or not url:
        logger.info("Integração sem ERP_WEBHOOK_SEGREDO ou ERP_WEBHOOK_URL; triagem permanece.")
        return {"enviados": 0, "motivo": "sem_segredo"}
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, corpo, tentativas
                FROM integracao_evento
                WHERE estado = 'pendente'
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT %(limite)s
                """,
                {"limite": LIMITE_LOTE},
            )
            lote = _linhas(cur)
        conn.commit()
    enviados = 0
    for linha in lote:
        if _postar_evento(url, segredo, linha):
            enviados += 1
    return {"enviados": enviados, "lidos": len(lote)}


@app.task(name="tasks.lifecycle.manter_particoes_oferta_vetor")
def manter_particoes_oferta_vetor() -> dict:
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT manter_particoes_oferta_vetor()")
        conn.commit()
    return {"status": "ok"}


def _estrutural_revalidavel(oferta_id: int) -> bool:
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT resposta_ia_bruta FROM ofertas WHERE id = %(id)s",
                {"id": oferta_id},
            )
            linha = cur.fetchone()
        conn.commit()
    texto = linha.get("resposta_ia_bruta") if isinstance(linha, dict) else None
    return revalidar_texto(texto) is not None


def _marcar(falha_id: int, estado: str) -> None:
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE falha_ia_definitiva
                SET estado = %(estado)s
                WHERE id = %(id)s
                """,
                {"estado": estado, "id": falha_id},
            )
        conn.commit()


def _enviar_reprocesso(falha_id: int) -> bool:
    try:
        app.send_task(
            "src.worker_ia.reprocessar_falha_da_tabela",
            kwargs={"falha_id": falha_id},
            queue="ai_multimodal_queue",
        )
    except Exception:
        logger.exception("Não foi possível enfileirar a falha %s.", falha_id)
        return False
    return True


def _llen_dead_letter() -> int | None:
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis
    except ImportError:
        return None
    try:
        cliente = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        return int(cliente.llen("ai_dead_letter"))
    except Exception:
        logger.info("LLEN da ai_dead_letter indisponível.")
        return None


def _postar_alerta(total: int) -> None:
    if not _pode_alertar():
        return
    url = ler_segredo("ALERTA_WEBHOOK_URL").strip()
    texto = f"DLQ de IA: {total} pendentes"
    if not url:
        logger.error(texto)
        return
    try:
        requests.post(url, json={"text": texto}, timeout=(3, 5), allow_redirects=False)
    except requests.RequestException:
        logger.exception("Falha ao postar o alerta de DLQ.")


def _pode_alertar() -> bool:
    global _carimbo_alerta
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        try:
            import redis

            cliente = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
            return bool(cliente.set("alerta:dlq", "1", nx=True, ex=1800))
        except Exception:
            logger.info("Alerta de DLQ sem Redis; usando carimbo local.")
    agora = time.time()
    if agora - _carimbo_alerta < 1800:
        return False
    _carimbo_alerta = agora
    return True


def _preencher_brl_pendente(cur) -> int:
    cur.execute(
        """
        SELECT id, preco_oferta_estimado, preco_moeda, cogs_estimado, cogs_moeda
        FROM ofertas
        WHERE venda_brl IS NULL
          AND preco_oferta_estimado IS NOT NULL
          AND cogs_estimado IS NOT NULL
          AND status_qualificacao = 'concluido'
        ORDER BY id
        LIMIT 50
        """
    )
    total = 0
    for linha in _linhas(cur):
        if completar_brl(
            cur,
            int(linha["id"]),
            linha.get("preco_oferta_estimado"),
            linha.get("preco_moeda"),
            linha.get("cogs_estimado"),
            linha.get("cogs_moeda"),
        ):
            total += 1
    return total


def _postar_evento(url: str, segredo: str, linha: dict) -> bool:
    corpo = linha.get("corpo") if isinstance(linha.get("corpo"), dict) else {"oferta_id": None}
    payload = json.dumps(corpo, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assinatura = hmac.new(segredo.encode(), payload, hashlib.sha256).hexdigest()
    try:
        resposta = requests.post(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "X-Assinatura": f"sha256={assinatura}"},
            timeout=(3, 5),
            allow_redirects=False,
        )
    except requests.RequestException:
        _falha_transporte(int(linha["id"]))
        return False
    if resposta.status_code >= 400 or resposta.is_redirect:
        _falha_transporte(int(linha["id"]))
        return False
    _marcar_enviado(int(linha["id"]))
    return True


def _falha_transporte(evento_id: int) -> None:
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE integracao_evento
                SET tentativas = tentativas + 1,
                    estado = CASE WHEN tentativas + 1 >= 3 THEN 'esgotada' ELSE 'pendente' END
                WHERE id = %(id)s
                """,
                {"id": evento_id},
            )
        conn.commit()


def _marcar_enviado(evento_id: int) -> None:
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE integracao_evento
                SET estado = 'enviado',
                    enviado_em = CURRENT_TIMESTAMP
                WHERE id = %(id)s
                """,
                {"id": evento_id},
            )
        conn.commit()
