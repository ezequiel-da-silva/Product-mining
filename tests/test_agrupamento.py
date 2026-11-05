from src.agrupamento import LIMIAR_COSENO, SQL_IDS_PARA_AGRUPAR, SQL_POR_EMBEDDING, estado_do_grupo
from src.phash_visual import LIMIAR_HAMMING


def test_triagem_do_grupo_prefere_testando():
    assert estado_do_grupo([]) is None
    assert estado_do_grupo(["descartada"]) == "descartada"
    assert estado_do_grupo(["descartada", "estrelada"]) == "estrelada"
    assert estado_do_grupo(["estrelada", "testando", "descartada"]) == "testando"
    assert estado_do_grupo(["outro"]) is None


def test_agrupamento_usa_phash_e_cosseno_curto():
    assert LIMIAR_HAMMING == 8
    assert LIMIAR_COSENO == 0.08
    assert "bit_count(o.phash_perceptual # irma.phash_perceptual)" in SQL_IDS_PARA_AGRUPAR
    assert "<= %(limiar_coseno)s" in SQL_POR_EMBEDDING
    assert "o.nicho IS NOT DISTINCT FROM %(nicho)s" in SQL_POR_EMBEDDING
    assert "oferta_canonica_id IS NULL" in SQL_IDS_PARA_AGRUPAR
