"""Lê os blocos JSON que uma página pública embute no HTML."""

import json
import re

_BLOCO_JSON = re.compile(
    r"<script[^>]*type=[\"']application/(?:ld\+)?json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def objetos_json(html: str) -> list:
    achados = []
    for bloco in _BLOCO_JSON.findall(html):
        try:
            achados.append(json.loads(bloco))
        except json.JSONDecodeError:
            continue
    return achados
