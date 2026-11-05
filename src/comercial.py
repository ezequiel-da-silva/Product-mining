"""Fecha a qualificação comercial da página desta oferta, sem copiar a loja irmã."""

import logging

from src.landing import buscar_preco

logger = logging.getLogger(__name__)

SQL_FECHAR_COMERCIAL = """
UPDATE ofertas
SET preco_oferta_estimado = %(preco)s,
    preco_moeda = %(moeda)s,
    dados_comerciais_ausentes = %(ausentes)s,
    preco_pagina_consultado_em = CURRENT_TIMESTAMP,
    status_qualificacao = 'concluido',
    processamento_iniciado_em = NULL,
    processamento_token = NULL
WHERE id = %(id)s
  AND status_qualificacao = 'pendente_lp'
"""

SQL_PRECO_LIDO = """
UPDATE ofertas
SET preco_oferta_estimado = %(preco)s,
    preco_moeda = %(moeda)s,
    dados_comerciais_ausentes = FALSE,
    preco_pagina_consultado_em = CURRENT_TIMESTAMP
WHERE id = %(id)s
  AND status_qualificacao = 'concluido'
"""

SQL_CONSULTA_SEM_PRECO = """
UPDATE ofertas
SET preco_pagina_consultado_em = CURRENT_TIMESTAMP
WHERE id = %(id)s
  AND status_qualificacao = 'concluido'
"""

SQL_PRECOS_AUSENTES = """
SELECT id, url_destino
FROM ofertas
WHERE status_qualificacao = 'concluido'
  AND preco_moeda IS NULL
  AND preco_pagina_consultado_em IS NULL
ORDER BY id
LIMIT %(limite)s
"""


def _linhas(cur) -> list[dict]:
    linhas = cur.fetchall()
    if not isinstance(linhas, list):
        return []
    return [linha for linha in linhas if isinstance(linha, dict)]


def qualificar_pagina(conn, oferta_id: int, url: str | None) -> bool:
    lido = buscar_preco(url.strip()) if isinstance(url, str) and url.strip() else None
    preco = lido.valor if lido else None
    moeda = lido.moeda if lido else None
    ausentes = lido is None
    with conn.cursor() as cur:
        cur.execute(
            SQL_FECHAR_COMERCIAL,
            {"preco": preco, "moeda": moeda, "ausentes": ausentes, "id": oferta_id},
        )
        fechou = cur.rowcount == 1
    conn.commit()
    if fechou and ausentes:
        logger.info("Oferta %s concluída com dados comerciais ausentes.", oferta_id)
    elif fechou:
        logger.info("Oferta %s concluída com preço lido na página de destino.", oferta_id)
    return fechou


def aplicar_preco_lido(conn, oferta_id: int, url: str | None) -> bool:
    """Substitui o preço da fala quando a página traz BRL ou USD. Uma consulta por oferta."""
    lido = buscar_preco(url.strip()) if isinstance(url, str) and url.strip() else None
    with conn.cursor() as cur:
        if lido is None:
            cur.execute(SQL_CONSULTA_SEM_PRECO, {"id": oferta_id})
        else:
            cur.execute(
                SQL_PRECO_LIDO,
                {"preco": lido.valor, "moeda": lido.moeda, "id": oferta_id},
            )
        fechou = cur.rowcount == 1
    conn.commit()
    if fechou and lido is not None:
        logger.info("Oferta %s recebeu o preço lido na página de destino.", oferta_id)
    return fechou


def listar_precos_ausentes(cur, limite: int) -> list[dict]:
    cur.execute(SQL_PRECOS_AUSENTES, {"limite": limite})
    return _linhas(cur)
