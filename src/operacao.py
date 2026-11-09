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
    sessao = _alertar_sessao()
    return {"pendentes": total, "fila": fila, "sessao": sessao}


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
            from src.viabilidade import preencher_viabilidade_pendente

            viabilidade = preencher_viabilidade_pendente(cur)
        conn.commit()
    return {"vigencia": vigencia.isoformat(), "linhas": len(linhas), "ofertas": atualizadas, "viabilidade": viabilidade}


def _loja_configurada() -> bool:
    return os.environ.get("LOJA_DESTINO", "").strip() == "shopify" and bool(ler_segredo("SHOPIFY_ADMIN_TOKEN").strip())


def _reivindicar_eventos(cur) -> list[dict]:
    cur.execute(
        """
        UPDATE integracao_evento
        SET estado = 'em_envio',
            atualizado_em = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT id
            FROM integracao_evento
            WHERE (estado = 'pendente' AND disponivel_em <= CURRENT_TIMESTAMP)
               OR (estado = 'em_envio' AND atualizado_em <= CURRENT_TIMESTAMP - INTERVAL '15 minutes')
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT %(limite)s
        )
        RETURNING id, corpo, tentativas
        """,
        {"limite": LIMITE_LOTE},
    )
    return _linhas(cur)


def _oferta_do_evento(cur, corpo: dict) -> dict:
    oferta_id = corpo.get("oferta_id")
    if not isinstance(oferta_id, int) or isinstance(oferta_id, bool):
        return corpo
    cur.execute(
        """
        SELECT id, nicho, copy_texto, venda_brl, cogs_brl, margem_liquida_brl,
               url_destino, fornecedor_url, provas_sociais
        FROM ofertas
        WHERE id = %(id)s
        """,
        {"id": oferta_id},
    )
    linha = cur.fetchone()
    if not isinstance(linha, dict):
        return corpo
    enriquecido = dict(corpo)
    enriquecido.update({chave: linha.get(chave) for chave in linha if chave != "id"})
    enriquecido["oferta_id"] = oferta_id
    return enriquecido


@app.task(name="tasks.lifecycle.enviar_integracao_evento")
def enviar_integracao_evento() -> dict:
    segredo = ler_segredo("ERP_WEBHOOK_SEGREDO").strip()
    url = ler_segredo("ERP_WEBHOOK_URL").strip()
    loja = _loja_configurada()
    if (not segredo or not url) and not loja:
        logger.info("Integração sem ERP_WEBHOOK_SEGREDO ou ERP_WEBHOOK_URL; triagem permanece.")
        return {"enviados": 0, "motivo": "sem_segredo"}
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            lote = _reivindicar_eventos(cur)
        conn.commit()
    enviados = 0
    for linha in lote:
        if _postar_evento(url, segredo, linha, loja):
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


def _alertar_sessao() -> str | None:
    from src.egress_pausa import pausa_ativa
    from src.sessao import estado_atual

    estado = estado_atual()
    if estado in {None, "ok"} and not pausa_ativa():
        return estado
    if estado not in {None, "ok"}:
        _postar_alerta_texto(f"Sessão da Biblioteca: {estado}. A publicação está pausada.")
    if pausa_ativa():
        _postar_alerta_texto("Egress pausado: bloqueios acima de 30% em 5 minutos.")
    return estado


def _postar_alerta_texto(texto: str) -> None:
    url = ler_segredo("ALERTA_WEBHOOK_URL").strip()
    if not url:
        logger.error(texto)
        return
    try:
        requests.post(url, json={"text": texto}, timeout=(3, 5), allow_redirects=False)
    except requests.RequestException:
        logger.exception("Falha ao postar o alerta.")


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


def _lembrar_corpo(evento_id: int, corpo: dict) -> None:
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE integracao_evento
                SET corpo = %(corpo)s::jsonb,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = %(id)s
                """,
                {"id": evento_id, "corpo": json.dumps(corpo, default=str)},
            )
        conn.commit()


def _enriquecer(evento_id: int, corpo: dict, loja: bool) -> dict:
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            enriquecido = _oferta_do_evento(cur, corpo)
        conn.commit()
    if not loja or enriquecido.get("loja_produto_id"):
        return enriquecido
    from src.loja import rascunho_shopify

    identificador = rascunho_shopify(enriquecido)
    if not identificador:
        raise RuntimeError("rascunho da loja não foi criado")
    enriquecido["loja_produto_id"] = identificador
    _lembrar_corpo(evento_id, enriquecido)
    return enriquecido


def _postar_evento(url: str, segredo: str, linha: dict, loja: bool = False) -> bool:
    corpo = linha.get("corpo") if isinstance(linha.get("corpo"), dict) else {"oferta_id": None}
    try:
        corpo = _enriquecer(int(linha["id"]), corpo, loja)
    except Exception:
        logger.exception("Falha ao montar o evento %s.", linha.get("id"))
        _falha_transporte(int(linha["id"]))
        return False
    if not url or not segredo:
        _marcar_enviado(int(linha["id"]), corpo)
        return True
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
    _marcar_enviado(int(linha["id"]), corpo)
    return True


def _falha_transporte(evento_id: int) -> None:
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE integracao_evento
                SET tentativas = tentativas + 1,
                    atualizado_em = CURRENT_TIMESTAMP,
                    disponivel_em = CURRENT_TIMESTAMP + (INTERVAL '30 seconds' * power(2, tentativas)),
                    estado = CASE WHEN tentativas + 1 >= 3 THEN 'esgotada' ELSE 'pendente' END
                WHERE id = %(id)s
                """,
                {"id": evento_id},
            )
        conn.commit()


def _marcar_enviado(evento_id: int, corpo: dict | None = None) -> None:
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE integracao_evento
                SET estado = 'enviado',
                    enviado_em = CURRENT_TIMESTAMP,
                    atualizado_em = CURRENT_TIMESTAMP,
                    corpo = COALESCE(%(corpo)s::jsonb, corpo)
                WHERE id = %(id)s
                """,
                {"id": evento_id, "corpo": json.dumps(corpo, default=str) if corpo else None},
            )
        conn.commit()
