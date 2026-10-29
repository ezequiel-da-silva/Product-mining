import hashlib
import ipaddress
import json
import logging
import math
import os
import socket
import sys
import tempfile
import time
import uuid
from urllib.parse import urljoin, urlsplit

import requests
import yaml
from celery.signals import worker_process_init, worker_process_shutdown
from google import genai
from google.genai import types
from google.genai.errors import ClientError
from jinja2 import Template
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.celery_app import app  # noqa: E402
from core.pontuacao import BONUS_ANALISE_IA, TETO_SCORE  # noqa: E402
from core.secrets import ler_segredo  # noqa: E402
from schemas import AnaliseAnuncioSchema, nicho_canonico  # noqa: E402
from src.comercial import qualificar_pagina  # noqa: E402
from src.midia_r2 import url_leitura  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

cliente_ia = genai.Client(api_key=ler_segredo("GEMINI_API_KEY"))

# Pool limitado a 1-2 conexões por processo worker do Celery
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


PERSONA_PATH = os.path.join(BASE_DIR, "prompts", "system_persona.yaml")
instrucao_sistema = (
    "Você é um estrategista de tráfego pago nível Doutor. "
    "Extraia informações de VSLs e anúncios com precisão cirúrgica."
)

if os.path.exists(PERSONA_PATH):
    try:
        with open(PERSONA_PATH, "r", encoding="utf-8") as f:
            config_persona = yaml.safe_load(f)
            instrucao_sistema = config_persona.get("instruction", instrucao_sistema)
    except Exception as e:
        logger.warning(f"Falha ao ler {PERSONA_PATH}, usando instrução padrão: {e}")

MODELO_ANALISE = "gemini-3.8-flash"
MODELO_EMBEDDING = "gemini-embedding-2"
DIMENSAO_EMBEDDING = 768
JANELA_VIDEO_FIM = "30s"
LIMITE_COPY_PROMPT = 2000

MAX_VIDEO_BYTES = 250 * 1024 * 1024  # Teto de 250 MB para proteger tmpfs
PROCESSING_LEASE_SECONDS = 2 * 60 * 60
R2_HOST_SUFFIX = "r2.cloudflarestorage.com"
MAX_VIDEO_REDIRECTS = 5


def _reservar_oferta_para_processamento(oferta_id: int) -> tuple[str, int, str | None]:
    """Reserva a oferta com um token exclusivo e recupera reservas abandonadas."""
    token = str(uuid.uuid4())
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ofertas
                SET status_qualificacao = 'processando_ia',
                    processamento_iniciado_em = CURRENT_TIMESTAMP,
                    processamento_token = %s
                WHERE id = %s
                  AND status_qualificacao != 'concluido'
                  AND (
                      status_qualificacao != 'processando_ia'
                      OR processamento_iniciado_em IS NULL
                      OR processamento_iniciado_em <
                          CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                  )
                RETURNING id;
                """,
                (token, oferta_id, PROCESSING_LEASE_SECONDS),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return "reserved", 0, token

            cur.execute(
                """
                SELECT status_qualificacao,
                       GREATEST(
                           0,
                           CEIL(EXTRACT(EPOCH FROM (
                               processamento_iniciado_em
                               + (%s * INTERVAL '1 second')
                               - CURRENT_TIMESTAMP
                           )))
                       )::INTEGER AS lease_remaining
                FROM ofertas
                WHERE id = %s;
                """,
                (PROCESSING_LEASE_SECONDS, oferta_id),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                return "missing", 0, None
            if row["status_qualificacao"] == "concluido":
                return "completed", 0, None
            if row["status_qualificacao"] == "processando_ia":
                return "processing", row["lease_remaining"] or 0, None
            return "unavailable", 0, None


def _liberar_reserva_erro(oferta_id: int, token: str, definitiva: bool = False):
    """Restaura somente a reserva pertencente a esta execução."""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ofertas
                SET status_qualificacao = 'pendente',
                    processamento_iniciado_em = NULL,
                    processamento_token = NULL,
                    falhas_ia = falhas_ia + %(incremento)s,
                    falhas_ia_em = CASE WHEN %(incremento)s = 1 THEN CURRENT_TIMESTAMP ELSE falhas_ia_em END
                WHERE id = %(id)s
                  AND status_qualificacao = 'processando_ia'
                  AND processamento_token = %(token)s;
                """,
                {"incremento": int(definitiva), "id": oferta_id, "token": token},
            )
            conn.commit()


