import logging
import os
import re
import sys
from urllib.parse import unquote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from celery.signals import worker_process_init, worker_process_shutdown
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.celery_app import app  # noqa: E402
from core.secrets import ler_segredo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

pool_db: ConnectionPool | None = None
_CHAVE_VIDEO = re.compile(r"(?:^|/)(videos/[A-Za-z0-9_-]+\.mp4)$")
_CHAVES_R2 = (
    "CLOUDFLARE_R2_ACCOUNT_ID",
    "CLOUDFLARE_R2_ACCESS_KEY",
    "CLOUDFLARE_R2_SECRET_KEY",
    "CLOUDFLARE_R2_BUCKET",
)

OFERTAS_FRACAS_SQL = """
SELECT id, url_video_r2
FROM ofertas
WHERE score < 40
  AND data_ultima_captura < NOW() - INTERVAL '30 days'
  AND (
    status_qualificacao <> 'processando_ia'
    OR processamento_iniciado_em < NOW() - INTERVAL '2 hours'
  )
ORDER BY data_ultima_captura, id
LIMIT 100
"""

OFERTA_ELEGIVEL_TRAVADA_SQL = """
SELECT id, url_video_r2
FROM ofertas
WHERE id = %s
  AND score < 40
  AND data_ultima_captura < NOW() - INTERVAL '30 days'
  AND (
    status_qualificacao <> 'processando_ia'
    OR processamento_iniciado_em < NOW() - INTERVAL '2 hours'
  )
FOR UPDATE SKIP LOCKED
"""


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


def _chave_objeto_r2(url: str) -> str | None:
    caminho = unquote(urlsplit(url).path)
    encontrada = _CHAVE_VIDEO.search(caminho)
    if not encontrada:
        return None
    return encontrada.group(1)


def _ofertas_fracas() -> list[dict]:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(OFERTAS_FRACAS_SQL)
            ofertas = cur.fetchall()
            conn.commit()
            return ofertas


def _criar_cliente_r2():
    if not all(ler_segredo(nome) for nome in _CHAVES_R2):
        return None
    account_id = ler_segredo("CLOUDFLARE_R2_ACCOUNT_ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=ler_segredo("CLOUDFLARE_R2_ACCESS_KEY"),
        aws_secret_access_key=ler_segredo("CLOUDFLARE_R2_SECRET_KEY"),
        region_name="auto",
        config=Config(
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _apagar_objeto(cliente, chave: str) -> bool:
    if cliente is None:
        logger.warning("Credenciais R2 ausentes; objeto %s não apagado.", chave)
        return False
    try:
        cliente.delete_object(Bucket=ler_segredo("CLOUDFLARE_R2_BUCKET"), Key=chave)
        return True
    except ClientError as error:
        codigo = error.response.get("Error", {}).get("Code", "")
        if codigo in {"404", "NoSuchKey", "NotFound"}:
            return True
        logger.warning("Falha ao apagar objeto %s: %s", chave, error)
        return False
    except BotoCoreError as error:
        logger.warning("Falha ao apagar objeto %s: %s", chave, error)
        return False


def _expirar_uma(oferta: dict, cliente) -> bool:
    oferta_id = oferta["id"]
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(OFERTA_ELEGIVEL_TRAVADA_SQL, (oferta_id,))
            oferta_travada = cur.fetchone()
            if not oferta_travada:
                conn.rollback()
                return False

            url = oferta_travada.get("url_video_r2")
            if isinstance(url, str) and url.strip():
                cur.execute(
                    """
                    SELECT 1
                    FROM ofertas
                    WHERE url_video_r2 = %s
                      AND id <> %s
                    LIMIT 1
                    """,
                    (url, oferta_id),
                )
                compartilhada = cur.fetchone() is not None
                if not compartilhada:
                    chave = _chave_objeto_r2(url)
                    if not chave:
                        logger.warning("Oferta %s mantida: URL de vídeo sem chave videos/<id>.mp4.", oferta_id)
                        conn.rollback()
                        return False
                    if not _apagar_objeto(cliente, chave):
                        logger.warning("Oferta %s mantida: falha ao apagar o objeto %s.", oferta_id, chave)
                        conn.rollback()
                        return False

            cur.execute("DELETE FROM ofertas WHERE id = %s", (oferta_id,))
            removida = cur.rowcount == 1
            conn.commit()

    if removida:
        logger.info("Oferta %s expirada.", oferta_id)
    return removida


@app.task(name="tasks.lifecycle.expirar_ofertas_fracas")
def expirar_ofertas_fracas() -> dict:
    """Remove ofertas fracas antigas e o vídeo exclusivo correspondente no R2."""
    ofertas = _ofertas_fracas()
    cliente = _criar_cliente_r2() if any(item.get("url_video_r2") for item in ofertas) else None
    removidas = sum(_expirar_uma(oferta, cliente) for oferta in ofertas)
    return {"status": "expired", "removidas": removidas}
