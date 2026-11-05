"""Consulta o fornecedor da oferta canônica e grava COGS e prova social."""

import json
import logging
from urllib.parse import urlsplit

from src.cambio import completar_brl
from src.landing import buscar_html, host_publico
from src.provas_r2 import arquivar_fotos
from src.provas_sociais import extrair_provas, url_http_publica
from src.sourcing import (
    Candidato,
    escolher_cogs,
    extrair_fornecedores,
    extrair_og_image,
    margem_com_cogs,
    origem_da_url,
    url_busca_imagem,
    urls_de_produto,
)

logger = logging.getLogger(__name__)

SQL_SEM_FORNECEDOR = """
SELECT id, url_destino, url_imagem_criativo, preco_oferta_estimado, preco_moeda
FROM ofertas
WHERE status_qualificacao = 'concluido'
  AND oferta_canonica_id IS NULL
  AND fornecedor_consultado_em IS NULL
  AND preco_pagina_consultado_em IS NOT NULL
ORDER BY id
LIMIT %(limite)s
"""

SQL_GRAVAR_FORNECEDOR = """
UPDATE ofertas
SET cogs_estimado = %(cogs)s,
    cogs_moeda = %(moeda)s,
    fornecedor_url = %(url)s,
    fornecedor_origem = %(origem)s,
    provas_sociais = %(provas)s::jsonb,
    fornecedor_consultado_em = CURRENT_TIMESTAMP,
    margem_estimada_pct = CASE
        WHEN %(margem)s::smallint IS NULL THEN margem_estimada_pct
        ELSE %(margem)s::smallint
    END,
    url_imagem_criativo = COALESCE(url_imagem_criativo, %(imagem)s)
WHERE id = %(id)s
  AND status_qualificacao = 'concluido'
  AND fornecedor_consultado_em IS NULL
"""


def _linhas(cur) -> list[dict]:
    linhas = cur.fetchall()
    if not isinstance(linhas, list):
        return []
    return [linha for linha in linhas if isinstance(linha, dict)]


def listar_sem_fornecedor(cur, limite: int) -> list[dict]:
    cur.execute(SQL_SEM_FORNECEDOR, {"limite": limite})
    return _linhas(cur)


def _imagem_utilizavel(url: object) -> str | None:
    if not isinstance(url, str) or not url_http_publica(url):
        return None
    host = urlsplit(url.strip()).hostname
    if not host or not host_publico(host):
        return None
    return url.strip()


def resolver_imagem(direta: object, html_destino: str | None) -> str | None:
    candidatas = [direta]
    if html_destino:
        candidatas.append(extrair_og_image(html_destino))
    for candidata in candidatas:
        imagem = _imagem_utilizavel(candidata)
        if imagem:
            return imagem
    return None


def _paginas_busca(imagem: str) -> list[str]:
    paginas: list[str] = []
    for origem in ("1688", "aliexpress"):
        endereco = url_busca_imagem(origem, imagem)
        if not endereco:
            continue
        html = buscar_html(endereco)
        if html:
            paginas.append(html)
    return paginas


def _urls_novas(paginas: list[str], conhecidas: set[str]) -> list[str]:
    achados: list[str] = []
    for html in paginas:
        for url in urls_de_produto(html):
            if url not in conhecidas and url not in achados:
                achados.append(url)
    return achados


def _com_preco_na_pagina(url: str, html: str) -> Candidato | None:
    origem = origem_da_url(url)
    if origem is None:
        return None
    for candidato in extrair_fornecedores(html):
        if candidato.origem == origem:
            return Candidato(origem, url, candidato.preco, candidato.moeda, candidato.similaridade)
    return None


def _primeiro_com_preco(urls: list[str]) -> tuple[Candidato | None, list[dict]]:
    ordenadas = [url for url in urls if origem_da_url(url) == "1688"][:1]
    ordenadas.extend(url for url in urls if origem_da_url(url) == "aliexpress")
    ordenadas = ordenadas[:2]
    for url in ordenadas:
        html = buscar_html(url)
        if not html:
            continue
        candidato = _com_preco_na_pagina(url, html)
        if candidato is not None:
            return candidato, extrair_provas(html)
    return None, []


def consultar_imagem(imagem: str) -> tuple[Candidato | None, list[dict]]:
    paginas = _paginas_busca(imagem)
    candidatos: list[Candidato] = []
    for html in paginas:
        candidatos.extend(extrair_fornecedores(html))
    escolhido = escolher_cogs(candidatos)
    if escolhido is None:
        return _primeiro_com_preco(_urls_novas(paginas, {candidato.url for candidato in candidatos}))
    html_produto = buscar_html(escolhido.url)
    provas = extrair_provas(html_produto) if html_produto else []
    return escolhido, provas


def aplicar_fornecedor(conn, linha: dict) -> bool:
    """Uma consulta por oferta. Sem imagem pública, registra a tentativa e segue."""
    destino = linha.get("url_destino")
    html_destino = buscar_html(destino.strip()) if isinstance(destino, str) and destino.strip() else None
    imagem = resolver_imagem(linha.get("url_imagem_criativo"), html_destino)
    escolhido, provas = consultar_imagem(imagem) if imagem else (None, [])
    if provas:
        provas = arquivar_fotos(linha.get("id"), provas)
    margem = None
    if escolhido is not None:
        margem = margem_com_cogs(
            linha.get("preco_oferta_estimado"),
            linha.get("preco_moeda"),
            escolhido.preco,
            escolhido.moeda,
        )
    with conn.cursor() as cur:
        cur.execute(
            SQL_GRAVAR_FORNECEDOR,
            {
                "cogs": escolhido.preco if escolhido else None,
                "moeda": escolhido.moeda if escolhido else None,
                "url": escolhido.url if escolhido else None,
                "origem": escolhido.origem if escolhido else None,
                "provas": json.dumps(provas) if provas else None,
                "margem": margem,
                "imagem": imagem,
                "id": linha.get("id"),
            },
        )
        gravou = cur.rowcount == 1
        if gravou and escolhido is not None and isinstance(linha.get("id"), int):
            completar_brl(
                cur,
                linha["id"],
                linha.get("preco_oferta_estimado"),
                linha.get("preco_moeda"),
                escolhido.preco,
                escolhido.moeda,
            )
    conn.commit()
    if gravou and escolhido is not None:
        logger.info("Oferta %s recebeu COGS de %s.", linha.get("id"), escolhido.origem)
    elif gravou:
        logger.info("Oferta %s ficou sem fornecedor identificado.", linha.get("id"))
    return gravou
