import json
import os
import socket
import sys
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry
from google.genai.errors import ClientError

# Adiciona a raiz ao path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _passthrough_task(*decorator_args, **_decorator_kwargs):
    def decorator(fun):
        return fun

    if decorator_args and callable(decorator_args[0]):
        return decorator_args[0]
    return decorator


_celery_app = MagicMock()
_celery_app.task.side_effect = _passthrough_task

# Mock do Celery, BD e Gemini antes da importação para evitar erros de ambiente
with (
    patch("core.celery_app.app", _celery_app),
    patch("psycopg_pool.ConnectionPool"),
    patch("google.genai.Client"),
):
    from src.worker_ia import (
        _baixar_video_temporario,
        _hash_texto_embedding,
        _texto_embedding,
        persistir_resultado_ia,
    )

VIDEO_R2_URL = "https://account.r2.cloudflarestorage.com/video.mp4"


def _dns(ip: str):
    def resolve(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]

    return resolve


def _dados_analise():
    return {
        "transcricao_video": "Texto transcrito",
        "gancho_3s": "Abertura rápida",
        "nicho": "Beleza",
        "gatilho_mental": "Escassez",
        "objecoes_quebradas": ["Caro"],
        "angulos_alternativos": [],
    }


def _pool(monkeypatch, cursor):
    import src.worker_ia as worker

    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = connection
    monkeypatch.setattr(worker, "pool_db", pool)
    return connection


@patch("src.worker_ia.pool_db")
def test_persistir_resultado_ia_mock(mock_pool_db):
    """Simula o pool do PostgreSQL e valida a execução da query de update."""
    # Configura mocks para connection() e cursor()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()

    # Simula o comportamento dos context managers (with...)
    mock_pool_db.connection.return_value.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    dados_mock = {
        "transcricao_video": "Texto transcrito",
        "gancho_3s": "Abertura rápida",
        "nicho": "Beleza",
        "gatilho_mental": "Escassez",
        "preco_oferta_estimado": 99.90,
        "margem_estimada_pct": 60,
        "objecoes_quebradas": ["Caro"],
        "angulos_alternativos": [],
    }
    embedding_mock = [0.15, -0.02, 0.88]
    mock_cursor.rowcount = 1

    # Executa a função
    persisted = persistir_resultado_ia(
        oferta_id=42,
        dados=dados_mock,
        embedding=embedding_mock,
        token="token-1",
    )
    assert persisted is True

    # Asserções principais: garante que execute() e commit() foram chamados
    assert mock_cursor.execute.called, "O cursor.execute não foi chamado."
    assert mock_conn.commit.called, "O conn.commit não foi chamado."

    # Valida se os argumentos passados para a query estão corretos
    args_chamada = mock_cursor.execute.call_args[0]
    parametros_sql = args_chamada[1]

    assert parametros_sql["id"] == 42
    assert parametros_sql["preco"] == 99.90
    assert parametros_sql["embedding"] == "[0.15,-0.02,0.88]"
    assert parametros_sql["embedding_texto_hash"] == _hash_texto_embedding(_texto_embedding(dados_mock))
    assert parametros_sql["token"] == "token-1"
    assert "processamento_token = %(token)s" in args_chamada[0]


@patch("src.worker_ia.requests.get")
def test_baixar_video_limite_excedido_remove_arquivo_temporario(mock_get, tmp_path, monkeypatch):
    import src.worker_ia as worker_ia

    monkeypatch.setattr(worker_ia.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(worker_ia, "MAX_VIDEO_BYTES", 2)
    monkeypatch.setattr(worker_ia.socket, "getaddrinfo", _dns("1.1.1.1"))
    response = MagicMock()
    response.is_redirect = False
    response.__enter__.return_value = response
    response.iter_content.return_value = [b"video"]
    mock_get.return_value = response

    with pytest.raises(ValueError, match="limite máximo seguro"):
        _baixar_video_temporario(VIDEO_R2_URL)

    assert list(tmp_path.iterdir()) == []
    assert mock_get.call_args.kwargs["allow_redirects"] is False


def test_pool_nao_e_criado_na_importacao():
    import src.worker_ia as worker

    assert worker.pool_db is None


def test_reserva_grava_token_exclusivo(monkeypatch):
    import src.worker_ia as worker

    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 7}
    _pool(monkeypatch, cursor)

    status, remaining, token = worker._reservar_oferta_para_processamento(7)

    query, params = cursor.execute.call_args.args
    assert status == "reserved"
    assert remaining == 0
    assert token in params
    assert "processamento_token = %s" in query


