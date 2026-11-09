"""Descarta criativo genérico antes do Gemini. Sem arquivo de modelo, o fluxo segue."""

import logging
import os

logger = logging.getLogger(__name__)

CONFIANCA_MINIMA = 0.90
_CARREGADO: dict[str, object] = {}


def triagem_local(texto: str, nicho_catalogo: set[str]) -> str:
    modelo = os.environ.get("MODELO_TRIAGEM_PATH", "").strip()
    if not modelo or not os.path.isfile(modelo):
        return "seguir"
    try:
        rotulo, confianca = prever(modelo, texto[:2000])
    except Exception:
        logger.info("Triagem local indisponível; a oferta segue para o Gemini.")
        return "seguir"
    if confianca >= CONFIANCA_MINIMA and rotulo == "generico":
        return "descartar"
    if confianca >= CONFIANCA_MINIMA and rotulo not in nicho_catalogo:
        return "descartar"
    return "seguir"


def prever(caminho: str, texto: str) -> tuple[str, float]:
    modelo = _CARREGADO.get(caminho)
    if modelo is None:
        modelo = _carregar(caminho)
        _CARREGADO[caminho] = modelo
    return modelo(texto)  # type: ignore[operator, no-any-return]


def _carregar(caminho: str):
    if caminho.endswith(".onnx"):
        return _onnx(caminho)
    return _fasttext(caminho)


def _fasttext(caminho: str):
    import fasttext

    modelo = fasttext.load_model(caminho)

    def aplicar(texto: str) -> tuple[str, float]:
        rotulos, probs = modelo.predict(texto.replace("\n", " "))
        rotulo = str(rotulos[0]).removeprefix("__label__")
        return rotulo, float(probs[0])

    return aplicar


def _onnx(caminho: str):
    import onnxruntime

    sessao = onnxruntime.InferenceSession(caminho, providers=["CPUExecutionProvider"])
    entrada = sessao.get_inputs()[0].name

    def aplicar(texto: str) -> tuple[str, float]:
        rotulo, confianca = sessao.run(None, {entrada: [[texto]]})[0][0]
        return str(rotulo), float(confianca)

    return aplicar
