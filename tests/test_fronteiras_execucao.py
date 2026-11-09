"""Pausa de sessão, custo de aterrissagem, funil, triagem e egress."""

from decimal import Decimal
from pathlib import Path

from src.egress_pausa import registrar_resultado
from src.funil import SQL_ATIVIDADE, ler_funil
from src.sessao import classificar_sessao
from src.triagem_local import triagem_local
from src.viabilidade import custo_liquido


def test_uma_aba_da_biblioteca_libera_mesmo_com_login():
    urls = [
        "https://www.facebook.com/login",
        "https://www.facebook.com/ads/library/?q=teste",
    ]
    assert classificar_sessao(urls) == "ok"


def test_duas_abas_da_biblioteca_contam_como_ausente():
    urls = [
        "https://www.facebook.com/ads/library/?q=a",
        "https://www.facebook.com/ads/library/?q=b",
    ]
    assert classificar_sessao(urls) == "aba_ausente"


def test_checkpoint_pede_sessao():
    assert classificar_sessao(["https://www.facebook.com/checkpoint/"]) == "sessao_pedida"


def test_custo_liquido_desconta_imposto_gateway_e_frete():
    aliquota = {
        "ii": Decimal("0.20"),
        "icms": Decimal("0.17"),
        "gateway_percentual": Decimal("0.0499"),
        "gateway_fixo_brl": Decimal("1.00"),
        "chargeback": Decimal("0.01"),
    }
    assert custo_liquido(Decimal("100"), Decimal("20"), Decimal("10"), aliquota) == Decimal("49.64")


def test_funil_le_pixel_e_so_preco_explicito():
    html = """
    <script>fbq('init', '1234567890');</script>
    <div class="order-bump" data-bump-price="12.90"></div>
    """
    funil = ler_funil(html)
    assert funil["pixels"] == ["1234567890"]
    assert funil["tem_bump"] is True
    assert funil["preco_bump"] == Decimal("12.90")
    assert ler_funil('<div class="order-bump"></div>')["preco_bump"] is None


def test_atividade_agrupa_dominio_e_nao_fatura():
    assert "SUM(vezes_avistado)" in SQL_ATIVIDADE
    assert "faturamento" not in SQL_ATIVIDADE


def test_triagem_sem_arquivo_segue(monkeypatch):
    monkeypatch.delenv("MODELO_TRIAGEM_PATH", raising=False)
    assert triagem_local("texto", {"casa"}) == "seguir"


def test_triagem_descarta_generico_e_fora_do_catalogo(monkeypatch, tmp_path):
    modelo = tmp_path / "modelo.bin"
    modelo.write_bytes(b"x")
    monkeypatch.setenv("MODELO_TRIAGEM_PATH", str(modelo))

    def prever(caminho, texto):
        return ("generico", 0.95)

    monkeypatch.setattr("src.triagem_local.prever", prever)
    assert triagem_local("texto", {"casa"}) == "descartar"

    def fora(caminho, texto):
        return ("outro", 0.91)

    monkeypatch.setattr("src.triagem_local.prever", fora)
    assert triagem_local("texto", {"casa"}) == "descartar"

    def no_catalogo(caminho, texto):
        return ("casa", 0.99)

    monkeypatch.setattr("src.triagem_local.prever", no_catalogo)
    assert triagem_local("texto", {"casa"}) == "seguir"

    def baixa(caminho, texto):
        return ("generico", 0.89)

    monkeypatch.setattr("src.triagem_local.prever", baixa)
    assert triagem_local("texto", {"casa"}) == "seguir"


class _Redis:
    def __init__(self):
        self.membros = []
        self.pausado = False

    def get(self, chave):
        return b"1" if self.pausado else None

    def zadd(self, chave, mapa):
        self.membros.extend(mapa)

    def zremrangebyscore(self, chave, inicio, fim):
        return 0

    def zrange(self, chave, inicio, fim):
        return [item.encode() if isinstance(item, str) else item for item in self.membros]

    def set(self, chave, valor, ex):
        self.pausado = True


def test_egress_pausa_depois_de_vinte_e_nao_grava_durante_a_pausa(monkeypatch):
    cliente = _Redis()
    monkeypatch.setattr("src.egress_pausa._cliente", lambda: cliente)
    for _ in range(13):
        registrar_resultado(False)
    for _ in range(7):
        registrar_resultado(True)
    assert cliente.pausado is True
    antes = len(cliente.membros)
    registrar_resultado(True)
    assert len(cliente.membros) == antes


def test_rascunho_sem_margem_positiva_nao_chama_a_loja(monkeypatch):
    from src import loja

    monkeypatch.setenv("LOJA_DESTINO", "shopify")
    monkeypatch.setattr(loja, "ler_segredo", lambda nome: "tok" if "TOKEN" in nome else "minhaloja.myshopify.com")
    monkeypatch.setattr(loja, "_loja_publica", lambda host: host)
    chamadas = []
    monkeypatch.setattr(loja.requests, "post", lambda *args, **kwargs: chamadas.append(1))
    assert loja.rascunho_shopify({"venda_brl": "80"}) is None
    assert loja.rascunho_shopify({"venda_brl": "80", "margem_liquida_brl": "0"}) is None
    assert loja.rascunho_shopify({"venda_brl": "80", "margem_liquida_brl": "-1"}) is None
    assert chamadas == []


def test_status_aceita_descarte_local():
    raiz = Path(__file__).resolve().parents[1]
    init = (raiz / "infra" / "init-db.sql").read_text()
    migracao = (raiz / "infra" / "migrations" / "026_viabilidade_funil.sql").read_text()
    assert init.count("descartado_local") >= 2
    assert "descartado_local" in migracao
    assert "BEGIN;" not in migracao
    assert "COMMIT;" not in migracao


def test_coleta_de_metricas_sem_servico(monkeypatch):
    from src.metricas import coletar

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    assert coletar() == "\n"
