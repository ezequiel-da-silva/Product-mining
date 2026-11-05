from src.agrupamento import (
    LIMIAR_COSENO,
    SQL_IDS_PARA_AGRUPAR,
    SQL_MEMBROS,
    SQL_ORIGEM,
    SQL_POR_CRIATIVO,
    SQL_POR_EMBEDDING,
    _id_inteiro,
    _ids_do_grupo,
    estado_do_grupo,
    ids_para_agrupar,
)
from src.phash_visual import LIMIAR_HAMMING


class _Cursor:
    def __init__(self, respostas: list[list[dict]]):
        self._respostas = list(respostas)
        self.sqls: list[str] = []
        self._atual: list[dict] = []

    def execute(self, sql: str, _params: dict) -> None:
        self.sqls.append(sql)
        self._atual = self._respostas.pop(0)

    def fetchone(self):
        return self._atual[0] if self._atual else None

    def fetchall(self):
        return self._atual


def _oferta(identificador: int, status: str = "concluido") -> dict:
    return {
        "id": identificador,
        "status_qualificacao": status,
        "url_video_r2": None,
        "phash_criativo": None,
        "phash_perceptual": None,
        "nicho": "casa",
        "oferta_canonica_id": None,
    }


def test_triagem_do_grupo_prefere_testando():
    assert estado_do_grupo([]) is None
    assert estado_do_grupo(["descartada"]) == "descartada"
    assert estado_do_grupo(["descartada", "estrelada"]) == "estrelada"
    assert estado_do_grupo(["estrelada", "testando", "descartada"]) == "testando"
    assert estado_do_grupo(["outro"]) is None


def test_id_inteiro_rejeita_booleano_e_texto():
    assert _id_inteiro(4) == 4
    assert _id_inteiro(True) is None
    assert _id_inteiro("4") is None


def test_ids_para_agrupar_ignora_id_invalido():
    cur = _Cursor([[{"id": 3}, {"id": True}, {"id": "4"}]])
    assert ids_para_agrupar(cur, 10) == [3]


def test_grupo_anda_dois_saltos_e_ignora_id_booleano():
    origem = _oferta(10)
    cur = _Cursor(
        [
            [
                {"id": 20, "oferta_canonica_id": 5},
                {"id": True, "oferta_canonica_id": 99},
                {"id": 10, "oferta_canonica_id": 7},
            ],
            [{"id": 30, "oferta_canonica_id": None}],
            [_oferta(20)],
            [_oferta(30, "pendente")],
            [{"id": 40, "oferta_canonica_id": 20}],
            [],
            [_oferta(40)],
            [{"id": 50, "oferta_canonica_id": 40}, {"id": False, "oferta_canonica_id": True}],
        ]
    )

    grupo = _ids_do_grupo(cur, origem)

    assert grupo == {10, 20, 5, 7, 30, 40, 50}
    assert cur.sqls.count(SQL_POR_CRIATIVO) == 2
    assert cur.sqls.count(SQL_POR_EMBEDDING) == 2
    assert cur.sqls.count(SQL_ORIGEM) == 3
    assert cur.sqls[-1] == SQL_MEMBROS


def test_grupo_sem_irma_nao_busca_membros():
    cur = _Cursor([[], []])

    assert _ids_do_grupo(cur, _oferta(10)) == {10}
    assert cur.sqls == [SQL_POR_CRIATIVO, SQL_POR_EMBEDDING]


def test_agrupamento_usa_phash_e_cosseno_curto():
    assert LIMIAR_HAMMING == 8
    assert LIMIAR_COSENO == 0.08
    assert "bit_count(o.phash_perceptual # irma.phash_perceptual)" in SQL_IDS_PARA_AGRUPAR
    assert "<= %(limiar_coseno)s" in SQL_POR_EMBEDDING
    assert "o.nicho IS NOT DISTINCT FROM %(nicho)s" in SQL_POR_EMBEDDING
    assert "oferta_canonica_id IS NULL" in SQL_IDS_PARA_AGRUPAR
