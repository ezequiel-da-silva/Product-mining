"""Fecha a qualificação comercial da página desta oferta, sem copiar a loja irmã."""

import logging

from src.landing import LeituraPagina, avaliar_destino

logger = logging.getLogger(__name__)

SQL_FECHAR_COMERCIAL = """
UPDATE ofertas
SET preco_oferta_estimado = %(preco)s,
    preco_moeda = %(moeda)s,
    dados_comerciais_ausentes = %(ausentes)s,
    preco_origem = %(origem)s,
    preco_pagina_consultado_em = CURRENT_TIMESTAMP,
    status_qualificacao = 'concluido',
    processamento_iniciado_em = NULL,
    processamento_token = NULL
WHERE id = %(id)s
  AND status_qualificacao = 'pendente_lp'
"""

SQL_FECHAR_BLOQUEADO = """
UPDATE ofertas
SET preco_origem = 'bloqueado',
    dados_comerciais_ausentes = TRUE,
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
    preco_origem = %(origem)s,
    preco_pagina_consultado_em = CURRENT_TIMESTAMP
WHERE id = %(id)s
  AND status_qualificacao = 'concluido'
"""

SQL_CONSULTA_SEM_PRECO = """
UPDATE ofertas
SET preco_origem = %(origem)s,
    preco_pagina_consultado_em = CURRENT_TIMESTAMP
WHERE id = %(id)s
  AND status_qualificacao = 'concluido'
"""

SQL_CONSULTA_BLOQUEADA = """
UPDATE ofertas
SET preco_origem = 'bloqueado'
WHERE id = %(id)s
  AND status_qualificacao = 'concluido'
"""

SQL_PRECOS_AUSENTES = """
SELECT id, url_destino
FROM ofertas
WHERE status_qualificacao = 'concluido'
  AND preco_moeda IS NULL
  AND (
    preco_pagina_consultado_em IS NULL
    OR preco_origem = 'bloqueado'
  )
ORDER BY id
LIMIT %(limite)s
"""


def _linhas(cur) -> list[dict]:
    linhas = cur.fetchall()
    if not isinstance(linhas, list):
        return []
    return [linha for linha in linhas if isinstance(linha, dict)]


def _leitura(url: str | None) -> LeituraPagina:
    if isinstance(url, str) and url.strip():
        return avaliar_destino(url.strip())
    return LeituraPagina("ausente")


def _gravar(cur, oferta_id: int, leitura: LeituraPagina, fechar: bool) -> None:
    if leitura.origem == "bloqueado":
        cur.execute(SQL_FECHAR_BLOQUEADO if fechar else SQL_CONSULTA_BLOQUEADA, {"id": oferta_id})
        return
    if leitura.preco is None:
        params = {"id": oferta_id, "origem": leitura.origem}
        if fechar:
            params["preco"] = None
            params["moeda"] = None
            params["ausentes"] = True
            cur.execute(SQL_FECHAR_COMERCIAL, params)
            return
        cur.execute(SQL_CONSULTA_SEM_PRECO, params)
        return
    params = {
        "id": oferta_id,
        "origem": leitura.origem,
        "preco": leitura.preco.valor,
        "moeda": leitura.preco.moeda,
    }
    if fechar:
        params["ausentes"] = False
        cur.execute(SQL_FECHAR_COMERCIAL, params)
        return
    cur.execute(SQL_PRECO_LIDO, params)


def qualificar_pagina(conn, oferta_id: int, url: str | None) -> bool:
    leitura = _leitura(url)
    with conn.cursor() as cur:
        _gravar(cur, oferta_id, leitura, True)
        fechou = cur.rowcount == 1
    conn.commit()
    if fechou and leitura.preco is None:
        logger.info("Oferta %s concluída com dados comerciais ausentes.", oferta_id)
    elif fechou:
        logger.info("Oferta %s concluída com preço lido na página de destino.", oferta_id)
    return fechou


def aplicar_preco_lido(conn, oferta_id: int, url: str | None) -> bool:
    """Substitui o preço da fala quando a página traz BRL ou USD. Uma consulta por oferta."""
    leitura = _leitura(url)
    with conn.cursor() as cur:
        _gravar(cur, oferta_id, leitura, False)
        fechou = cur.rowcount == 1
    conn.commit()
    if fechou and leitura.preco is not None:
        logger.info("Oferta %s recebeu o preço lido na página de destino.", oferta_id)
    return fechou


def listar_precos_ausentes(cur, limite: int) -> list[dict]:
    cur.execute(SQL_PRECOS_AUSENTES, {"limite": limite})
    return _linhas(cur)
