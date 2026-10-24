import hashlib
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

with patch("psycopg_pool.ConnectionPool"):
    from src.worker_qualification import (
        _abrir_stream_video,
        _calcular_score,
        _enviar_stream_multipart,
        _extrair_payload,
        _host_video_permitido,
        _pode_despachar_ia,
        _upload_video_r2,
        avaliar_oferta,
    )


def test_pool_nao_e_criado_na_importacao():
    import src.worker_qualification as worker

    assert worker.pool_db is None


def test_extrair_payload_mapeia_snapshot_e_video():
    payload = {
        "ad_archive_id": "ad-123",
        "page_id": "page-456",
        "ad_delivery_start_time": 123456,
        "videos": [{"video_hd_url": "https://video.fbcdn.net/ad.mp4"}],
        "snapshot": {
            "body": {"text": "Texto do anúncio"},
            "cta_type": "SHOP_NOW",
            "cards": [{"link_url": "https://loja.example/produto"}],
        },
    }

    campos = _extrair_payload(payload)

    assert campos["ad_id"] == "ad-123"
    assert campos["url_destino"] == "https://loja.example/produto"
    assert campos["copy_texto"] == "Texto do anúncio"
    assert campos["tipo_cta"] == "SHOP_NOW"
    assert campos["video_url"] == "https://video.fbcdn.net/ad.mp4"


def test_calcular_score_soma_regras_de_negocio():
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    campos = {
        "video_url": "https://video.fbcdn.net/ad.mp4",
        "tipo_cta": "COMPRAR_AGORA",
        "ad_delivery_start_time": now - timedelta(days=6),
        "url_destino": "https://loja.myshopify.com/checkout",
    }

    assert _calcular_score(campos, now=now) == 75


def test_calcular_score_nao_dá_pontos_de_longevidade_no_limite_de_cinco_dias():
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    campos = {
        "video_url": "https://video.fbcdn.net/ad.mp4",
        "tipo_cta": "LEARN_MORE",
        "ad_delivery_start_time": now - timedelta(days=5),
        "url_destino": "https://loja.example/checkout",
    }

    assert _calcular_score(campos, now=now) == 55


def test_host_video_exige_https_e_cdn_meta():
    assert _host_video_permitido("https://scontent.fbcdn.net/video.mp4")
    assert not _host_video_permitido("http://scontent.fbcdn.net/video.mp4")
    assert not _host_video_permitido("https://127.0.0.1/video.mp4")


def test_video_meta_rejeita_ip_privado(monkeypatch):
    import socket

    import src.worker_qualification as worker

    def _dns(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]

    monkeypatch.setattr(worker.socket, "getaddrinfo", _dns)
    with patch("src.worker_qualification.requests.get") as get_video:
        with pytest.raises(ValueError, match="não público"):
            _abrir_stream_video("https://scontent.fbcdn.net/video.mp4")

    get_video.assert_not_called()


def test_video_redirect_para_host_nao_permitido_nao_e_seguido(monkeypatch):
    import socket

    import src.worker_qualification as worker

    redirect = MagicMock()
    redirect.is_redirect = True
    redirect.headers = {"Location": "http://127.0.0.1/private"}

    def _dns(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]

    monkeypatch.setattr(worker.socket, "getaddrinfo", _dns)
    with patch("src.worker_qualification.requests.get", return_value=redirect) as get_video:
        with pytest.raises(ValueError, match="não é permitido"):
            _abrir_stream_video("https://scontent.fbcdn.net/video.mp4")

    get_video.assert_called_once()
    redirect.close.assert_called_once()


def test_upload_r2_sem_credenciais_nao_faz_requisicao(monkeypatch):
    for key in (
        "CLOUDFLARE_R2_ACCOUNT_ID",
        "CLOUDFLARE_R2_ACCESS_KEY",
        "CLOUDFLARE_R2_SECRET_KEY",
        "CLOUDFLARE_R2_BUCKET",
    ):
        monkeypatch.delenv(key, raising=False)

    with patch("src.worker_qualification.requests.get") as get_video:
        assert _upload_video_r2(
            "https://scontent.fbcdn.net/video.mp4",
            "ad-123",
        ) == (None, None)

    get_video.assert_not_called()


def test_upload_stream_multipart_divide_em_partes(monkeypatch):
    import src.worker_qualification as worker

    monkeypatch.setattr(worker, "R2_MULTIPART_PART_BYTES", 4)
    response = MagicMock()
    response.headers = {}
    response.iter_content.return_value = [b"1234", b"567"]
    client = MagicMock()
    client.create_multipart_upload.return_value = {"UploadId": "upload-1"}
    client.upload_part.side_effect = [{"ETag": "etag-1"}, {"ETag": "etag-2"}]

    digest = _enviar_stream_multipart(client, "bucket", "videos/ad.mp4", response)

    assert [call.kwargs["Body"] for call in client.upload_part.call_args_list] == [
        b"1234",
        b"567",
    ]
    client.complete_multipart_upload.assert_called_once()
    client.abort_multipart_upload.assert_not_called()
    assert digest == hashlib.sha256(b"1234567").hexdigest()


