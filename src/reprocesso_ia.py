"""Classe da falha e revalidação do texto já pago, sem nova chamada ao modelo."""

import json

from pydantic import ValidationError
from schemas import AnaliseAnuncioSchema, nicho_canonico


def classe_da_excecao(erro: BaseException) -> str:
    """JSON inválido e validação são estruturais. O resto que esgota retry é transporte."""
    if isinstance(erro, (json.JSONDecodeError, ValidationError, ValueError)):
        return "estrutural"
    return "transporte"


def enquadrar_nicho(dados: dict) -> dict:
    """Encaixa texto livre já pago no catálogo, sem nova chamada ao modelo."""
    ajustado = dict(dados)
    bruto = str(ajustado.get("nicho") or "").strip()
    canonico = nicho_canonico(bruto)
    if canonico:
        ajustado["nicho"] = canonico
        if canonico != "Outros":
            ajustado["sub_nicho"] = None
        return ajustado
    if bruto:
        sub = str(ajustado.get("sub_nicho") or "").strip() or bruto
        ajustado["nicho"] = "Outros"
        ajustado["sub_nicho"] = sub[:100]
    return ajustado


def revalidar_texto(texto: object) -> dict | None:
    """Relê a resposta bruta. Não chama o Gemini."""
    if not isinstance(texto, str) or not texto.strip():
        return None
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError:
        return None
    if not isinstance(dados, dict):
        return None
    try:
        return AnaliseAnuncioSchema(**enquadrar_nicho(dados)).model_dump()
    except ValidationError:
        return None
