"""Referência estável do vídeo no banco e URL curta só na hora de ler."""

import re
from urllib.parse import unquote, urlsplit

import boto3
from core.secrets import ler_segredo

TTL_LEITURA_SEGUNDOS = 300
_CHAVE_VIDEO = re.compile(r"(?:^|/)(videos/[A-Za-z0-9_-]+\.mp4)$")
_CREDENCIAIS = (
    "CLOUDFLARE_R2_ACCOUNT_ID",
    "CLOUDFLARE_R2_ACCESS_KEY",
    "CLOUDFLARE_R2_SECRET_KEY",
    "CLOUDFLARE_R2_BUCKET",
)


def _url_publica() -> str:
    return ler_segredo("CLOUDFLARE_R2_PUBLIC_URL").strip().rstrip("/")


def _credenciais_r2() -> bool:
    return all(ler_segredo(nome) for nome in _CREDENCIAIS)


def extrair_chave(valor: str) -> str | None:
    texto = valor.strip()
    if _CHAVE_VIDEO.fullmatch(texto):
        return texto
    caminho = unquote(urlsplit(texto).path)
    encontrada = _CHAVE_VIDEO.search(caminho)
    if not encontrada:
        return None
    return encontrada.group(1)


def referencia_armazenada(chave: str) -> str:
    """URL pública estável, ou a chave do objeto quando não há domínio público."""
    publico = _url_publica()
    if publico:
        return f"{publico}/{chave.lstrip('/')}"
    return chave


def _assinar(chave: str) -> str:
    conta = ler_segredo("CLOUDFLARE_R2_ACCOUNT_ID")
    cliente = boto3.client(
        "s3",
        endpoint_url=f"https://{conta}.r2.cloudflarestorage.com",
        aws_access_key_id=ler_segredo("CLOUDFLARE_R2_ACCESS_KEY"),
        aws_secret_access_key=ler_segredo("CLOUDFLARE_R2_SECRET_KEY"),
        region_name="auto",
    )
    return cliente.generate_presigned_url(
        "get_object",
        Params={"Bucket": ler_segredo("CLOUDFLARE_R2_BUCKET"), "Key": chave},
        ExpiresIn=TTL_LEITURA_SEGUNDOS,
    )


def url_leitura(valor: object) -> str | None:
    """URL https para playback ou download. A chave é assinada por poucos minutos."""
    if not isinstance(valor, str):
        return None
    texto = valor.strip()
    if not texto:
        return None
    publico = _url_publica()
    if publico and (texto == publico or texto.startswith(f"{publico}/")):
        return texto
    if texto.startswith(("http://", "https://")) and extrair_chave(texto) is None:
        return texto
    chave = extrair_chave(texto)
    if chave and texto.startswith("https://") and _credenciais_r2():
        return _assinar(chave)
    if chave and not texto.startswith(("http://", "https://")) and _credenciais_r2():
        return _assinar(chave)
    if texto.startswith(("http://", "https://")):
        return texto
    return None
