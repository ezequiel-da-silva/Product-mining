import hashlib
import ipaddress
import logging
import os
import re
import socket
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError
from celery.signals import worker_process_init, worker_process_shutdown
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.celery_app import app  # noqa: E402
from core.pontuacao import BONUS_ANALISE_IA, DESPACHO_IMEDIATO, TETO_HEURISTICO, TETO_SCORE  # noqa: E402
from core.secrets import ler_segredo  # noqa: E402
from src.comercial import qualificar_pagina  # noqa: E402
from src.midia_r2 import referencia_armazenada  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

pool_db: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Abre o pool no processo atual, nunca no processo pai do Celery."""
    global pool_db
    if pool_db is None:
        conninfo = os.environ.get("POSTGRES_URL")
        if not conninfo:
            raise RuntimeError("POSTGRES_URL é obrigatória.")
        pool_db = ConnectionPool(
            conninfo=conninfo,
            min_size=1,
            max_size=2,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return pool_db


@worker_process_init.connect
def _open_db_pool(**_kwargs):
    get_pool()


@worker_process_shutdown.connect
def _close_db_pool(**_kwargs):
    global pool_db
    if pool_db is not None:
        pool_db.close()
        pool_db = None


MAX_VIDEO_BYTES = 250 * 1024 * 1024
R2_MULTIPART_PART_BYTES = 8 * 1024 * 1024
MAX_FALHAS_IA = 3
MIN_AVISTAMENTOS_IA = 2
SCORE_DESPACHO_IMEDIATO = DESPACHO_IMEDIATO
TETO_FALHAS_IA = 5
ESPERA_REPROCESSAMENTO_IA = timedelta(days=7)
COMMERCIAL_CTAS = {
    "BUY_NOW",
    "COMPRAR_AGORA",
    "GET_OFFER",
    "LEARN_MORE",
    "OBTER_OFERTA",
    "ORDER_NOW",
    "SAIBA_MAIS",
    "SAIBAR_MAIS",
    "SHOP_NOW",
}
ECOMMERCE_MARKERS = (
    "shopify",
    "yampi",
    "cartpanda",
    "nuvemshop",
    "tiendanube",
    "woocommerce",
    "checkout",
)
VIDEO_HOST_SUFFIXES = ("fbcdn.net", "fbsbx.com")


def _normalizar_cta(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^A-Z0-9]+", "_", normalized.upper()).strip("_")


def _extrair_payload(payload: dict) -> dict:
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}

    cards = snapshot.get("cards")
    first_card = cards[0] if isinstance(cards, list) and cards else {}
    if not isinstance(first_card, dict):
        first_card = {}

    body = snapshot.get("body")
    if isinstance(body, dict):
        copy_texto = body.get("text", "")
    elif isinstance(body, str):
        copy_texto = body
    else:
        copy_texto = ""
    if not copy_texto:
        message = snapshot.get("message", "")
        copy_texto = message if isinstance(message, str) else ""

    cta = snapshot.get("cta_text") or snapshot.get("cta_type")
    page_id = payload.get("page_id") or snapshot.get("page_id")
    video_url = None

    videos = payload.get("videos")
    if isinstance(videos, list) and videos and isinstance(videos[0], dict):
        video_url = videos[0].get("video_hd_url")

    if not video_url:
        snapshot_videos = snapshot.get("videos")
        if isinstance(snapshot_videos, list) and snapshot_videos and isinstance(snapshot_videos[0], dict):
            video_url = snapshot_videos[0].get("video_sd_url") or snapshot_videos[0].get("video_hd_url")

    return {
        "ad_id": payload.get("ad_archive_id") or payload.get("collateral_id") or payload.get("id"),
        "url_destino": snapshot.get("link_url") or first_card.get("link_url") or payload.get("link_url"),
        "copy_texto": copy_texto if isinstance(copy_texto, str) else "",
        "tipo_cta": cta if isinstance(cta, str) else None,
        "page_id": str(page_id) if page_id is not None else None,
        "video_url": video_url,
        "ad_delivery_start_time": payload.get("ad_delivery_start_time"),
    }


def _data_inicio(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()
    if text.isdigit():
        return _data_inicio(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _url_ecommerce(url: str) -> bool:
    normalized = url.lower()
    return any(marker in normalized for marker in ECOMMERCE_MARKERS)


def _calcular_score(campos: dict, now: datetime | None = None) -> int:
    score = 0
    if campos.get("video_url"):
        score += 15
    if _normalizar_cta(campos.get("tipo_cta")) in COMMERCIAL_CTAS:
        score += 15

    start_time = _data_inicio(campos.get("ad_delivery_start_time"))
    current_time = now or datetime.now(timezone.utc)
    if start_time and (current_time - start_time).total_seconds() > 5 * 24 * 60 * 60:
        score += 20

    destination = campos.get("url_destino")
    if isinstance(destination, str) and _url_ecommerce(destination):
        score += 25

    return min(score, TETO_HEURISTICO)


def _r2_configurado() -> bool:
    return all(
        ler_segredo(key)
        for key in (
            "CLOUDFLARE_R2_ACCOUNT_ID",
            "CLOUDFLARE_R2_ACCESS_KEY",
            "CLOUDFLARE_R2_SECRET_KEY",
            "CLOUDFLARE_R2_BUCKET",
        )
    )


def _criar_cliente_r2():
    account_id = ler_segredo("CLOUDFLARE_R2_ACCOUNT_ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=ler_segredo("CLOUDFLARE_R2_ACCESS_KEY"),
        aws_secret_access_key=ler_segredo("CLOUDFLARE_R2_SECRET_KEY"),
        region_name="auto",
    )


def _url_r2(_client, _bucket: str, key: str) -> str:
    return referencia_armazenada(key)


def _host_video_permitido(video_url: str) -> bool:
    try:
        parsed = urlsplit(video_url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    return any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in VIDEO_HOST_SUFFIXES)


def _endereco_publico(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(ip.is_global)


def _rejeitar_ip_nao_publico(hostname: str) -> None:
    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Não foi possível resolver o host do vídeo.") from exc
    if not infos:
        raise ValueError("Não foi possível resolver o host do vídeo.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _endereco_publico(ip):
            raise ValueError("O host do vídeo resolve para um endereço não público.")


def _url_video_existente(client, bucket: str, object_key: str) -> str | None:
    try:
        client.head_object(Bucket=bucket, Key=object_key)
    except ClientError as error:
        error_code = str(error.response.get("Error", {}).get("Code", ""))
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    return _url_r2(client, bucket, object_key)


def _abrir_stream_video(video_url: str):
    current_url = video_url
    for _ in range(6):
        if not _host_video_permitido(current_url):
            raise ValueError("A URL ou o redirecionamento do vídeo não é permitido.")
        _rejeitar_ip_nao_publico(urlsplit(current_url).hostname or "")
        response = requests.get(
            current_url,
            stream=True,
            timeout=(10, 60),
            allow_redirects=False,
        )
        if response.is_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("Redirecionamento de vídeo sem cabeçalho Location.")
            current_url = urljoin(current_url, location)
            continue
        response.raise_for_status()
        return response
    raise ValueError("O vídeo excedeu o limite de redirecionamentos permitidos.")


def _enviar_stream_multipart(client, bucket: str, object_key: str, response) -> str:
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_VIDEO_BYTES:
        raise ValueError("Vídeo excede o limite de 250 MB.")

    response.raw.decode_content = True
    digest = hashlib.sha256()
    multipart = client.create_multipart_upload(Bucket=bucket, Key=object_key, ContentType="video/mp4")
    upload_id = multipart["UploadId"]
    parts = []
    buffered = bytearray()
    bytes_uploaded = 0

    try:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            digest.update(chunk)
            bytes_uploaded += len(chunk)
            if bytes_uploaded > MAX_VIDEO_BYTES:
                raise ValueError("Vídeo excede o limite de 250 MB.")
            buffered.extend(chunk)

            while len(buffered) >= R2_MULTIPART_PART_BYTES:
                part_number = len(parts) + 1
                part = client.upload_part(
                    Bucket=bucket,
                    Key=object_key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=bytes(buffered[:R2_MULTIPART_PART_BYTES]),
                )
                parts.append({"ETag": part["ETag"], "PartNumber": part_number})
                del buffered[:R2_MULTIPART_PART_BYTES]

        if bytes_uploaded == 0:
            raise ValueError("A resposta de vídeo está vazia.")

        if not parts:
            client.abort_multipart_upload(Bucket=bucket, Key=object_key, UploadId=upload_id)
            client.put_object(
                Bucket=bucket,
                Key=object_key,
                Body=bytes(buffered),
                ContentType="video/mp4",
            )
            return digest.hexdigest()

        if buffered:
            part_number = len(parts) + 1
            part = client.upload_part(
                Bucket=bucket,
                Key=object_key,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=bytes(buffered),
            )
            parts.append({"ETag": part["ETag"], "PartNumber": part_number})
        client.complete_multipart_upload(
            Bucket=bucket,
            Key=object_key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        return digest.hexdigest()
    except Exception:
        try:
            client.abort_multipart_upload(Bucket=bucket, Key=object_key, UploadId=upload_id)
        except (BotoCoreError, ClientError) as error:
            logger.warning("Falha ao abortar multipart do vídeo %s: %s", object_key, error)
        raise


def _upload_video_r2(video_url: str | None, ad_id: object) -> tuple[str | None, str | None]:
    if not video_url:
        return None, None
    if not ad_id:
        logger.warning("Vídeo ignorado: anúncio sem identificador para a chave do objeto R2.")
        return None, None
    if not _host_video_permitido(video_url):
        logger.warning("URL de vídeo ignorada por não pertencer a um host HTTPS permitido.")
        return None, None
    if not _r2_configurado():
        logger.warning("Credenciais incompletas do Cloudflare R2; vídeo não enviado.")
        return None, None

    bucket = ler_segredo("CLOUDFLARE_R2_BUCKET")
    safe_ad_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(ad_id or "unknown"))
    object_key = f"videos/{safe_ad_id}.mp4"

    try:
        client = _criar_cliente_r2()
        existing_url = _url_video_existente(client, bucket, object_key)
        if existing_url:
            return existing_url, None

        with _abrir_stream_video(video_url) as response:
            digest = _enviar_stream_multipart(client, bucket, object_key, response)

        return _url_r2(client, bucket, object_key), digest
    except (BotoCoreError, ClientError, requests.RequestException, ValueError, KeyError) as error:
        logger.warning("Falha ao enviar o vídeo %s ao Cloudflare R2: %s", object_key, error)
        return None, None
    except Exception as error:
        logger.exception("Erro inesperado no upload R2 do vídeo %s: %s", object_key, error)
        return None, None


def _deve_analisar(phash: str, oferta_id: int) -> bool:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%(phash)s)::bigint)", {"phash": phash})
            cur.execute(
                """
                SELECT id
                FROM ofertas
                WHERE phash_criativo = %(phash)s
                  AND id <> %(id)s
                  AND (
                    status_qualificacao IN ('concluido', 'processando_ia')
                    OR (
                      id < %(id)s
                      AND status_qualificacao = 'pendente'
                      AND url_video_r2 IS NOT NULL
                      AND score >= 50
                    )
                  )
                LIMIT 1
                """,
                {"phash": phash, "id": oferta_id},
            )
            duplicada = cur.fetchone() is not None
            conn.commit()
            return not duplicada


def _persistir_oferta(
    campos: dict,
    score: int,
    url_video_r2: str | None,
    phash_criativo: str | None,
) -> dict | None:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ofertas (
                    url_destino, page_id, copy_texto, tipo_cta,
                    url_video_r2, score, phash_criativo
                )
                VALUES (
                    %(url_destino)s, %(page_id)s, %(copy_texto)s,
                    %(tipo_cta)s, %(url_video_r2)s, %(score)s, %(phash_criativo)s
                )
                ON CONFLICT (url_destino) DO UPDATE
                SET vezes_avistado = ofertas.vezes_avistado + 1,
                    data_ultima_captura = CURRENT_TIMESTAMP,
                    page_id = COALESCE(EXCLUDED.page_id, ofertas.page_id),
                    copy_texto = COALESCE(NULLIF(EXCLUDED.copy_texto, ''), ofertas.copy_texto),
                    tipo_cta = COALESCE(EXCLUDED.tipo_cta, ofertas.tipo_cta),
                    url_video_r2 = COALESCE(EXCLUDED.url_video_r2, ofertas.url_video_r2),
                    score = GREATEST(ofertas.score, EXCLUDED.score),
                    phash_criativo = COALESCE(EXCLUDED.phash_criativo, ofertas.phash_criativo)
                RETURNING id, score, status_qualificacao, phash_criativo, falhas_ia, falhas_ia_em, vezes_avistado;
                """,
                {
                    "url_destino": campos["url_destino"],
                    "page_id": campos.get("page_id"),
                    "copy_texto": campos.get("copy_texto") or "",
                    "tipo_cta": campos.get("tipo_cta"),
                    "url_video_r2": url_video_r2,
                    "score": score,
                    "phash_criativo": phash_criativo,
                },
            )
            oferta = cur.fetchone()
            conn.commit()
            return oferta