def test_liberacao_exige_o_token_da_execucao(monkeypatch):
    import src.worker_ia as worker

    cursor = MagicMock()
    _pool(monkeypatch, cursor)

    worker._liberar_reserva_erro(7, "token-1")

    query, params = cursor.execute.call_args.args
    assert "processamento_token = %s" in query
    assert params == (7, "token-1")


def test_persistencia_descarta_lease_obsoleto(monkeypatch):
    import src.worker_ia as worker

    cursor = MagicMock()
    cursor.rowcount = 0
    _pool(monkeypatch, cursor)

    persisted = worker.persistir_resultado_ia(7, _dados_analise(), [0.1], "token-antigo")

    assert persisted is False


def test_processamento_descarta_resultado_de_lease_obsoleto(monkeypatch):
    import src.worker_ia as worker

    monkeypatch.setattr(
        worker,
        "_reservar_oferta_para_processamento",
        lambda _oferta_id: ("reserved", 0, "token-1"),
    )
    monkeypatch.setattr(worker, "_baixar_video_temporario", lambda _url: "/tmp/video-inexistente.mp4")
    upload = MagicMock()
    upload.name = "files/video"
    upload.uri = "https://generativelanguage.googleapis.com/v1beta/files/video"
    upload.mime_type = "video/mp4"
    monkeypatch.setattr(worker.cliente_ia.files, "upload", lambda **_kwargs: upload)
    monkeypatch.setattr(worker, "_aguardar_upload_ativo", lambda video: video)
    resposta = MagicMock()
    resposta.text = json.dumps(_dados_analise())
    monkeypatch.setattr(worker.cliente_ia.models, "generate_content", lambda **_kwargs: resposta)
    embedding = MagicMock()
    embedding.embeddings = [MagicMock(values=[0.1] * 768)]
    monkeypatch.setattr(worker, "_embedding_em_cache", lambda *_args: None)
    monkeypatch.setattr(worker.cliente_ia.models, "embed_content", lambda **_kwargs: embedding)
    monkeypatch.setattr(worker.cliente_ia.files, "delete", lambda **_kwargs: None)
    persist = MagicMock(return_value=False)
    monkeypatch.setattr(worker, "persistir_resultado_ia", persist)

    result = worker.processar_anuncio_multimodal(MagicMock(), 7, VIDEO_R2_URL, "copy")

    assert result == {"status": "stale_lease", "oferta_id": 7}
    assert persist.call_args.args[-1] == "token-1"


def test_embedding_com_tamanho_errado_vai_para_dlq(monkeypatch):
    import src.worker_ia as worker

    monkeypatch.setattr(
        worker,
        "_reservar_oferta_para_processamento",
        lambda _oferta_id: ("reserved", 0, "token-1"),
    )
    monkeypatch.setattr(worker, "_baixar_video_temporario", lambda _url: "/tmp/video-inexistente.mp4")
    upload = MagicMock()
    upload.name = "files/video"
    upload.uri = "https://generativelanguage.googleapis.com/v1beta/files/video"
    upload.mime_type = "video/mp4"
    monkeypatch.setattr(worker.cliente_ia.files, "upload", lambda **_kwargs: upload)
    monkeypatch.setattr(worker, "_aguardar_upload_ativo", lambda video: video)
    resposta = MagicMock()
    resposta.text = json.dumps(_dados_analise())
    monkeypatch.setattr(worker.cliente_ia.models, "generate_content", lambda **_kwargs: resposta)
    embedding = MagicMock()
    embedding.embeddings = [MagicMock(values=[0.1, 0.2])]
    monkeypatch.setattr(worker, "_embedding_em_cache", lambda *_args: None)
    monkeypatch.setattr(worker.cliente_ia.models, "embed_content", lambda **_kwargs: embedding)
    monkeypatch.setattr(worker.cliente_ia.files, "delete", lambda **_kwargs: None)
    monkeypatch.setattr(worker, "_liberar_reserva_erro", MagicMock())
    send_task = MagicMock()
    monkeypatch.setattr(worker.app, "send_task", send_task)
    task_self = MagicMock()
    task_self.request.retries = 0
    task_self.max_retries = 3

    result = worker.processar_anuncio_multimodal(task_self, 7, VIDEO_R2_URL, "copy")

    assert result == {"status": "dead_letter", "oferta_id": 7}
    task_self.retry.assert_not_called()
    send_task.assert_not_called()