SQL_PROPAGAR_IRMAS = """
UPDATE ofertas AS irma
SET transcricao_video = origem.transcricao_video,
    gancho_3s = origem.gancho_3s,
    nicho = origem.nicho,
    sub_nicho = origem.sub_nicho,
    gatilho_mental = origem.gatilho_mental,
    objecoes_quebradas = origem.objecoes_quebradas,
    angulos_alternativos = origem.angulos_alternativos,
    embedding_analise = origem.embedding_analise,
    embedding_texto_hash = origem.embedding_texto_hash,
    score = LEAST(irma.score + %(bonus)s, %(teto)s),
    status_qualificacao = 'pendente_lp',
    dados_comerciais_ausentes = FALSE,
    processamento_iniciado_em = NULL,
    processamento_token = NULL
FROM ofertas AS origem
WHERE origem.id = %(id)s
  AND irma.id <> origem.id
  AND irma.status_qualificacao = 'pendente'
  AND (irma.url_video_r2 = origem.url_video_r2 OR irma.phash_criativo = origem.phash_criativo)
RETURNING irma.id, irma.url_destino;
"""


def persistir_resultado_ia(
    oferta_id: int,
    dados: dict,
    embedding: list,
    token: str,
    uso_tokens: dict | None = None,
) -> bool:
    """Grava o enriquecimento somente se esta execução ainda possui a reserva."""
    embedding_str = f"[{','.join(map(str, embedding))}]"
    uso_tokens = uso_tokens or {}
    moeda = dados.get("preco_moeda")
    preco = dados.get("preco_oferta_estimado")
    if moeda not in {"BRL", "USD"}:
        preco = None
        moeda = None

    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ofertas
                SET transcricao_video = %(transcricao)s,
                    gancho_3s = %(gancho)s,
                    nicho = %(nicho)s,
                    sub_nicho = %(sub_nicho)s,
                    gatilho_mental = %(gatilho)s,
                    preco_oferta_estimado = %(preco)s,
                    preco_moeda = %(moeda)s,
                    margem_estimada_pct = %(margem)s,
                    objecoes_quebradas = %(objecoes)s::jsonb,
                    angulos_alternativos = %(angulos)s::jsonb,
                    embedding_analise = %(embedding)s::vector,
                    embedding_texto_hash = %(embedding_texto_hash)s,
                    tokens_prompt_ia = %(tokens_prompt)s,
                    tokens_saida_ia = %(tokens_saida)s,
                    score = CASE
                        WHEN status_qualificacao != 'concluido' THEN LEAST(score + %(bonus)s, %(teto)s)
                        ELSE score
                    END,
                    dados_comerciais_ausentes = (%(preco)s IS NULL),
                    status_qualificacao = 'concluido',
                    processamento_iniciado_em = NULL,
                    processamento_token = NULL
                WHERE id = %(id)s
                  AND processamento_token = %(token)s;
                """,
                {
                    "transcricao": dados["transcricao_video"],
                    "gancho": dados["gancho_3s"],
                    "nicho": dados["nicho"],
                    "sub_nicho": dados.get("sub_nicho"),
                    "gatilho": dados["gatilho_mental"],
                    "preco": preco,
                    "moeda": moeda,
                    "margem": dados.get("margem_estimada_pct"),
                    "objecoes": json.dumps(dados.get("objecoes_quebradas", [])),
                    "angulos": json.dumps(dados.get("angulos_alternativos", [])),
                    "embedding": embedding_str,
                    "embedding_texto_hash": _hash_texto_embedding(_texto_embedding(dados)),
                    "tokens_prompt": uso_tokens.get("prompt"),
                    "tokens_saida": uso_tokens.get("saida"),
                    "token": token,
                    "id": oferta_id,
                    "bonus": BONUS_ANALISE_IA,
                    "teto": TETO_SCORE,
                },
            )
            persisted = cur.rowcount == 1
            irmas = []
            if persisted:
                cur.execute(
                    SQL_PROPAGAR_IRMAS,
                    {"id": oferta_id, "bonus": BONUS_ANALISE_IA, "teto": TETO_SCORE},
                )
                irmas = cur.fetchall()
            conn.commit()
    for irma in irmas:
        with get_pool().connection() as conn:
            qualificar_pagina(conn, irma["id"], irma.get("url_destino"))
    if irmas:
        logger.info("Oferta %s propagou o criativo para %s irmãs.", oferta_id, len(irmas))
    return persisted


def _hosts_r2_permitidos() -> set[str]:
    hosts = set()
    public_url = ler_segredo("CLOUDFLARE_R2_PUBLIC_URL").strip()
    if public_url:
        hostname = urlsplit(public_url).hostname
        if hostname:
            hosts.add(hostname.lower().rstrip("."))
    return hosts


def _host_r2_permitido(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    if host == R2_HOST_SUFFIX or host.endswith(f".{R2_HOST_SUFFIX}"):
        return True
    return host in _hosts_r2_permitidos()


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


def _validar_url_video_r2(video_url: str) -> None:
    try:
        parsed = urlsplit(video_url)
    except ValueError as exc:
        raise ValueError("URL de vídeo inválida.") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("A URL do vídeo precisa usar HTTPS e um host permitido.")
    if not _host_r2_permitido(parsed.hostname):
        raise ValueError("A URL do vídeo não pertence a um host permitido.")
    _rejeitar_ip_nao_publico(parsed.hostname)


def _abrir_stream_video_r2(video_url: str):
    current_url = video_url
    for _ in range(MAX_VIDEO_REDIRECTS + 1):
        _validar_url_video_r2(current_url)
        response = requests.get(
            current_url,
            stream=True,
            timeout=30,
            allow_redirects=False,
        )
        if response.is_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("Redirecionamento de vídeo sem cabeçalho Location.")
            current_url = urljoin(current_url, location)
            continue
        try:
            response.raise_for_status()
        except requests.RequestException:
            response.close()
            raise
        return response
    raise ValueError("O vídeo excedeu o limite de redirecionamentos permitidos.")


def _baixar_video_temporario(video_url_r2: str) -> str:
    """Download em tmpfs com proteção de cota e limpeza em caso de falha[cite: 1]."""
    url = url_leitura(video_url_r2)
    if not url:
        raise ValueError("Não foi possível resolver a URL do vídeo.")
    bytes_baixados = 0
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name
            with _abrir_stream_video_r2(url) as response:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    bytes_baixados += len(chunk)
                    if bytes_baixados > MAX_VIDEO_BYTES:
                        raise ValueError(
                            f"Vídeo excedeu o limite máximo seguro de {MAX_VIDEO_BYTES // (1024 * 1024)} MB."
                        )
                    tmp.write(chunk)
        return tmp_path
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError as cleanup_error:
                logger.warning(
                    "Falha ao remover download temporário incompleto %s: %s",
                    tmp_path,
                    cleanup_error,
                )
        raise


def _estado_arquivo(video_file) -> str:
    state = video_file.state
    name = getattr(state, "name", None)
    if isinstance(name, str) and name:
        return name
    return str(state)


def _aguardar_upload_ativo(video_file, timeout_segundos: int = 90):
    """Guardrail contra travamento indefinido na File API (Timeout de 90s)[cite: 1]."""
    tempo_inicio = time.time()
    while _estado_arquivo(video_file) == "PROCESSING":
        if time.time() - tempo_inicio > timeout_segundos:
            raise TimeoutError(f"A File API excedeu o limite de {timeout_segundos}s.")
        time.sleep(2.5)
        video_file = cliente_ia.files.get(name=video_file.name)

    estado = _estado_arquivo(video_file)
    if estado != "ACTIVE":
        raise ValueError(f"Falha no processamento da mídia: estado '{estado}'.")
    return video_file


def _enquadrar_nicho(dados: dict) -> dict:
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


def _texto_embedding(dados: dict) -> str:
    transcricao = str(dados["transcricao_video"])[:500]
    nicho = str(dados["nicho"])
    sub = str(dados.get("sub_nicho") or "").strip()
    if sub:
        nicho = f"{nicho}: {sub}"
    return f"{nicho} | {dados['gatilho_mental']} | {dados['gancho_3s']} | {transcricao}"


def _hash_texto_embedding(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _vetor_de_texto(texto: str) -> list[float] | None:
    bruto = texto.strip()
    if bruto.startswith("[") and bruto.endswith("]"):
        bruto = bruto[1:-1]
    if not bruto.strip():
        return None
    try:
        vetor = [float(parte) for parte in bruto.split(",")]
    except ValueError:
        return None
    if len(vetor) != DIMENSAO_EMBEDDING or not all(math.isfinite(valor) for valor in vetor):
        return None
    return vetor


def _embedding_em_cache(texto_hash: str, oferta_id: int) -> list[float] | None:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT embedding_analise::text AS embedding
                FROM ofertas
                WHERE embedding_texto_hash = %s
                  AND embedding_analise IS NOT NULL
                  AND id <> %s
                LIMIT 1
                """,
                (texto_hash, oferta_id),
            )
            row = cur.fetchone()
            conn.commit()
    if not row or not row.get("embedding"):
        return None
    return _vetor_de_texto(row["embedding"])


