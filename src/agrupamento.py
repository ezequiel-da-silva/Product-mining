"""Agrupa criativos do mesmo produto na oferta mais antiga, sem apagar as URLs."""

import logging

from src.phash_visual import LIMIAR_HAMMING

logger = logging.getLogger(__name__)

LIMIAR_COSENO = 0.08
_PRIORIDADE_TRIAGEM = {"testando": 3, "estrelada": 2, "descartada": 1}
_SALTOS = 2

SQL_ORIGEM = """
SELECT id, status_qualificacao, url_video_r2, phash_criativo,
       phash_perceptual::text AS phash_perceptual, nicho, oferta_canonica_id
FROM ofertas
WHERE id = %(id)s
"""

SQL_POR_CRIATIVO = """
SELECT id, oferta_canonica_id
FROM ofertas
WHERE id <> %(id)s
  AND status_qualificacao = 'concluido'
  AND (
    (%(url)s::text IS NOT NULL AND url_video_r2 = %(url)s::text)
    OR (%(phash)s::text IS NOT NULL AND phash_criativo = %(phash)s::text)
    OR (
      %(visual)s::bit(64) IS NOT NULL
      AND phash_perceptual IS NOT NULL
      AND bit_count(phash_perceptual # %(visual)s::bit(64)) <= %(limiar)s
    )
  )
"""

SQL_POR_EMBEDDING = """
SELECT o.id, o.oferta_canonica_id
FROM ofertas AS o
JOIN oferta_vetor AS v ON v.oferta_id = o.id
JOIN oferta_vetor AS origem ON origem.oferta_id = %(id)s
WHERE o.id <> %(id)s
  AND o.status_qualificacao = 'concluido'
  AND o.nicho IS NOT DISTINCT FROM %(nicho)s
  AND (v.embedding <=> origem.embedding) <= %(limiar_coseno)s
"""

SQL_MEMBROS = """
SELECT id, oferta_canonica_id
FROM ofertas
WHERE id = ANY(%(ids)s)
   OR oferta_canonica_id = ANY(%(ids)s)
"""

SQL_APONTAR = """
UPDATE ofertas
SET oferta_canonica_id = CASE WHEN id = %(canon)s THEN NULL ELSE %(canon)s END
WHERE id = ANY(%(ids)s)
  AND (
    (id = %(canon)s AND oferta_canonica_id IS NOT NULL)
    OR (id <> %(canon)s AND oferta_canonica_id IS DISTINCT FROM %(canon)s)
  )
"""

SQL_TRIAGEM_DO_GRUPO = """
SELECT oferta_id, estado
FROM triagem_oferta
WHERE oferta_id = ANY(%(ids)s)
"""

SQL_DEFINIR_TRIAGEM = """
INSERT INTO triagem_oferta (oferta_id, estado)
VALUES (%(id)s, %(estado)s)
ON CONFLICT (oferta_id) DO UPDATE
SET estado = EXCLUDED.estado,
    atualizado_em = CURRENT_TIMESTAMP
"""

SQL_LIMPAR_TRIAGEM = """
DELETE FROM triagem_oferta
WHERE oferta_id = ANY(%(ids)s)
  AND oferta_id <> %(canon)s
"""

SQL_IDS_PARA_AGRUPAR = """
SELECT o.id
FROM ofertas AS o
WHERE o.status_qualificacao = 'concluido'
  AND o.oferta_canonica_id IS NULL
  AND EXISTS (
    SELECT 1
    FROM ofertas AS irma
    WHERE irma.id < o.id
      AND irma.status_qualificacao = 'concluido'
      AND (
        (o.url_video_r2 IS NOT NULL AND irma.url_video_r2 = o.url_video_r2)
        OR (o.phash_criativo IS NOT NULL AND irma.phash_criativo = o.phash_criativo)
        OR (
          o.phash_perceptual IS NOT NULL
          AND irma.phash_perceptual IS NOT NULL
          AND bit_count(o.phash_perceptual # irma.phash_perceptual) <= %(limiar)s
        )
        OR EXISTS (
          SELECT 1
          FROM oferta_vetor AS origem
          JOIN oferta_vetor AS v ON v.oferta_id = irma.id
          WHERE origem.oferta_id = o.id
            AND irma.nicho IS NOT DISTINCT FROM o.nicho
            AND (v.embedding <=> origem.embedding) <= %(limiar_coseno)s
        )
      )
  )
ORDER BY o.id
LIMIT %(limite)s
"""


def estado_do_grupo(estados: list[str]) -> str | None:
    """Testando prevalece. Entre o resto, salvar prevalece sobre descartar."""
    validos = [estado for estado in estados if estado in _PRIORIDADE_TRIAGEM]
    if not validos:
        return None
    return max(validos, key=_PRIORIDADE_TRIAGEM.__getitem__)