def test_parte_video_recorta_os_primeiros_30_segundos():
    import src.worker_ia as worker

    arquivo = MagicMock()
    arquivo.uri = "https://generativelanguage.googleapis.com/v1beta/files/abc"
    arquivo.mime_type = ""

    parte = worker._parte_video(arquivo)

    assert parte.video_metadata.start_offset == "0s"
    assert parte.video_metadata.end_offset == "30s"
    assert parte.file_data.file_uri == arquivo.uri
    assert parte.file_data.mime_type == "video/mp4"


def test_config_geracao_desliga_thinking():
    import src.worker_ia as worker

    config = worker._config_geracao()

    assert config.thinking_config.thinking_budget == 0


def test_registrar_uso_tokens_le_os_contadores(caplog):
    import logging

    import src.worker_ia as worker

    resposta = MagicMock()
    resposta.usage_metadata.prompt_token_count = 120
    resposta.usage_metadata.candidates_token_count = 40

    with caplog.at_level(logging.INFO):
        worker._registrar_uso_tokens(7, resposta)

    assert "Tokens prompt=120 candidatos=40" in caplog.text


def test_erro_libera_somente_o_token_da_execucao(monkeypatch):
    import src.worker_ia as worker

    monkeypatch.setattr(
        worker,
        "_reservar_oferta_para_processamento",
        lambda _oferta_id: ("reserved", 0, "token-1"),
    )

    def _falha(_url):
        raise RuntimeError("falha no download")

    monkeypatch.setattr(worker, "_baixar_video_temporario", _falha)
    liberar = MagicMock()
    monkeypatch.setattr(worker, "_liberar_reserva_erro", liberar)
    task_self = MagicMock()
    task_self.request.retries = 0
    task_self.max_retries = 3
    task_self.retry.side_effect = Retry("retry")

    with pytest.raises(Retry):
        worker.processar_anuncio_multimodal(task_self, 7, VIDEO_R2_URL, "copy")

    liberar.assert_called_once_with(7, "token-1")
    task_self.retry.assert_called_once()


def test_value_error_vai_para_dlq_sem_retry(monkeypatch):
    import src.worker_ia as worker

    monkeypatch.setattr(
        worker,
        "_reservar_oferta_para_processamento",
        lambda _oferta_id: ("reserved", 0, "token-1"),
    )

    def _falha(_url):
        raise ValueError("vídeo inválido")

    monkeypatch.setattr(worker, "_baixar_video_temporario", _falha)
    monkeypatch.setattr(worker, "_liberar_reserva_erro", MagicMock())
    send_task = MagicMock()
    monkeypatch.setattr(worker.app, "send_task", send_task)
    task_self = MagicMock()
    task_self.request.retries = 0
    task_self.max_retries = 3

    result = worker.processar_anuncio_multimodal(task_self, 7, VIDEO_R2_URL, "copy")

    assert result == {"status": "dead_letter", "oferta_id": 7}
    task_self.retry.assert_not_called()
    send_task.assert_not_called()


def test_client_error_400_vai_para_dlq_sem_retry(monkeypatch):
    import src.worker_ia as worker

    monkeypatch.setattr(
        worker,
        "_reservar_oferta_para_processamento",
        lambda _oferta_id: ("reserved", 0, "token-1"),
    )

    def _falha(_url):
        raise ClientError(400, {"error": {"message": "pedido invalido"}})

    monkeypatch.setattr(worker, "_baixar_video_temporario", _falha)
    monkeypatch.setattr(worker, "_liberar_reserva_erro", MagicMock())
    send_task = MagicMock()
    monkeypatch.setattr(worker.app, "send_task", send_task)
    task_self = MagicMock()
    task_self.request.retries = 0
    task_self.max_retries = 3

    result = worker.processar_anuncio_multimodal(task_self, 7, VIDEO_R2_URL, "copy")

    assert result == {"status": "dead_letter", "oferta_id": 7}
    task_self.retry.assert_not_called()
    send_task.assert_not_called()


def test_client_error_429_faz_retry(monkeypatch):
    import src.worker_ia as worker

    monkeypatch.setattr(
        worker,
        "_reservar_oferta_para_processamento",
        lambda _oferta_id: ("reserved", 0, "token-1"),
    )

    def _falha(_url):
        raise ClientError(429, {"error": {"message": "quota"}})

    monkeypatch.setattr(worker, "_baixar_video_temporario", _falha)
    monkeypatch.setattr(worker, "_liberar_reserva_erro", MagicMock())
    task_self = MagicMock()
    task_self.request.retries = 0
    task_self.max_retries = 3
    task_self.retry.side_effect = Retry("retry")

    with pytest.raises(Retry):
        worker.processar_anuncio_multimodal(task_self, 7, VIDEO_R2_URL, "copy")

    task_self.retry.assert_called_once()


