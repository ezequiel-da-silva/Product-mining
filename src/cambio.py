"""Paridade USD/CNY para BRL. A taxa do dia não reescreve o preço nem o COGS gravados."""

import json
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlsplit

import requests
from src.landing import _abrir_pagina, _ler_corpo, resolver_publico
from src.sourcing import margem_com_cogs

logger = logging.getLogger(__name__)

HOSTS_CAMBIO = frozenset({"olinda.bcb.gov.br", "economia.awesomeapi.com.br"})
URL_AWESOME = "https://economia.awesomeapi.com.br/json/last/USD-BRL,CNY-BRL"


def margem_em_brl(venda: object, cogs: object) -> int | None:
    """Mesma quantização de margem_com_cogs, já com os dois lados em BRL."""
    try:
        custo = Decimal(str(cogs))
    except (InvalidOperation, ValueError):
        return None
    return margem_com_cogs(venda, "BRL", custo, "BRL")


def url_bacen(moeda: str, vigencia: date) -> str:
    dia = vigencia.strftime("%m-%d-%Y")
    parametro = quote(f"'{moeda}'", safe="")
    data = quote(f"'{dia}'", safe="")
    return (
        "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
        f"CotacaoMoedaDia(moeda=@moeda,dataCotacao=@dataCotacao)?@moeda={parametro}"
        f"&@dataCotacao={data}&$top=100&$format=json&$select=cotacaoVenda"
    )


def interpretar_bacen(texto: str) -> Decimal | None:
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError:
        return None
    linhas = dados.get("value") if isinstance(dados, dict) else None
    if not isinstance(linhas, list) or not linhas or not isinstance(linhas[-1], dict):
        return None
    return _taxa(linhas[-1].get("cotacaoVenda"))


def interpretar_awesome(texto: str) -> dict[str, Decimal]:
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError:
        return {}
    if not isinstance(dados, dict):
        return {}
    achados = {}
    for moeda, chave in (("USD", "USDBRL"), ("CNY", "CNYBRL")):
        bloco = dados.get(chave)
        if not isinstance(bloco, dict):
            continue
        taxa = _taxa(bloco.get("bid"))
        if taxa is not None:
            achados[moeda] = taxa
    return achados


def buscar_texto_permitido(url: str) -> str | None:
    """Só os dois hosts de câmbio, no IP público já resolvido, sem seguir redirect."""
    partes = urlsplit(url)
    host = (partes.hostname or "").lower().rstrip(".")
    if partes.scheme != "https" or host not in HOSTS_CAMBIO:
        return None
    ips = resolver_publico(host)
    if not ips:
        return None
    try:
        resposta = _abrir_pagina(url, host, ips[0])
    except requests.RequestException:
        logger.info("Falha ao ler a paridade em %s.", host)
        return None
    try:
        if resposta.is_redirect or resposta.status_code != 200:
            return None
        return _ler_corpo(resposta)
    finally:
        resposta.close()


def coletar_paridades(vigencia: date, buscar=buscar_texto_permitido) -> list[tuple[str, Decimal, str]]:
    """AwesomeAPI só entra se o Bacen não gravou nenhuma linha naquele dia."""
    achados: list[tuple[str, Decimal, str]] = []
    for moeda in ("USD", "CNY"):
        corpo = buscar(url_bacen(moeda, vigencia))
        taxa = interpretar_bacen(corpo) if isinstance(corpo, str) else None
        if taxa is not None:
            achados.append((moeda, taxa, "bacen"))
    if any(fonte == "bacen" for _moeda, _taxa, fonte in achados):
        return achados
    corpo = buscar(URL_AWESOME)
    if not isinstance(corpo, str):
        return achados
    for moeda, taxa in interpretar_awesome(corpo).items():
        achados.append((moeda, taxa, "awesomeapi"))
    return achados


def gravar_paridades(cur, vigencia: date, linhas: list[tuple[str, Decimal, str]]) -> None:
    for moeda, taxa, fonte in linhas:
        cur.execute(
            """
            INSERT INTO paridade_cambio (moeda_origem, moeda_destino, taxa, fonte, vigencia)
            VALUES (%(moeda)s, 'BRL', %(taxa)s, %(fonte)s, %(vigencia)s)
            ON CONFLICT DO NOTHING
            """,
            {"moeda": moeda, "taxa": taxa, "fonte": fonte, "vigencia": vigencia},
        )


def taxas_do_dia(cur, vigencia: date) -> tuple[dict[str, Decimal], str | None]:
    cur.execute(
        """
        SELECT moeda_origem, taxa, fonte
        FROM paridade_cambio
        WHERE moeda_destino = 'BRL'
          AND vigencia = %(vigencia)s
          AND fonte = 'bacen'
        """,
        {"vigencia": vigencia},
    )
    linhas = _linhas(cur)
    if not linhas:
        cur.execute(
            """
            SELECT moeda_origem, taxa, fonte
            FROM paridade_cambio
            WHERE moeda_destino = 'BRL'
              AND vigencia = %(vigencia)s
              AND fonte = 'awesomeapi'
            """,
            {"vigencia": vigencia},
        )
        linhas = _linhas(cur)
    taxas: dict[str, Decimal] = {}
    fonte = None
    for linha in linhas:
        taxa = _taxa(linha.get("taxa"))
        moeda = linha.get("moeda_origem")
        if taxa is not None and isinstance(moeda, str):
            taxas[moeda] = taxa
            fonte = linha.get("fonte") if isinstance(linha.get("fonte"), str) else fonte
    return taxas, fonte if isinstance(fonte, str) else None


def converter_para_brl(valor: object, moeda: object, taxas: dict[str, Decimal]) -> Decimal | None:
    if isinstance(valor, bool) or valor is None or not isinstance(moeda, str):
        return None
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    if not numero.is_finite() or numero < 0:
        return None
    if moeda == "BRL":
        fator = Decimal(1)
    else:
        encontrado = taxas.get(moeda)
        if encontrado is None:
            return None
        fator = encontrado
    return (numero * fator).quantize(Decimal("0.01"))


def completar_brl(cur, oferta_id: int, venda: object, moeda_venda: object, cogs: object, moeda_cogs: object) -> bool:
    """Preenche as colunas em BRL quando há taxa. Não mexe no preço nem no COGS de origem."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    vigencia = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    taxas, fonte = taxas_do_dia(cur, vigencia)
    venda_brl = converter_para_brl(venda, moeda_venda, taxas)
    cogs_brl = converter_para_brl(cogs, moeda_cogs, taxas)
    if venda_brl is None or cogs_brl is None:
        return False
    if moeda_venda == moeda_cogs == "BRL":
        fonte = None
    cur.execute(
        """
        UPDATE ofertas
        SET venda_brl = %(venda)s,
            cogs_brl = %(cogs)s,
            paridade_vigencia = %(vigencia)s,
            paridade_fonte = %(fonte)s
        WHERE id = %(id)s
          AND venda_brl IS NULL
        """,
        {
            "venda": venda_brl,
            "cogs": cogs_brl,
            "vigencia": vigencia if fonte else None,
            "fonte": fonte,
            "id": oferta_id,
        },
    )
    return cur.rowcount == 1


def _taxa(valor: object) -> Decimal | None:
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        taxa = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    if not taxa.is_finite() or taxa <= 0:
        return None
    return taxa


def _linhas(cur) -> list[dict]:
    linhas = cur.fetchall()
    if not isinstance(linhas, list):
        return []
    return [linha for linha in linhas if isinstance(linha, dict)]