def _obter_vetor_embedding(oferta_id: int, dados: dict) -> list[float]:
    texto_hash = _hash_texto_embedding(_texto_embedding(dados))
    vetor = _embedding_em_cache(texto_hash, oferta_id)
    if vetor is not None:
        logger.info("[Oferta %s] Embedding reutilizado pelo hash do texto.", oferta_id)
        return vetor

    embedding_res = cliente_ia.models.embed_content(
        model=MODELO_EMBEDDING,
        contents=_texto_embedding(dados),
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=DIMENSAO_EMBEDDING,
        ),
    )
    return _vetor_embedding(embedding_res)


def _vetor_embedding(resposta) -> list[float]:
    embeddings = getattr(resposta, "embeddings", None)
    if embeddings is None and isinstance(resposta, dict):
        if "embedding" in resposta:
            return list(resposta["embedding"])
        embeddings = resposta.get("embeddings")
    if not embeddings:
        raise ValueError("A resposta de embedding não contém um vetor.")
    primeiro = embeddings[0]
    valores = getattr(primeiro, "values", None)
    if valores is None and isinstance(primeiro, dict):
        valores = primeiro.get("values")
    if not valores:
        raise ValueError("A resposta de embedding não contém um vetor.")
    vetor = list(valores)
    if len(vetor) != DIMENSAO_EMBEDDING:
        raise ValueError(f"Embedding com {len(vetor)} dimensões; a coluna espera {DIMENSAO_EMBEDDING}.")
    return vetor


