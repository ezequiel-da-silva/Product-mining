import json
import logging
import os
import socket
import sys
import time
import types
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def _compat_pkg_resources() -> None:
    """O setuptools 83 removeu pkg_resources, ainda importado pelo playwright-stealth 1.0.6."""
    if "pkg_resources" in sys.modules:
        return

    modulo = types.ModuleType("pkg_resources")

    def resource_string(package: str, resource: str) -> bytes:
        import importlib

        base = Path(importlib.import_module(package).__file__).parent
        return (base / resource).read_bytes()

    modulo.resource_string = resource_string
    sys.modules["pkg_resources"] = modulo


_compat_pkg_resources()
from playwright_stealth import stealth_sync  # noqa: E402

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.celery_app import app  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)


class FilaIndisponivel(Exception):
    """O broker recusou o anúncio; a varredura precisa parar."""


CDP_HOSTS_PERMITIDOS = {"host.docker.internal"}
PREFIXO_BIBLIOTECA = "https://www.facebook.com/ads/library"
LIMITE_CORPO_GRAPHQL = 2_000_000
_erro_fila: FilaIndisponivel | None = None
_ouvinte_graphql = False


def url_cdp_permitida(host: str, port: str) -> str:
    if host not in CDP_HOSTS_PERMITIDOS:
        raise ValueError("CDP_HOST fora da lista permitida.")
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        raise ValueError("CDP_PORT inválida.")
    return f"http://{host}:{int(port)}"


def url_cdp_conectavel(cdp_url: str) -> str:
    """O Chrome recusa o cabeçalho Host quando ele não é um IP nem localhost."""
    parsed = urlsplit(cdp_url)
    endereco = socket.gethostbyname(parsed.hostname or "")
    return f"{parsed.scheme}://{endereco}:{parsed.port}"


def despachar_anuncio_capturado(dados_brutos: dict):
    """Enfileira anúncio extraído na fila de qualificação determinística[cite: 1]."""
    from src.sessao import sessao_permite_publicar

    if not sessao_permite_publicar():
        logger.info("Publicação pausada: a sessão da Biblioteca não está ok.")
        return True
    try:
        app.send_task(
            "tasks.qualification.avaliar_oferta",
            args=[dados_brutos],
            queue="qualification_queue",
        )
        return True
    except Exception as e:
        logger.exception("Falha ao enfileirar anúncio no Redis/Celery: %s", e)
        return False


def _extrair_anuncios(payload: dict) -> list[dict]:
    anuncios = {}

    def visitar(value, em_no_de_edge=False):
        if isinstance(value, list):
            for item in value:
                visitar(item, em_no_de_edge)
        elif isinstance(value, dict):
            ad_id = value.get("ad_archive_id") or value.get("collateral_id")
            if not ad_id and em_no_de_edge:
                ad_id = value.get("id")
            if ad_id:
                anuncios[str(ad_id)] = value
                return
            for key, child in value.items():
                visitar(child, em_no_de_edge=key in {"edges", "node"})

    visitar(payload.get("data", {}))
    return list(anuncios.values())


def _corpo_graphql(response) -> bytes | None:
    corpo = response.body()
    if isinstance(corpo, str):
        corpo = corpo.encode("utf-8")
    if not isinstance(corpo, (bytes, bytearray)):
        return None
    if len(corpo) > LIMITE_CORPO_GRAPHQL:
        logger.info("Resposta GraphQL descartada: corpo acima de %s bytes.", LIMITE_CORPO_GRAPHQL)
        return None
    return bytes(corpo)


def interceptar_graphql(response):
    """Filtra requisições GraphQL da Ad Library e processa criativos[cite: 1]."""
    global _erro_fila
    if "/api/graphql/" in response.url and response.status == 200:
        try:
            corpo = _corpo_graphql(response)
            if corpo is None:
                return
            payload = json.loads(corpo)
            if not isinstance(payload, dict):
                return

            anuncios = _extrair_anuncios(payload)
            despachar_lote(anuncios)
        except FilaIndisponivel as exc:
            _erro_fila = exc
            raise
        except Exception as e:
            logger.exception("Falha ao interpretar resposta GraphQL: %s", e)


def despachar_lote(anuncios: list[dict]) -> None:
    enviados = 0
    for item in anuncios:
        if not despachar_anuncio_capturado(item):
            raise FilaIndisponivel("O Redis recusou um anúncio; varredura interrompida.")
        enviados += 1
    if enviados:
        logger.info("%s anúncios extraídos e enviados para a fila Celery.", enviados)