def _pode_despachar_ia(oferta: dict, agora: datetime | None = None) -> bool:
    """Permite até MAX_FALHAS_IA sem espera; depois disso, só reprocessa com teto e espera."""
    falhas = oferta.get("falhas_ia") or 0
    if falhas < MAX_FALHAS_IA:
        return True
    if falhas >= TETO_FALHAS_IA:
        return False
    ultima = oferta.get("falhas_ia_em")
    agora = agora or datetime.now(timezone.utc)
    return ultima is not None and agora - ultima >= ESPERA_REPROCESSAMENTO_IA


def _url_video_ja_analisada(url: str, oferta_id: int) -> bool:
    """Outra oferta com o mesmo objeto já foi paga, está em análise ou tem prioridade na fila."""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%(url)s)::bigint)", {"url": url})
            cur.execute(
                """
                SELECT 1
                FROM ofertas
                WHERE url_video_r2 = %(url)s
                  AND id <> %(id)s
                  AND (
                    status_qualificacao IN ('concluido', 'processando_ia')
                    OR (
                      id < %(id)s
                      AND status_qualificacao = 'pendente'
                      AND score >= 50
                      AND falhas_ia < 5
                    )
                  )
                LIMIT 1
                """,
                {"url": url, "id": oferta_id},
            )
            ja_analisada = cur.fetchone() is not None
            conn.commit()
            return ja_analisada