def _erro_permanente(erro: Exception) -> bool:
    if isinstance(erro, (ValueError, json.JSONDecodeError)):
        return True
    codigo = getattr(erro, "code", None)
    return isinstance(erro, ClientError) and isinstance(codigo, int) and codigo != 429


def _erro_configuracao(erro: Exception) -> bool:
    """Chave inválida, permissão negada ou modelo inexistente: não é culpa da oferta."""
    if not isinstance(erro, ClientError):
        return False
    if erro.code in (401, 403, 404):
        return True
    texto = f"{getattr(erro, 'status', '') or ''} {getattr(erro, 'message', '') or ''}".upper()
    return erro.code == 400 and ("API_KEY" in texto or "API KEY" in texto)


def _tratar_falha_processamento(
    task,
    oferta_id: int,
    lease_token: str,
    erro: Exception,
    analise: dict | None = None,
    uso_tokens: dict | None = None,
) -> dict:
    configuracao = _erro_configuracao(erro)
    definitiva = not configuracao and (_erro_permanente(erro) or task.request.retries >= task.max_retries)
    try:
        _liberar_reserva_erro(oferta_id, lease_token, definitiva)
    except Exception as cleanup_error:
        logger.exception(
            "[Oferta %s] Falha ao liberar reserva após erro: %s",
            oferta_id,
            cleanup_error,
        )
    if configuracao:
        logger.critical(
            "[Oferta %s] Erro de configuração do Gemini; oferta não penalizada: %s",
            oferta_id,
            erro,
        )
        return {"status": "config_error", "oferta_id": oferta_id}
    if definitiva:
        logger.error("[Oferta %s] Falha permanente (%s): %s", oferta_id, type(erro).__name__, erro)
        return {"status": "dead_letter", "oferta_id": oferta_id}

    logger.error("[Oferta %s] Erro: %s. Executando retry...", oferta_id, erro)
    retry_kwargs = {"kwargs": {"analise": analise, "uso_tokens": uso_tokens}} if analise else {}
    raise task.retry(exc=erro, countdown=15 * (2**task.request.retries), **retry_kwargs) from erro