def test_ultima_tentativa_transitoria_vai_para_dlq(monkeypatch):
    import src.worker_ia as worker

    monkeypatch.setattr(
        worker,
        "_reservar_oferta_para_processamento",
        lambda _oferta_id: ("reserved", 0, "token-1"),
    )

    def _falha(_url):
        raise RuntimeError("falha no download")

    monkeypatch.setattr(worker, "_baixar_video_temporario", _falha)
    monkeypatch.setattr(worker, "_liberar_reserva_erro", MagicMock())
    send_task = MagicMock()
    monkeypatch.setattr(worker.app, "send_task", send_task)
    task_self = MagicMock()
    task_self.request.retries = 3
    task_self.max_retries = 3

    result = worker.processar_anuncio_multimodal(task_self, 7, VIDEO_R2_URL, "copy")

    assert result == {"status": "dead_letter", "oferta_id": 7}
    task_self.retry.assert_not_called()
    send_task.assert_not_called()


def test_download_rejeita_http():
    with patch("src.worker_ia.requests.get") as get_video:
        with pytest.raises(ValueError, match="HTTPS"):
            _baixar_video_temporario("http://account.r2.cloudflarestorage.com/video.mp4")

    get_video.assert_not_called()


def test_download_rejeita_ip_privado(monkeypatch):
    import src.worker_ia as worker

    monkeypatch.setattr(worker.socket, "getaddrinfo", _dns("127.0.0.1"))
    with patch("src.worker_ia.requests.get") as get_video:
        with pytest.raises(ValueError, match="não público"):
            _baixar_video_temporario(VIDEO_R2_URL)

    get_video.assert_not_called()


def test_download_nao_segue_redirect_fora_da_allowlist(monkeypatch):
    import src.worker_ia as worker

    monkeypatch.setattr(worker.socket, "getaddrinfo", _dns("1.1.1.1"))
    redirect = MagicMock()
    redirect.is_redirect = True
    redirect.headers = {"Location": "https://evil.example/video.mp4"}
    with patch("src.worker_ia.requests.get", return_value=redirect) as get_video:
        with pytest.raises(ValueError, match="host permitido"):
            _baixar_video_temporario(VIDEO_R2_URL)

    get_video.assert_called_once()
    redirect.close.assert_called_once()


def test_embedding_valido_no_banco_nao_chama_a_api(monkeypatch):
    import src.worker_ia as worker

    cursor = MagicMock()
    cursor.fetchone.return_value = {"embedding": "[" + ",".join(["0.1"] * 768) + "]"}
    _pool(monkeypatch, cursor)
    embed = MagicMock()
    monkeypatch.setattr(worker.cliente_ia.models, "embed_content", embed)

    vetor = worker._obter_vetor_embedding(7, _dados_analise())

    assert vetor == [0.1] * 768
    embed.assert_not_called()


def test_embedding_ausente_ou_invalido_chama_a_api(monkeypatch):
    import src.worker_ia as worker

    cursor = MagicMock()
    cursor.fetchone.side_effect = [None, {"embedding": "[0.1,0.2]"}]
    _pool(monkeypatch, cursor)
    resposta = MagicMock()
    resposta.embeddings = [MagicMock(values=[0.2] * 768)]
    embed = MagicMock(return_value=resposta)
    monkeypatch.setattr(worker.cliente_ia.models, "embed_content", embed)

    primeiro = worker._obter_vetor_embedding(7, _dados_analise())
    segundo = worker._obter_vetor_embedding(7, _dados_analise())

    assert primeiro == segundo == [0.2] * 768
    assert embed.call_count == 2
    assert embed.call_args.kwargs["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert embed.call_args.kwargs["config"].output_dimensionality == 768


def test_copy_curta_permanece_no_prompt():
    import src.worker_ia as worker

    assert "promessa curta" in worker._obter_prompt_final("promessa curta")


def test_copy_longa_e_cortada_no_prompt(caplog):
    import logging

    import src.worker_ia as worker

    with caplog.at_level(logging.INFO):
        prompt = worker._obter_prompt_final("x" * 2001)

    assert "x" * 2000 in prompt
    assert "x" * 2001 not in prompt
    assert "2001" in caplog.text
