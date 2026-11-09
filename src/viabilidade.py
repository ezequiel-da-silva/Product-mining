"""Custo de aterrissagem. A alíquota vem da tabela, não do código."""

import logging
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

NOMES = ("ii", "icms", "gateway_percentual", "gateway_fixo_brl", "chargeback")


def custo_liquido(venda: Decimal, cogs: Decimal, frete: Decimal, aliquota: dict[str, Decimal]) -> Decimal:
    base = cogs + frete
    ii = base * aliquota["ii"]
    icms = (base + ii) / (Decimal(1) - aliquota["icms"]) * aliquota["icms"]
    gateway = venda * aliquota["gateway_percentual"] + aliquota["gateway_fixo_brl"]
    chargeback = venda * aliquota["chargeback"]
    return (venda - cogs - frete - ii - icms - gateway - chargeback).quantize(Decimal("0.01"))


def imposto_estimado(cogs: Decimal, frete: Decimal, aliquota: dict[str, Decimal]) -> Decimal:
    base = cogs + frete
    ii = base * aliquota["ii"]
    icms = (base + ii) / (Decimal(1) - aliquota["icms"]) * aliquota["icms"]
    return (ii + icms).quantize(Decimal("0.01"))


def _decimal(valor: object) -> Decimal | None:
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    if not numero.is_finite():
        return None
    return numero


def carregar_aliquota(cur) -> dict[str, Decimal] | None:
    cur.execute("SELECT nome, valor FROM parametro_viabilidade WHERE nome = ANY(%(nomes)s)", {"nomes": list(NOMES)})
    linhas = cur.fetchall()
    if not isinstance(linhas, list):
        return None
    achados: dict[str, Decimal] = {}
    for linha in linhas:
        if not isinstance(linha, dict):
            continue
        numero = _decimal(linha.get("valor"))
        nome = linha.get("nome")
        if isinstance(nome, str) and numero is not None:
            achados[nome] = numero
    if any(nome not in achados for nome in NOMES):
        return None
    if achados["icms"] >= 1:
        return None
    return achados


def frete_do_nicho(cur, nicho: object) -> Decimal | None:
    if not isinstance(nicho, str) or not nicho.strip():
        return None
    cur.execute("SELECT frete_brl FROM faixa_frete_nicho WHERE nicho = %(nicho)s", {"nicho": nicho.strip()})
    linha = cur.fetchone()
    if not isinstance(linha, dict):
        return None
    return _decimal(linha.get("frete_brl"))


def aplicar_viabilidade(cur, oferta_id: int, nicho: object, venda: object, cogs: object) -> bool:
    """Sem faixa de frete, a margem líquida permanece nula e o rascunho não nasce."""
    venda_brl = _decimal(venda)
    cogs_brl = _decimal(cogs)
    frete = frete_do_nicho(cur, nicho)
    aliquota = carregar_aliquota(cur)
    if venda_brl is None or cogs_brl is None or frete is None or aliquota is None:
        return False
    cur.execute(
        """
        UPDATE ofertas
        SET frete_estimado_brl = %(frete)s,
            imposto_estimado_brl = %(imposto)s,
            margem_liquida_brl = %(liquida)s
        WHERE id = %(id)s
          AND oferta_canonica_id IS NULL
        """,
        {
            "id": oferta_id,
            "frete": frete,
            "imposto": imposto_estimado(cogs_brl, frete, aliquota),
            "liquida": custo_liquido(venda_brl, cogs_brl, frete, aliquota),
        },
    )
    return cur.rowcount == 1


def preencher_viabilidade_pendente(cur) -> int:
    cur.execute(
        """
        SELECT id, nicho, venda_brl, cogs_brl
        FROM ofertas
        WHERE oferta_canonica_id IS NULL
          AND venda_brl IS NOT NULL
          AND cogs_brl IS NOT NULL
          AND margem_liquida_brl IS NULL
          AND nicho IN (SELECT nicho FROM faixa_frete_nicho)
        ORDER BY id
        LIMIT 50
        """
    )
    linhas = cur.fetchall()
    if not isinstance(linhas, list):
        return 0
    total = 0
    for linha in linhas:
        if isinstance(linha, dict) and aplicar_viabilidade(
            cur, int(linha["id"]), linha.get("nicho"), linha.get("venda_brl"), linha.get("cogs_brl")
        ):
            total += 1
    return total
