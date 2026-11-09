"""Publica fila, pool e bloqueio de egress. O Grafana fica de fora do Compose."""

import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)

FILAS = ("qualification_queue", "ai_multimodal_queue", "ai_dead_letter", "lifecycle_queue")
_texto = ""
_trava = threading.Lock()


def _filas(linhas: list[str]) -> None:
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return
    import redis

    cliente = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    for fila in FILAS:
        linhas.append(f'celery_fila_profundidade{{fila="{fila}"}} {int(cliente.llen(fila))}')
    membros = cliente.zrange("egress:janela", 0, -1)
    if not membros:
        linhas.append("egress_bloqueio_ratio 0")
        return
    bloqueios = sum(1 for item in membros if b":1:" in item)
    linhas.append(f"egress_bloqueio_ratio {bloqueios / len(membros):.4f}")


def _pgbouncer(linhas: list[str]) -> None:
    url = os.environ.get("POSTGRES_URL", "").strip()
    if not url:
        return
    import psycopg

    with psycopg.connect(url, connect_timeout=2, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW POOLS")
            nomes = [item.name for item in cur.description]
            linhas.extend(_pool(nomes, cur.fetchall()))


def _pool(nomes: list[str], linhas_sql: list) -> list[str]:
    clientes = _soma(nomes, linhas_sql, "cl_active")
    servidores = _soma(nomes, linhas_sql, "sv_used")
    return [
        f"pgbouncer_clientes_ativos {clientes}",
        f"pgbouncer_servidores_usados {servidores}",
    ]


def _soma(nomes: list[str], linhas_sql: list, coluna: str) -> int:
    if coluna not in nomes:
        return 0
    indice = nomes.index(coluna)
    return sum(int(linha[indice] or 0) for linha in linhas_sql)


def coletar() -> str:
    linhas: list[str] = []
    try:
        _filas(linhas)
    except Exception:
        logger.info("Redis indisponível para as métricas.")
    try:
        _pgbouncer(linhas)
    except Exception:
        logger.info("PgBouncer indisponível para as métricas.")
    return "\n".join(linhas) + "\n"


def _ciclo() -> None:
    global _texto
    while True:
        with _trava:
            _texto = coletar()
        time.sleep(15)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        with _trava:
            corpo = _texto.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, formato: str, *args: object) -> None:
        return


def main() -> None:
    threading.Thread(target=_ciclo, name="metricas", daemon=True).start()
    # O Compose publica só em 127.0.0.1. Dentro da rede dados o processo escuta a ponte.
    ThreadingHTTPServer(("0.0.0.0", 9108), _Handler).serve_forever()  # nosec B104


if __name__ == "__main__":
    main()