def _linha(cur) -> dict | None:
    linha = cur.fetchone()
    return linha if isinstance(linha, dict) else None


def _linhas(cur) -> list[dict]:
    linhas = cur.fetchall()
    if not isinstance(linhas, list):
        return []
    return [linha for linha in linhas if isinstance(linha, dict)]


def _visual(valor: object) -> str | None:
    if not isinstance(valor, str):
        return None
    texto = valor.strip()
    if len(texto) == 64 and set(texto) <= {"0", "1"}:
        return texto
    return None


def _id_inteiro(valor: object) -> int | None:
    if isinstance(valor, bool) or not isinstance(valor, int):
        return None
    return valor


def _acrescentar_id(grupo: set[int], valor: object) -> None:
    identificador = _id_inteiro(valor)
    if identificador is not None:
        grupo.add(identificador)


def _parametros(origem: dict) -> dict:
    return {
        "id": origem["id"],
        "url": origem.get("url_video_r2"),
        "phash": origem.get("phash_criativo"),
        "visual": _visual(origem.get("phash_perceptual")),
        "nicho": origem.get("nicho"),
        "limiar": LIMIAR_HAMMING,
        "limiar_coseno": LIMIAR_COSENO,
    }


def _vizinhos(cur, origem: dict) -> list[dict]:
    parametros = _parametros(origem)
    cur.execute(SQL_POR_CRIATIVO, parametros)
    criativo = _linhas(cur)
    cur.execute(SQL_POR_EMBEDDING, parametros)
    return criativo + _linhas(cur)


def _proximo_nivel(cur, nivel: list[dict], grupo: set[int], vistos: set[int]) -> list[dict]:
    proximo: list[dict] = []
    for atual in nivel:
        for vizinho in _vizinhos(cur, atual):
            identificador = _id_inteiro(vizinho.get("id"))
            if identificador is None:
                continue
            grupo.add(identificador)
            _acrescentar_id(grupo, vizinho.get("oferta_canonica_id"))
            if identificador in vistos:
                continue
            vistos.add(identificador)
            cur.execute(SQL_ORIGEM, {"id": identificador})
            linha = _linha(cur)
            if linha is not None and linha.get("status_qualificacao") == "concluido":
                proximo.append(linha)
    return proximo


def _incluir_membros(cur, grupo: set[int]) -> None:
    cur.execute(SQL_MEMBROS, {"ids": list(grupo)})
    for linha in _linhas(cur):
        _acrescentar_id(grupo, linha.get("id"))
        _acrescentar_id(grupo, linha.get("oferta_canonica_id"))


def _ids_do_grupo(cur, origem: dict) -> set[int]:
    grupo = {int(origem["id"])}
    vistos = {int(origem["id"])}
    nivel = [origem]
    for _ in range(_SALTOS):
        nivel = _proximo_nivel(cur, nivel, grupo, vistos)
        if not nivel:
            break
    if len(grupo) >= 2:
        _incluir_membros(cur, grupo)
    return grupo


def _subir_triagem(cur, grupo: set[int], canon: int) -> None:
    cur.execute(SQL_TRIAGEM_DO_GRUPO, {"ids": list(grupo)})
    estados = [linha.get("estado") for linha in _linhas(cur) if isinstance(linha.get("estado"), str)]
    estado = estado_do_grupo(estados)
    if estado is not None:
        cur.execute(SQL_DEFINIR_TRIAGEM, {"id": canon, "estado": estado})
    cur.execute(SQL_LIMPAR_TRIAGEM, {"ids": list(grupo), "canon": canon})


def vincular_grupo(conn, oferta_id: int) -> int | None:
    """Aponta as irmãs para a oferta mais antiga. A canônica fica com o campo nulo."""
    canon = None
    with conn.cursor() as cur:
        cur.execute(SQL_ORIGEM, {"id": oferta_id})
        origem = _linha(cur)
        if origem is not None and origem.get("status_qualificacao") == "concluido":
            grupo = _ids_do_grupo(cur, origem)
            if len(grupo) >= 2:
                canon = min(grupo)
                cur.execute(SQL_APONTAR, {"canon": canon, "ids": list(grupo)})
                _subir_triagem(cur, grupo, canon)
    conn.commit()
    if canon is not None:
        logger.info("Oferta %s ficou no grupo da canônica %s.", oferta_id, canon)
    return canon


def ids_para_agrupar(cur, limite: int) -> list[int]:
    cur.execute(
        SQL_IDS_PARA_AGRUPAR,
        {"limiar": LIMIAR_HAMMING, "limiar_coseno": LIMIAR_COSENO, "limite": limite},
    )
    ids: list[int] = []
    for linha in _linhas(cur):
        identificador = _id_inteiro(linha.get("id"))
        if identificador is not None:
            ids.append(identificador)
    return ids
