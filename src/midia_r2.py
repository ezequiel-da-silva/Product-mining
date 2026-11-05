"""A referência persistida é a chave. A URL de playback nasce na leitura e morre em 300 s."""

import re
from urllib.parse import unquote, urlsplit

import boto3
from core.secrets import ler_segredo

TTL_LEITURA_SEGUNDOS = 300
_CHAVE_OBJETO = re.compile(r"(?:^|/)((?:videos/[A-Za-z0-9_-]+\.mp4|reviews/\d+/[a-f0-9]{32}\.webp))$")
_CONTA_E_BALDE = (
    "CLOUDFLARE_R2_ACCOUNT_ID",
    "CLOUDFLARE_R2_BUCKET",
)


def extrair_chave(valor: str) -> str | None:
    texto = valor.strip()
    if _CHAVE_OBJETO.fullmatch(texto):
        return texto
    caminho = unquote(urlsplit(texto).path)
    encontrada = _CHAVE_OBJETO.search(caminho)
    if not encontrada:
        return None
    return encontrada.group(1)


def referencia_armazenada(chave: str) -> str:
    """Grava a chave do objeto. Nunca a URL pública nem a pré-assinada."""
    extraida = extrair_chave(chave)
    if extraida:
        return extraida
    return chave.lstrip("/")


def _par(acesso: str, segredo: str) -> tuple[str, str] | None:
    chave = ler_segredo(acesso).strip()
    secreta = ler_segredo(segredo).strip()
    if chave and secreta:
        return chave, secreta
    return None


def _pares_r2() -> list[tuple[str, str]]:
    """O par vigente vem primeiro. O próximo só entra se estiver completo."""
    pares = []
    vigente = _par("CLOUDFLARE_R2_ACCESS_KEY", "CLOUDFLARE_R2_SECRET_KEY")
    if vigente:
        pares.append(vigente)
    proximo = _par("CLOUDFLARE_R2_ACCESS_KEY_PROXIMO", "CLOUDFLARE_R2_SECRET_KEY_PROXIMO")
    if proximo:
        pares.append(proximo)
    return pares


def _credenciais_r2() -> bool:
    return all(ler_segredo(nome).strip() for nome in _CONTA_E_BALDE) and bool(_pares_r2())


def _assinar(chave: str) -> str:
    acesso, segredo = _pares_r2()[0]
    conta = ler_segredo("CLOUDFLARE_R2_ACCOUNT_ID").strip()
    cliente = boto3.client(
        "s3",
        endpoint_url=f"https://{conta}.r2.cloudflarestorage.com",
        aws_access_key_id=acesso,
        aws_secret_access_key=segredo,
        region_name="auto",
    )
    return cliente.generate_presigned_url(
        "get_object",
        Params={"Bucket": ler_segredo("CLOUDFLARE_R2_BUCKET").strip(), "Key": chave},
        ExpiresIn=TTL_LEITURA_SEGUNDOS,
    )


def url_leitura(valor: object) -> str | None:
    """Assina a chave reconhecida por 300 s. URL antiga continua legível sem credencial."""
    if not isinstance(valor, str):
        return None
    texto = valor.strip()
    if not texto:
        return None
    chave = extrair_chave(texto)
    if chave and _credenciais_r2():
        return _assinar(chave)
    if texto.startswith(("http://", "https://")):
        return texto
    return None
