"""Fecha a qualificação comercial da página desta oferta, sem copiar a loja irmã."""

import logging
from decimal import Decimal

from src.landing import buscar_preco

logger = logging.getLogger(__name__)

SQL_FECHAR_COMERCIAL = """
UPDATE ofertas
SET preco_oferta_estimado = %(preco)s,
    dados_comerciais_ausentes = %(ausentes)s,
    status_qualificacao = 'concluido',
    processamento_iniciado_em = NULL,
    processamento_token = NULL
WHERE id = %(id)s
  AND status_qualificacao = 'pendente_lp'
"""


def qualificar_pagina(conn, oferta_id: int, url: str | None) -> bool:
    preco: Decimal | None = None
    if isinstance(url, str) and url.strip():
        preco = buscar_preco(url.strip())
    ausentes = preco is None
    with conn.cursor() as cur:
        cur.execute(
            SQL_FECHAR_COMERCIAL,
            {"preco": preco, "ausentes": ausentes, "id": oferta_id},
        )
        fechou = cur.rowcount == 1
    conn.commit()
    if fechou and ausentes:
        logger.info("Oferta %s concluída com dados comerciais ausentes.", oferta_id)
    elif fechou:
        logger.info("Oferta %s concluída com preço lido na página de destino.", oferta_id)
    return fechou
