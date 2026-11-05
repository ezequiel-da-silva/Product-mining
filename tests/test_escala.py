"""Correções da escala: resolve HOST:PORT, alinhamento do áudio, janela de 30 s e reaper."""

from src.landing import mapa_resolve
from src.operacao import _reivindicar_eventos
from src.visao import argv_cortes, argv_quadro, narracao_parecida, ritmo_dos_cortes
from src.worker_qualification import get_pool as pool_qualificacao


def test_chrome_fixa_o_ip_validado_quando_ha_proxy(monkeypatch):
    import src.landing as landing

    registrados: list[dict] = []

    class FalsoCurl:
        RESOLVE = "RESOLVE"

        @staticmethod
        def get(url, **pedido):
            registrados.append({"url": url, **pedido})
            resposta = type("Resposta", (), {})()
            resposta.is_redirect = False
            resposta.status_code = 200
            resposta.headers = {"Content-Type": "text/html"}
            resposta.iter_content = lambda _tamanho: [b"<p>ok</p>"]
            resposta.close = lambda: None
            return resposta

    monkeypatch.setenv("EGRESS_PROXIES", "http://proxy.example:8080")
    monkeypatch.setattr(landing, "_modulo_curl", lambda: (FalsoCurl, FalsoCurl))
    monkeypatch.setattr(landing, "resolver_publico", lambda host: ["203.0.113.10"] if host == "loja.example" else None)

    assert landing.buscar_html("https://loja.example/p", 1) == "<p>ok</p>"
    assert registrados[0]["proxy"] == "http://proxy.example:8080"
    assert registrados[0]["curl_options"]["RESOLVE"] == ["loja.example:443:203.0.113.10"]
    assert registrados[0]["impersonate"] == "chrome"


def test_mapa_resolve_exige_porta_na_chave():
    assert mapa_resolve("loja.example", "https://loja.example/p", "203.0.113.10") == {
        "loja.example:443": "203.0.113.10"
    }
    assert mapa_resolve("loja.example", "http://loja.example:8080/p", "203.0.113.10") == {
        "loja.example:8080": "203.0.113.10"
    }


def test_narracao_deslocada_em_um_quadro_ainda_casa():
    base = list(range(40))
    deslocada = [999] + base
    assert narracao_parecida(base, base) is True
    assert narracao_parecida(base, deslocada) is True
    assert narracao_parecida(base, list(range(100, 140))) is False
    assert narracao_parecida(base[:10], base[:10]) is False


def test_ritmo_usa_no_maximo_trinta_segundos():
    assert ritmo_dos_cortes(15, 900) == 0.5
    assert ritmo_dos_cortes(6, 10) == 0.6
    assert ritmo_dos_cortes(1, 0) is None
    assert "-t" in argv_cortes("ffmpeg")
    assert argv_cortes("ffmpeg")[argv_cortes("ffmpeg").index("-t") + 1] == "30"
    assert "-ss" in argv_quadro("ffmpeg")
    assert argv_quadro("ffmpeg")[argv_quadro("ffmpeg").index("-ss") + 1] == "0"


def test_reaper_recupera_em_envio_antigo():
    consulta = _reivindicar_eventos.__code__.co_consts
    texto = " ".join(item for item in consulta if isinstance(item, str))
    assert "estado = 'em_envio'" in texto
    assert "INTERVAL '15 minutes'" in texto
    assert "FOR UPDATE SKIP LOCKED" in texto


def test_tubo_nao_trava_quando_a_saida_passa_do_pipe():
    import sys

    from src.visao import _Tubo

    codigo = (
        "import sys\n"
        "while True:\n"
        "    bloco = sys.stdin.buffer.read(1024)\n"
        "    if not bloco:\n"
        "        break\n"
        "    sys.stdout.buffer.write(b'x' * 8192)\n"
        "    sys.stdout.buffer.flush()\n"
    )
    tubo = _Tubo([sys.executable, "-c", codigo])
    for _ in range(40):
        tubo.escrever(b"a" * 1024)
    saida, _erro = tubo.fechar()
    assert len(saida) == 40 * 8192


def test_o_beat_registra_o_envio_e_as_rotinas_novas(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "teste")
    monkeypatch.delenv("MODELO_JUIZ", raising=False)
    import src.lifecycle  # noqa: F401
    import src.worker_ia as worker_ia
    import src.worker_qualification  # noqa: F401
    from core.celery_app import app

    assert app.tasks["tasks.lifecycle.enviar_integracao_evento"].run.__name__ == "enviar_integracao_evento"
    assert app.tasks["tasks.qualification.revisar_fornecedores"].run.__name__ == "revisar_fornecedores"
    assert app.conf.task_routes["src.worker_ia.avaliar_amostra"]["queue"] == "ai_multimodal_queue"
    assert app.conf.beat_schedule["revisar-fornecedores"]["task"] == "tasks.qualification.revisar_fornecedores"
    assert app.conf.beat_schedule["avaliar-amostra-ia"]["task"] == "src.worker_ia.avaliar_amostra"
    assert worker_ia.avaliar_amostra() == {"status": "sem_juiz"}


def test_pool_nao_prepara_statement_no_pgbouncer(monkeypatch):
    chamadas = []

    class FalsoPool:
        def __init__(self, **kwargs):
            chamadas.append(kwargs)

    monkeypatch.setattr("src.worker_qualification.ConnectionPool", FalsoPool)
    monkeypatch.setattr("src.worker_qualification.pool_db", None)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://pipeline_writer:x@pgbouncer:6432/mineracao_fb")
    pool_qualificacao()
    assert chamadas[-1]["kwargs"]["prepare_threshold"] is None

    monkeypatch.setenv("GEMINI_API_KEY", "teste")
    import src.worker_ia as worker_ia

    monkeypatch.setattr(worker_ia, "ConnectionPool", FalsoPool)
    monkeypatch.setattr(worker_ia, "pool_db", None)
    worker_ia.get_pool()
    assert chamadas[-1]["kwargs"]["prepare_threshold"] is None