def test_persistencia_usa_upsert_e_incrementa_avistamentos(monkeypatch):
    import src.worker_qualification as worker

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {
        "id": 42,
        "score": 55,
        "status_qualificacao": "pendente",
    }
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__enter__.return_value = mock_conn
    monkeypatch.setattr(worker, "pool_db", mock_pool)

    result = worker._persistir_oferta(
        {
            "url_destino": "https://loja.example/produto",
            "page_id": "page-1",
            "copy_texto": "Copy",
            "tipo_cta": "SHOP_NOW",
        },
        55,
        "https://cdn.example/videos/ad-1.mp4",
        "ab" * 32,
    )

    query, parameters = mock_cursor.execute.call_args.args
    assert "ON CONFLICT (url_destino) DO UPDATE" in query
    assert "vezes_avistado = ofertas.vezes_avistado + 1" in query
    assert "COALESCE(EXCLUDED.phash_criativo, ofertas.phash_criativo)" in query
    assert parameters["score"] == 55
    assert parameters["url_video_r2"] == "https://cdn.example/videos/ad-1.mp4"
    assert parameters["phash_criativo"] == "ab" * 32
    assert result["id"] == 42
    mock_conn.commit.assert_called_once()


def test_score_50_com_video_persistido_despacha_para_ia(monkeypatch):
    import src.worker_qualification as worker

    payload = {
        "ad_archive_id": "ad-123",
        "ad_delivery_start_time": None,
        "videos": [{"video_hd_url": "https://video.fbcdn.net/ad.mp4"}],
        "snapshot": {
            "link_url": "https://loja.myshopify.com/checkout",
            "cta_type": "LEARN_MORE",
            "body": {"text": "Copy"},
        },
    }
    monkeypatch.setattr(worker, "_upload_video_r2", lambda *_: ("https://cdn.test/video.mp4", None))
    monkeypatch.setattr(
        worker,
        "_persistir_oferta",
        lambda *_: {"id": 42, "score": 55, "status_qualificacao": "pendente"},
    )
    send_task = MagicMock()
    monkeypatch.setattr(worker.app, "send_task", send_task)

    result = avaliar_oferta.run(payload)

    assert result["oferta_id"] == 42
    send_task.assert_called_once_with(
        "src.worker_ia.processar_anuncio_multimodal",
        args=[42, "https://cdn.test/video.mp4", "Copy"],
        queue="ai_multimodal_queue",
    )


def test_mesmo_digest_concluido_nao_despacha_ia(monkeypatch):
    import src.worker_qualification as worker

    payload = {
        "ad_archive_id": "ad-456",
        "ad_delivery_start_time": None,
        "videos": [{"video_hd_url": "https://video.fbcdn.net/ad.mp4"}],
        "snapshot": {
            "link_url": "https://outra.myshopify.com/checkout",
            "cta_type": "LEARN_MORE",
            "body": {"text": "Copy"},
        },
    }
    digest = "cd" * 32
    monkeypatch.setattr(worker, "_upload_video_r2", lambda *_: ("https://cdn.test/video.mp4", digest))
    monkeypatch.setattr(
        worker,
        "_persistir_oferta",
        lambda *_: {"id": 99, "score": 55, "status_qualificacao": "pendente"},
    )
    monkeypatch.setattr(worker, "_deve_analisar", lambda *_args: False)
    send_task = MagicMock()
    monkeypatch.setattr(worker.app, "send_task", send_task)

    result = avaliar_oferta.run(payload)

    assert result["status"] == "duplicate_creative"
    assert result["oferta_id"] == 99
    send_task.assert_not_called()


def test_recaptura_usa_phash_persistido_para_deduplicar(monkeypatch):
    import src.worker_qualification as worker

    monkeypatch.setattr(worker, "_upload_video_r2", lambda *_: ("https://cdn.test/video.mp4", None))
    monkeypatch.setattr(
        worker,
        "_persistir_oferta",
        lambda *_: {
            "id": 99,
            "score": 55,
            "status_qualificacao": "pendente",
            "phash_criativo": "cd" * 32,
            "falhas_ia": 0,
        },
    )
    deve_analisar = MagicMock(return_value=False)
    monkeypatch.setattr(worker, "_deve_analisar", deve_analisar)
    send_task = MagicMock()
    monkeypatch.setattr(worker.app, "send_task", send_task)

    result = avaliar_oferta.run(_payload_qualificacao())

    assert result == {"status": "duplicate_creative", "oferta_id": 99, "score": 55}
    deve_analisar.assert_called_once_with("cd" * 32, 99)
    send_task.assert_not_called()


@pytest.mark.parametrize(
    ("status", "falhas_ia"),
    [("concluido", 0), ("processando_ia", 0), ("pendente", 3)],
)
def test_oferta_nao_e_redespachada_quando_nao_esta_elegivel(monkeypatch, status, falhas_ia):
    import src.worker_qualification as worker

    monkeypatch.setattr(worker, "_upload_video_r2", lambda *_: ("https://cdn.test/video.mp4", None))
    monkeypatch.setattr(
        worker,
        "_persistir_oferta",
        lambda *_: {
            "id": 42,
            "score": 55,
            "status_qualificacao": status,
            "phash_criativo": None,
            "falhas_ia": falhas_ia,
        },
    )
    send_task = MagicMock()
    monkeypatch.setattr(worker.app, "send_task", send_task)

    result = avaliar_oferta.run(_payload_qualificacao())

    assert result["status"] == "qualified"
    send_task.assert_not_called()


