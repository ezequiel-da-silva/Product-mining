from src.phash_visual import LIMIAR_HAMMING, distancia_hamming, para_bit64, phash_de_luma


def _quadro(funcao) -> list[int]:
    return [funcao(x, y) for y in range(32) for x in range(32)]


def _espelho(pixels: list[int]) -> list[int]:
    virado: list[int] = []
    for linha in range(32):
        inicio = linha * 32
        virado.extend(reversed(pixels[inicio : inicio + 32]))
    return virado


def test_hash_igual_e_espelho_coincidem():
    original = _quadro(lambda x, y: (x * 9 + y) % 256)
    assert phash_de_luma(original) == phash_de_luma(bytes(original))
    assert phash_de_luma(original) == phash_de_luma(_espelho(original))
    assert distancia_hamming(phash_de_luma(original), phash_de_luma(_espelho(original))) == 0


def test_quadros_diferentes_passam_do_limiar():
    listras = _quadro(lambda _x, y: 255 if y < 16 else 0)
    xadrez = _quadro(lambda x, y: 255 if (x + y) % 2 == 0 else 0)
    distancia = distancia_hamming(phash_de_luma(listras), phash_de_luma(xadrez))
    assert distancia > LIMIAR_HAMMING


def test_bit64_tem_64_posicoes():
    assert len(para_bit64(1)) == 64
    assert para_bit64(1).endswith("1")
    assert para_bit64(0) == "0" * 64
