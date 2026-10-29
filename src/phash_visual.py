"""pHash de um quadro 32×32. O SHA-256 continua sendo a identidade exata do arquivo."""

import math
import shutil
import subprocess

TAMANHO = 32
_BAIXA_FREQUENCIA = 8
LIMIAR_HAMMING = 8


def distancia_hamming(esquerda: int, direita: int) -> int:
    return (esquerda ^ direita).bit_count()


def para_bit64(valor: int) -> str:
    return f"{valor & ((1 << 64) - 1):064b}"


def _canonizar(pixels: list[int], largura: int) -> list[int]:
    """Espelha na horizontal quando a direita é mais clara, para o espelho cair no mesmo hash."""
    meio = largura // 2
    altura = len(pixels) // largura
    esquerda = 0
    direita = 0
    for linha in range(altura):
        inicio = linha * largura
        esquerda += sum(pixels[inicio : inicio + meio])
        direita += sum(pixels[inicio + meio : inicio + largura])
    if direita <= esquerda:
        return pixels
    virada: list[int] = []
    for linha in range(altura):
        inicio = linha * largura
        virada.extend(reversed(pixels[inicio : inicio + largura]))
    return virada


def _dct(vetor: list[float]) -> list[float]:
    tamanho = len(vetor)
    espectro: list[float] = []
    for frequencia in range(tamanho):
        soma = 0.0
        for indice, valor in enumerate(vetor):
            soma += valor * math.cos(math.pi * frequencia * (2 * indice + 1) / (2 * tamanho))
        espectro.append(soma)
    return espectro


def phash_de_luma(pixels: bytes | list[int], largura: int = TAMANHO) -> int:
    valores = _canonizar([int(ponto) for ponto in pixels], largura)
    if len(valores) != largura * largura:
        raise ValueError("O quadro do pHash precisa ser quadrado.")
    linhas = [valores[linha * largura : (linha + 1) * largura] for linha in range(largura)]
    horizontais = [_dct([float(ponto) for ponto in linha]) for linha in linhas]
    espectro: list[list[float]] = []
    for linha in range(largura):
        coluna = _dct([horizontais[origem][linha] for origem in range(largura)])
        espectro.append(coluna)
    coeficientes = [
        espectro[linha][coluna] for linha in range(_BAIXA_FREQUENCIA) for coluna in range(_BAIXA_FREQUENCIA)
    ]
    mediana = sorted(coeficientes)[len(coeficientes) // 2]
    bits = 0
    for indice, coeficiente in enumerate(coeficientes):
        if coeficiente > mediana:
            bits |= 1 << indice
    return bits


class CapturaQuadro:
    """Pede um quadro cinza 32×32 ao ffmpeg enquanto o vídeo é lido uma vez."""

    def __init__(self) -> None:
        self._processo: subprocess.Popen[bytes] | None = None
        executavel = shutil.which("ffmpeg")
        if not executavel:
            return
        self._processo = subprocess.Popen(
            [
                executavel,
                "-v",
                "error",
                "-i",
                "pipe:0",
                "-frames:v",
                "1",
                "-vf",
                f"scale={TAMANHO}:{TAMANHO}",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "pipe:1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def escrever(self, bloco: bytes) -> None:
        processo = self._processo
        if processo is None or processo.stdin is None or processo.poll() is not None:
            return
        try:
            processo.stdin.write(bloco)
        except BrokenPipeError:
            return

    def fechar(self) -> str | None:
        processo = self._processo
        self._processo = None
        if processo is None or processo.stdin is None or processo.stdout is None:
            return None
        try:
            if not processo.stdin.closed:
                processo.stdin.close()
        except BrokenPipeError:
            pass
        try:
            quadro = processo.stdout.read()
            processo.wait(timeout=20)
        except subprocess.TimeoutExpired:
            processo.kill()
            processo.wait(timeout=5)
            return None
        if len(quadro) != TAMANHO * TAMANHO:
            return None
        return para_bit64(phash_de_luma(quadro))

    def encerrar(self) -> None:
        processo = self._processo
        self._processo = None
        if processo is None or processo.poll() is not None:
            return
        if processo.stdin is not None and not processo.stdin.closed:
            try:
                processo.stdin.close()
            except BrokenPipeError:
                pass
        processo.kill()
        try:
            processo.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return