def test_pode_despachar_ia_libera_apos_espera_com_teto():
    agora = datetime(2026, 10, 24, tzinfo=timezone.utc)

    assert _pode_despachar_ia({"falhas_ia": 3, "falhas_ia_em": agora - timedelta(days=8)}, agora) is True
    assert _pode_despachar_ia({"falhas_ia": 3, "falhas_ia_em": agora - timedelta(days=1)}, agora) is False
    assert _pode_despachar_ia({"falhas_ia": 5, "falhas_ia_em": agora - timedelta(days=30)}, agora) is False
    assert _pode_despachar_ia({"falhas_ia": 0, "falhas_ia_em": None}, agora) is True
    assert _pode_despachar_ia({"falhas_ia": 3, "falhas_ia_em": None}, agora) is False


def test_oferta_pendente_mais_antiga_nao_despacha_ia(monkeypatch):
    import src.worker_qualification as worker

    payload = {
        "ad_archive_id": "ad-789",
        "ad_delivery_start_time": None,
        "videos": [{"video_hd_url": "https://video.fbcdn.net/ad.mp4"}],
        "snapshot": {
            "link_url": "https://terceira.myshopify.com/checkout",
            "cta_type": "LEARN_MORE",
            "body": {"text": "Copy"},
        },
    }
    digest = "ab" * 32
    monkeypatch.setattr(worker, "_upload_video_r2", lambda *_: ("https://cdn.test/video.mp4", digest))
    monkeypatch.setattr(
        worker,
        "_persistir_oferta",
        lambda *_: {"id": 99, "score": 55, "status_qualificacao": "pendente"},
    )
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": 10,
        "score": 55,
        "status_qualificacao": "pendente",
        "url_video_r2": "https://cdn.test/antiga.mp4",
    }
    conexao = MagicMock()
    conexao.cursor.return_value.__enter__.return_value = cursor
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conexao
    monkeypatch.setattr(worker, "get_pool", lambda: pool)
    send_task = MagicMock()
    monkeypatch.setattr(worker.app, "send_task", send_task)

    result = avaliar_oferta.run(payload)

    assert result["status"] == "duplicate_creative"
    assert result["oferta_id"] == 99
    send_task.assert_not_called()
    lock_sql = cursor.execute.call_args_list[0].args[0]
    consulta = cursor.execute.call_args_list[1].args[0]
    assert "pg_advisory_xact_lock" in lock_sql
    assert "processando_ia" in consulta
    assert "score >= 50" in consulta
    assert cursor.execute.call_args_list[1].args[1] == {"phash": digest, "id": 99}


def _payload_qualificacao():
    return {
        "ad_archive_id": "ad-456",
        "ad_delivery_start_time": None,
        "videos": [{"video_hd_url": "https://video.fbcdn.net/ad.mp4"}],
        "snapshot": {
            "link_url": "https://loja.myshopify.com/checkout",
            "cta_type": "LEARN_MORE",
            "body": {"text": "Copy"},
        },
    }


def _falha_na_persistencia(monkeypatch):
    import src.worker_qualification as worker

    monkeypatch.setattr(worker, "_upload_video_r2", lambda *_args: (None, None))

    def _falha(*_args, **_kwargs):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(worker, "_persistir_oferta", _falha)
    send_task = MagicMock()
    monkeypatch.setattr(worker.app, "send_task", send_task)
    return send_task


def test_qualificacao_descarta_na_ultima_tentativa(monkeypatch, caplog):
    send_task = _falha_na_persistencia(monkeypatch)
    avaliar_oferta.push_request(retries=3)
    try:
        with caplog.at_level(logging.ERROR):
            result = avaliar_oferta.run(_payload_qualificacao())
    finally:
        avaliar_oferta.pop_request()

    assert result["status"] == "discarded"
    assert result["ad_id"] == "ad-456"
    assert result["url_destino"] == "https://loja.myshopify.com/checkout"
    assert result["motivo"] == "RuntimeError"
    assert "ad-456" in caplog.text
    assert "https://loja.myshopify.com/checkout" in caplog.text
    send_task.assert_not_called()


def test_qualificacao_retenta_antes_do_descarte(monkeypatch):
    send_task = _falha_na_persistencia(monkeypatch)
    monkeypatch.setattr(avaliar_oferta, "retry", MagicMock(side_effect=Retry("retry")))
    avaliar_oferta.push_request(retries=0)
    try:
        with pytest.raises(Retry):
            avaliar_oferta.run(_payload_qualificacao())
    finally:
        avaliar_oferta.pop_request()

    avaliar_oferta.retry.assert_called_once()
    send_task.assert_not_called()