def _parte_video(video_file):
    mime = getattr(video_file, "mime_type", None) or "video/mp4"
    return types.Part(
        file_data=types.FileData(file_uri=video_file.uri, mime_type=mime),
        video_metadata=types.VideoMetadata(start_offset="0s", end_offset=JANELA_VIDEO_FIM),
    )


def _config_geracao():
    return types.GenerateContentConfig(
        temperature=0.2,
        response_mime_type="application/json",
        response_schema=AnaliseAnuncioSchema,
        system_instruction=instrucao_sistema,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )


def _registrar_uso_tokens(oferta_id: int, resposta) -> dict:
    uso = getattr(resposta, "usage_metadata", None)
    prompt_tokens = getattr(uso, "prompt_token_count", None) if uso else None
    candidatos = getattr(uso, "candidates_token_count", None) if uso else None
    total_tokens = getattr(uso, "total_token_count", None) if uso else None
    logger.info(
        "[Oferta %s] Consumo Gemini: prompt=%s, completion=%s, total=%s tokens.",
        oferta_id,
        prompt_tokens,
        candidatos,
        total_tokens,
    )
    return {
        "prompt": prompt_tokens if isinstance(prompt_tokens, int) else None,
        "saida": candidatos if isinstance(candidatos, int) else None,
    }


def _limitar_copy_prompt(ad_copy_texto: str) -> str:
    if len(ad_copy_texto) <= LIMITE_COPY_PROMPT:
        return ad_copy_texto
    logger.info(
        "Copy truncada de %s para %s caracteres no prompt.",
        len(ad_copy_texto),
        LIMITE_COPY_PROMPT,
    )
    return ad_copy_texto[:LIMITE_COPY_PROMPT]


def _obter_prompt_final(ad_copy_texto: str) -> str:
    """Carrega template Jinja2 externo ou utiliza fallback estático."""
    ad_copy_texto = _limitar_copy_prompt(ad_copy_texto)
    prompt_path = os.path.join(BASE_DIR, "prompts", "extracao_vsl.jinja")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            template = Template(f.read())
        return template.render(ad_copy_texto=ad_copy_texto)

    return f"""
    Analise só os primeiros 30 segundos deste anúncio.
    1. Extraia o gancho visual inicial e a fala desses 30 segundos.
    2. Leia a copy abaixo para entender a promessa escrita.
    3. Proponha um único ângulo de venda, o mais forte.

    --- COPY ORIGINAL INTERCEPTADA ---
    {ad_copy_texto}
    ----------------------------------

    Responda conforme o schema JSON solicitado.
    """