def _interromper_se_fila_recusou() -> None:
    if _erro_fila is not None:
        raise _erro_fila


def rotina_extracao(page):
    """Aplica bypass estrutural e executa rolagem humana controlada[cite: 1]."""
    global _erro_fila, _ouvinte_graphql
    _erro_fila = None
    stealth_sync(page)
    logger.info("Modo Stealth ativado.")

    page.remove_listener("response", interceptar_graphql)
    page.on("response", interceptar_graphql)
    _ouvinte_graphql = True

    logger.info("Iniciando varredura contínua...")
    for i in range(10):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        _interromper_se_fila_recusou()
        logger.info("Scroll disparado (Ciclo %s)...", i + 1)
        page.wait_for_timeout(3500)
        _interromper_se_fila_recusou()


def _varrer_com_pausa_de_fila(page) -> None:
    """Repete a varredura na mesma página quando o Redis recusa escrita."""
    while True:
        try:
            rotina_extracao(page)
            return
        except FilaIndisponivel as exc:
            logger.error("Redis recusou a escrita: %s. Nova varredura em 60s.", exc)
            time.sleep(60)


def _escolher_pagina_cdp(context):
    """Usa a única aba da Biblioteca. Não fecha a aba: quem abre é o minerar.ps1."""
    paginas = list(context.pages)
    alvo = os.environ.get("CDP_TARGET_URL", "").strip()
    if alvo:
        escolhidas = [pagina for pagina in paginas if alvo in (getattr(pagina, "url", "") or "")]
        if len(escolhidas) != 1:
            raise RuntimeError(
                "CDP_TARGET_URL exige exatamente uma aba que contenha o alvo. " f"Encontradas: {len(escolhidas)}."
            )
        return escolhidas[0]
    biblioteca = [pagina for pagina in paginas if (getattr(pagina, "url", "") or "").startswith(PREFIXO_BIBLIOTECA)]
    if len(biblioteca) == 1:
        return biblioteca[0]
    raise RuntimeError("Deixe uma única aba em https://www.facebook.com/ads/library ou defina CDP_TARGET_URL.")


def _soltar_ouvinte(page) -> None:
    global _ouvinte_graphql
    if page is None or not _ouvinte_graphql:
        return
    try:
        page.remove_listener("response", interceptar_graphql)
    except Exception:
        logger.exception("Falha ao remover o ouvinte GraphQL.")
    _ouvinte_graphql = False


def _conectar_e_varrer(cdp_url: str) -> None:
    with sync_playwright() as p:
        page = None
        try:
            destino = url_cdp_conectavel(cdp_url)
            browser = p.chromium.connect_over_cdp(destino, timeout=30000)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            from src.sessao import classificar_sessao, gravar_estado

            urls = [getattr(pagina, "url", "") or "" for pagina in context.pages]
            estado = classificar_sessao(urls)
            gravar_estado(estado)
            if estado != "ok":
                logger.warning("Sessão %s. A publicação fica pausada até a aba da Biblioteca voltar.", estado)
                time.sleep(60)
                return
            page = _escolher_pagina_cdp(context)
            _varrer_com_pausa_de_fila(page)
        finally:
            _soltar_ouvinte(page)


def executar_sessao_cdp(cdp_url: str):
    """Mantém a conexão CDP resiliente com reconexão exponencial[cite: 1]."""
    tentativas = 0
    max_tentativas = 10

    while tentativas < max_tentativas:
        try:
            destino = url_cdp_conectavel(cdp_url)
            logger.info(f"Tentando conectar ao CDP em {destino} (Tentativa {tentativas + 1}/{max_tentativas})...")
            _conectar_e_varrer(cdp_url)
            tentativas = 0

        except Exception as e:
            tentativas += 1
            intervalo = min(60, 5 * (2**tentativas))
            logger.error(
                f"Falha de sessão CDP: {e}. Próxima tentativa em {intervalo}s (Falha {tentativas}/{max_tentativas})..."
            )
            time.sleep(intervalo)

    logger.critical("Limite máximo de tentativas de conexão CDP excedido. Encerrando Producer.")


if __name__ == "__main__":
    cdp_host = os.environ.get("CDP_HOST", "host.docker.internal")
    cdp_port = os.environ.get("CDP_PORT", "9222")
    executar_sessao_cdp(url_cdp_permitida(cdp_host, cdp_port))
