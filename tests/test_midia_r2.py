from unittest.mock import MagicMock

from src.midia_r2 import TTL_LEITURA_SEGUNDOS, referencia_armazenada, url_leitura


def _sem_publico(nome: str) -> str:
    if nome == "CLOUDFLARE_R2_PUBLIC_URL":
        return ""
    return {
        "CLOUDFLARE_R2_ACCOUNT_ID": "acc",
        "CLOUDFLARE_R2_ACCESS_KEY": "key",
        "CLOUDFLARE_R2_SECRET_KEY": "secret",
        "CLOUDFLARE_R2_BUCKET": "bucket",
    }.get(nome, "")


def test_sem_dominio_publico_a_referencia_e_a_chave(monkeypatch):
    monkeypatch.setattr("src.midia_r2.ler_segredo", lambda nome: "" if nome == "CLOUDFLARE_R2_PUBLIC_URL" else "x")

    assert referencia_armazenada("videos/ad-1.mp4") == "videos/ad-1.mp4"


def test_dominio_publico_guarda_url_estavel(monkeypatch):
    monkeypatch.setattr(
        "src.midia_r2.ler_segredo",
        lambda nome: "https://cdn.example" if nome == "CLOUDFLARE_R2_PUBLIC_URL" else "",
    )

    assert referencia_armazenada("videos/ad-1.mp4") == "https://cdn.example/videos/ad-1.mp4"


def test_leitura_assina_a_chave_por_poucos_minutos(monkeypatch):
    monkeypatch.setattr("src.midia_r2.ler_segredo", _sem_publico)
    cliente = MagicMock()
    cliente.generate_presigned_url.return_value = (
        "https://acc.r2.cloudflarestorage.com/videos/ad-1.mp4?X-Amz-Expires=300"
    )
    monkeypatch.setattr("src.midia_r2.boto3.client", lambda *_args, **_kwargs: cliente)

    assert url_leitura("videos/ad-1.mp4").startswith("https://")
    cliente.generate_presigned_url.assert_called_once()
    assert cliente.generate_presigned_url.call_args.kwargs["ExpiresIn"] == TTL_LEITURA_SEGUNDOS
    assert cliente.generate_presigned_url.call_args.kwargs["Params"]["Key"] == "videos/ad-1.mp4"
    assert TTL_LEITURA_SEGUNDOS == 300


def test_url_publica_nao_e_reassinada(monkeypatch):
    monkeypatch.setattr(
        "src.midia_r2.ler_segredo",
        lambda nome: "https://cdn.example" if nome == "CLOUDFLARE_R2_PUBLIC_URL" else "x",
    )
    cliente = MagicMock()
    monkeypatch.setattr("src.midia_r2.boto3.client", lambda *_args, **_kwargs: cliente)

    assert url_leitura("https://cdn.example/videos/ad-1.mp4") == "https://cdn.example/videos/ad-1.mp4"
    cliente.generate_presigned_url.assert_not_called()


def test_url_https_legada_sem_credencial_continua_legivel(monkeypatch):
    monkeypatch.setattr("src.midia_r2.ler_segredo", lambda _nome: "")
    legado = "https://acc.r2.cloudflarestorage.com/bucket/videos/ad-1.mp4?X-Amz-Expires=604800"

    assert url_leitura(legado) == legado


def test_worker_nao_grava_presign_de_sete_dias(monkeypatch):
    import src.worker_qualification as worker

    monkeypatch.setattr("src.midia_r2.ler_segredo", lambda _nome: "")
    cliente = MagicMock()

    assert worker._url_r2(cliente, "bucket", "videos/ad-1.mp4") == "videos/ad-1.mp4"
    cliente.generate_presigned_url.assert_not_called()
