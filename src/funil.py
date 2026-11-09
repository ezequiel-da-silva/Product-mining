"""Pixel e bump lidos do HTML já baixado. Sem enviar pagamento."""

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

_PIXEL = re.compile(r"""fbq\(\s*['"]init['"]\s*,\s*['"](\d{5,20})['"]""")
_BUMP = ("order-bump", "order_bump", "bump-item", "data-bump")
_PRECO_BUMP = re.compile(r"""data-bump-price\s*=\s*["'](\d{1,6}(?:[.,]\d{2})?)["']""", re.I)

SQL_ATIVIDADE = """
SELECT split_part(regexp_replace(url_destino, '^https?://', ''), '/', 1) AS dominio,
       COUNT(*) AS ofertas,
       SUM(vezes_avistado) AS avistamentos,
       MIN(data_primeira_captura) AS desde
FROM ofertas
WHERE oferta_canonica_id IS NULL
  AND data_ultima_captura >= CURRENT_TIMESTAMP - INTERVAL '7 days'
GROUP BY 1
ORDER BY avistamentos DESC
LIMIT 50
"""


def ler_funil(html: str) -> dict:
    pixels = sorted(set(_PIXEL.findall(html or "")))
    texto = (html or "").casefold()
    preco = _preco_explicito(html or "")
    return {"pixels": pixels, "tem_bump": any(marca in texto for marca in _BUMP), "preco_bump": preco}


def _preco_explicito(html: str) -> Decimal | None:
    achado = _PRECO_BUMP.search(html)
    if not achado:
        return None
    try:
        valor = Decimal(achado.group(1).replace(",", "."))
    except InvalidOperation:
        return None
    if not valor.is_finite() or valor <= 0:
        return None
    return valor.quantize(Decimal("0.01"))


def dominio_de(url: str | None) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    host = urlsplit(url.strip()).hostname
    if not host:
        return None
    return host.lower().rstrip(".")


def gravar_funil(
    cur, oferta_id: int, url: str | None, pixels: tuple[str, ...] | list[str], tem_bump: bool, preco_bump
) -> None:
    cur.execute(
        """
        UPDATE ofertas
        SET tem_order_bump = %(bump)s,
            preco_bump = %(preco)s
        WHERE id = %(id)s
        """,
        {"id": oferta_id, "bump": tem_bump, "preco": preco_bump},
    )
    dominio = dominio_de(url)
    if not dominio:
        return
    for pixel in pixels:
        cur.execute(
            """
            INSERT INTO loja_pixel (pixel_id, dominio)
            VALUES (%(pixel)s, %(dominio)s)
            ON CONFLICT (pixel_id, dominio) DO NOTHING
            """,
            {"pixel": pixel, "dominio": dominio},
        )
