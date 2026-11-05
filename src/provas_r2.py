"""Copia a foto do depoimento para o R2. Se falhar, o texto fica sem foto_url."""

import hashlib
import logging
import shutil
import subprocess  # nosec B404

import boto3
import requests
from botocore.config import Config
from core.secrets import ler_segredo
from src.landing import _abrir_pagina, resolver_publico

logger = logging.getLogger(__name__)

TETO_BYTES = 2_000_000
_CHAVES_R2 = (
    "CLOUDFLARE_R2_ACCOUNT_ID",
    "CLOUDFLARE_R2_ACCESS_KEY",
    "CLOUDFLARE_R2_SECRET_KEY",
    "CLOUDFLARE_R2_BUCKET",
)


def arquivar_fotos(oferta_id: object, provas: list[dict]) -> list[dict]:
    """Troca a URL do CDN pela chave reviews/ ou remove a foto."""
    if isinstance(oferta_id, bool) or not isinstance(oferta_id, int):
        return [_sem_foto(prova) for prova in provas]
    arquivadas = []
    for prova in provas:
        if not isinstance(prova, dict):
            continue
        foto = prova.get("foto_url")
        chave = _arquivar_uma(oferta_id, foto) if isinstance(foto, str) else None
        limpa = {chave_item: valor for chave_item, valor in prova.items() if chave_item != "foto_url"}
        if chave:
            limpa["foto_url"] = chave
        arquivadas.append(limpa)
    return arquivadas


def baixar_imagem(url: str) -> tuple[bytes, str] | None:
    from urllib.parse import urlsplit

    partes = urlsplit(url)
    host = (partes.hostname or "").lower()
    if partes.scheme != "https" or not host:
        return None
    ips = resolver_publico(host)
    if not ips:
        return None
    try:
        resposta = _abrir_pagina(url, host, ips[0])
    except requests.RequestException:
        return None
    try:
        if resposta.is_redirect or resposta.status_code != 200:
            return None
        tipo = (resposta.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if not tipo.startswith("image/"):
            return None
        pedacos: list[bytes] = []
        total = 0
        for pedaco in resposta.iter_content(16_384):
            if not pedaco:
                continue
            total += len(pedaco)
            if total > TETO_BYTES:
                return None
            pedacos.append(pedaco)
        if not pedacos:
            return None
        return b"".join(pedacos), tipo
    finally:
        resposta.close()


def converter_webp(conteudo: bytes) -> bytes | None:
    executavel = shutil.which("ffmpeg")
    if not executavel:
        return None
    # O executável vem do PATH e o conteúdo entra só pelo stdin.
    try:
        processo = subprocess.run(  # nosec B603
            [executavel, "-v", "error", "-i", "pipe:0", "-f", "webp", "pipe:1"],
            input=conteudo,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if processo.returncode != 0 or not processo.stdout:
        return None
    return processo.stdout


def _arquivar_uma(oferta_id: int, url: str) -> str | None:
    baixada = baixar_imagem(url)
    if baixada is None:
        return None
    conteudo, _tipo = baixada
    webp = converter_webp(conteudo)
    if webp is None:
        return None
    digest = hashlib.sha256(webp).hexdigest()[:32]
    chave = f"reviews/{oferta_id}/{digest}.webp"
    if not _enviar(chave, webp):
        return None
    return chave


def _enviar(chave: str, corpo: bytes) -> bool:
    if not all(ler_segredo(nome).strip() for nome in _CHAVES_R2):
        return False
    conta = ler_segredo("CLOUDFLARE_R2_ACCOUNT_ID").strip()
    try:
        cliente = boto3.client(
            "s3",
            endpoint_url=f"https://{conta}.r2.cloudflarestorage.com",
            aws_access_key_id=ler_segredo("CLOUDFLARE_R2_ACCESS_KEY").strip(),
            aws_secret_access_key=ler_segredo("CLOUDFLARE_R2_SECRET_KEY").strip(),
            region_name="auto",
            config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 2, "mode": "standard"}),
        )
        cliente.put_object(
            Bucket=ler_segredo("CLOUDFLARE_R2_BUCKET").strip(),
            Key=chave,
            Body=corpo,
            ContentType="image/webp",
        )
    except Exception:
        logger.info("Foto de review não foi para o R2: %s.", chave)
        return False
    return True


def _sem_foto(prova: dict) -> dict:
    if not isinstance(prova, dict):
        return {}
    return {chave: valor for chave, valor in prova.items() if chave != "foto_url"}