@app.task(bind=True, max_retries=3, rate_limit="10/m")
def processar_anuncio_multimodal(
    self,
    oferta_id: int,
    video_url_r2: str,
    ad_copy_texto: str,
    analise: dict | None = None,
    uso_tokens: dict | None = None,
):
    """Consome a mídia permanente do R2, processa com Gemini e indexa no pgvector[cite: 1]."""
    reservation_status, lease_remaining, lease_token = _reservar_oferta_para_processamento(oferta_id)
    if reservation_status == "completed":
        logger.info(f"[Oferta {oferta_id}] Tarefa ignorada: oferta já concluída anteriormente.")
        return {"status": "already_processed", "oferta_id": oferta_id}

    if reservation_status == "missing":
        logger.warning("[Oferta %s] Tarefa ignorada: registro não encontrado.", oferta_id)
        return {"status": "not_found", "oferta_id": oferta_id}

    if reservation_status == "processing":
        logger.info(
            "[Oferta %s] Outra execução mantém a reserva; nova tentativa em %s segundos.",
            oferta_id,
            lease_remaining,
        )
        raise self.retry(
            countdown=max(1, lease_remaining),
            max_retries=1000,
        )

    if reservation_status != "reserved" or lease_token is None:
        raise RuntimeError(f"Não foi possível reservar a oferta {oferta_id}: {reservation_status}.")

    video_file = None
    tmp_path = None
    resultado_validado = None

    try:
        if analise:
            resultado_validado = AnaliseAnuncioSchema(**_enquadrar_nicho(analise))
            logger.info("[Oferta %s] Retry reaproveita a análise já paga.", oferta_id)
        else:
            tmp_path = _baixar_video_temporario(video_url_r2)

            logger.info(f"[Oferta {oferta_id}] Upload para Gemini File API...")
            video_file = cliente_ia.files.upload(file=tmp_path)
            video_file = _aguardar_upload_ativo(video_file)

            prompt_final = _obter_prompt_final(ad_copy_texto)
            resposta = cliente_ia.models.generate_content(
                model=MODELO_ANALISE,
                contents=[_parte_video(video_file), prompt_final],
                config=_config_geracao(),
            )
            uso_tokens = _registrar_uso_tokens(oferta_id, resposta)
            dados_extraidos = json.loads(resposta.text)
            resultado_validado = AnaliseAnuncioSchema(**_enquadrar_nicho(dados_extraidos))

        vetor_embedding = _obter_vetor_embedding(oferta_id, resultado_validado.model_dump())

        persisted = persistir_resultado_ia(
            oferta_id,
            resultado_validado.model_dump(),
            vetor_embedding,
            lease_token,
            uso_tokens,
        )
        if not persisted:
            logger.warning(
                "[Oferta %s] Resultado descartado: a reserva pertence a outra execução.",
                oferta_id,
            )
            return {"status": "stale_lease", "oferta_id": oferta_id}
        logger.info(f"[Oferta {oferta_id}] Processamento e persistência concluídos com sucesso.")

        return resultado_validado.model_dump()

    except Exception as e:
        paga = resultado_validado.model_dump() if resultado_validado else None
        return _tratar_falha_processamento(self, oferta_id, lease_token, e, paga, uso_tokens)

    finally:
        # FinOps compulsório: remoção imediata da mídia na nuvem e do arquivo em tmpfs[cite: 1]
        if video_file:
            try:
                cliente_ia.files.delete(name=video_file.name)
            except Exception as cleanup_error:
                logger.warning(
                    "[Oferta %s] Falha ao remover arquivo no Gemini: %s",
                    oferta_id,
                    cleanup_error,
                )
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError as cleanup_error:
                logger.warning(
                    "[Oferta %s] Falha ao remover arquivo temporário %s: %s",
                    oferta_id,
                    tmp_path,
                    cleanup_error,
                )
