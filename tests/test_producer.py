import logging
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.producer import _extrair_anuncios, despachar_anuncio_capturado, url_cdp_permitida  # noqa: E402


def test_extrair_anuncios_encontra_nodes_graphql_aninhados_e_remove_duplicatas():
    first_ad = {"ad_archive_id": "ad-1", "snapshot": {"link_url": "https://shop.test"}}
    payload = {
        "data": {
            "search": {
                "edges": [
                    {"node": first_ad},
                    {"node": {"collateral_id": "ad-2", "snapshot": {}}},
                    {"node": first_ad},
                ]
            }
        }
    }

    assert _extrair_anuncios(payload) == [first_ad, {"collateral_id": "ad-2", "snapshot": {}}]


def test_despachar_anuncio_usa_tarefa_e_fila_de_qualificacao():
    from src import producer

    send_task = MagicMock()
    with patch.object(producer.app, "send_task", send_task):
        dispatched = despachar_anuncio_capturado({"ad_archive_id": "ad-1"})

    assert dispatched is True
    send_task.assert_called_once_with(
        "tasks.qualification.avaliar_oferta",
        args=[{"ad_archive_id": "ad-1"}],
        queue="qualification_queue",
    )


def test_url_cdp_rejeita_host_de_metadata():
    with pytest.raises(ValueError, match="lista permitida"):
        url_cdp_permitida("169.254.169.254", "80")


def test_url_cdp_aceita_host_da_bridge():
    assert url_cdp_permitida("host.docker.internal", "9222") == "http://host.docker.internal:9222"


def test_falha_de_fila_interrompe_a_varredura():
    from src import producer

    page = MagicMock()
    handlers = []
    page.on.side_effect = lambda _event, callback: handlers.append(callback)

    def acionar_resposta(_script):
        response = MagicMock()
        response.url = "https://www.facebook.com/ads/library/api/graphql/"
        response.status = 200
        response.json.return_value = {
            "data": {
                "edges": [
                    {"node": {"ad_archive_id": "ad-1"}},
                    {"node": {"ad_archive_id": "ad-2"}},
                ]
            }
        }
        for callback in handlers:
            callback(response)

    page.evaluate.side_effect = acionar_resposta

    with (
        patch.object(producer, "stealth_sync", lambda _page: None),
        patch.object(
            producer,
            "despachar_anuncio_capturado",
            lambda payload: payload.get("ad_archive_id") == "ad-1",
        ),
        pytest.raises(producer.FilaIndisponivel),
    ):
        producer.rotina_extracao(page)

    assert page.evaluate.call_count == 1
    page.wait_for_timeout.assert_not_called()


def test_fila_cheia_pausa_na_mesma_pagina_sem_encerrar(monkeypatch, caplog):
    from src import producer

    chamadas = {"n": 0}

    def rotina(_page):
        chamadas["n"] += 1
        if chamadas["n"] <= 11:
            raise producer.FilaIndisponivel("cheio")

    pausas = []
    monkeypatch.setattr(producer, "rotina_extracao", rotina)
    monkeypatch.setattr(producer.time, "sleep", lambda segundos: pausas.append(segundos))

    with caplog.at_level(logging.CRITICAL):
        producer._varrer_com_pausa_de_fila(MagicMock())

    assert chamadas["n"] == 12
    assert pausas == [60] * 11
    assert "Encerrando Producer" not in caplog.text