def _copiar_enriquecimento(oferta_id: int, url_video_r2: str | None, phash: str | None) -> bool:
    """Copia só o criativo da irmã e qualifica o preço na página desta oferta."""
    url_destino = None
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ofertas AS destino
                SET transcricao_video = origem.transcricao_video,
                    gancho_3s = origem.gancho_3s,
                    nicho = origem.nicho,
                    sub_nicho = origem.sub_nicho,
                    gatilho_mental = origem.gatilho_mental,
                    objecoes_quebradas = origem.objecoes_quebradas,
                    angulos_alternativos = origem.angulos_alternativos,
                    embedding_analise = origem.embedding_analise,
                    embedding_texto_hash = origem.embedding_texto_hash,
                    score = LEAST(destino.score + %(bonus)s, %(teto)s),
                    status_qualificacao = 'pendente_lp',
                    dados_comerciais_ausentes = FALSE,
                    processamento_iniciado_em = NULL,
                    processamento_token = NULL
                FROM (
                    SELECT *
                    FROM ofertas
                    WHERE id <> %(id)s
                      AND status_qualificacao = 'concluido'
                      AND embedding_analise IS NOT NULL
                      AND (url_video_r2 = %(url)s OR phash_criativo = %(phash)s)
                    ORDER BY id
                    LIMIT 1
                ) AS origem
                WHERE destino.id = %(id)s
                  AND destino.status_qualificacao = 'pendente'
                RETURNING destino.url_destino;
                """,
                {
                    "id": oferta_id,
                    "url": url_video_r2,
                    "phash": phash,
                    "bonus": BONUS_ANALISE_IA,
                    "teto": TETO_SCORE,
                },
            )
            linha = cur.fetchone()
            enriquecida = linha is not None
            if enriquecida:
                url_destino = linha.get("url_destino")
            conn.commit()
    if not enriquecida:
        return False
    with get_pool().connection() as conn:
        qualificar_pagina(conn, oferta_id, url_destino)
    logger.info("Oferta %s herdou o criativo e seguiu para a página de destino.", oferta_id)
    return True


@app.task(bind=True, name="tasks.qualification.avaliar_oferta", max_retries=3)
def avaliar_oferta(self, payload: dict):
    if not isinstance(payload, dict):
        logger.warning("Payload de anúncio inválido; esperado objeto JSON.")
        return {"status": "invalid_payload"}

    campos = _extrair_payload(payload)
    destination = campos.get("url_destino")
    if not isinstance(destination, str) or not destination.strip():
        logger.warning(
            "Anúncio %s ignorado: não possui URL de destino.",
            campos.get("ad_id"),
        )
        return {"status": "missing_destination"}
    campos["url_destino"] = destination.strip()
    parsed_destination = urlsplit(campos["url_destino"])
    if parsed_destination.scheme not in {"http", "https"} or not parsed_destination.hostname:
        logger.warning(
            "Anúncio %s ignorado: URL de destino inválida.",
            campos.get("ad_id"),
        )
        return {"status": "invalid_destination"}

    try:
        score = _calcular_score(campos)
        video_r2_url, digest = _upload_video_r2(campos.get("video_url"), campos.get("ad_id"))
        oferta = _persistir_oferta(campos, score, video_r2_url, digest)
        if not oferta:
            logger.error("Upsert não retornou uma oferta para URL %s.", campos["url_destino"])
            raise RuntimeError("O upsert da oferta não retornou o registro persistido.")

        phash = digest or oferta.get("phash_criativo")
        if phash and not _deve_analisar(phash, oferta["id"]):
            return {
                "status": "duplicate_creative",
                "oferta_id": oferta["id"],
                "score": oferta["score"],
                "enriquecida": _copiar_enriquecimento(oferta["id"], video_r2_url, phash),
            }

        if (
            score >= 50
            and video_r2_url
            and oferta["status_qualificacao"] == "pendente"
            and ((oferta.get("vezes_avistado") or 0) >= MIN_AVISTAMENTOS_IA or score >= SCORE_DESPACHO_IMEDIATO)
            and _pode_despachar_ia(oferta)
        ):
            if _url_video_ja_analisada(video_r2_url, oferta["id"]):
                return {
                    "status": "duplicate_video_url",
                    "oferta_id": oferta["id"],
                    "score": oferta["score"],
                    "enriquecida": _copiar_enriquecimento(oferta["id"], video_r2_url, phash),
                }
            app.send_task(
                "src.worker_ia.processar_anuncio_multimodal",
                args=[
                    oferta["id"],
                    video_r2_url,
                    campos.get("copy_texto") or "",
                ],
                queue="ai_multimodal_queue",
            )
            motivo = (
                "avistamentos"
                if (oferta.get("vezes_avistado") or 0) >= MIN_AVISTAMENTOS_IA
                else f"score_{SCORE_DESPACHO_IMEDIATO}"
            )
            logger.info(
                "Oferta %s (score %s) despachada para processamento multimodal; motivo=%s.",
                oferta["id"],
                score,
                motivo,
            )

        return {
            "status": "qualified",
            "oferta_id": oferta["id"],
            "score": oferta["score"],
            "video_uploaded": bool(video_r2_url),
        }
    except Exception as error:
        if self.request.retries >= self.max_retries:
            logger.error(
                "Qualificação descartada definitivamente ad=%s url=%s motivo=%s: %s",
                campos.get("ad_id"),
                campos.get("url_destino"),
                type(error).__name__,
                error,
            )
            return {
                "status": "discarded",
                "ad_id": campos.get("ad_id"),
                "url_destino": campos.get("url_destino"),
                "motivo": type(error).__name__,
            }
        logger.exception("Falha ao qualificar anúncio %s: %s", campos.get("ad_id"), error)
        raise self.retry(exc=error, countdown=15 * (2**self.request.retries)) from error
