"""Ritmo dos 30 primeiros segundos, legenda do primeiro quadro e narração com folga de alinhamento."""

import json
import re
import shutil
import subprocess  # nosec B404
import threading
from dataclasses import dataclass

JANELA_SEGUNDOS = 30
_DURACAO = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def mapa_alinhado(esquerda: list[int], direita: list[int], deslocamento: int) -> tuple[list[int], list[int]]:
    if deslocamento >= 0:
        return esquerda[deslocamento:], direita
    return esquerda, direita[-deslocamento:]


def narracao_parecida(esquerda: list[int], direita: list[int], max_shift: int = 4) -> bool:
    """Compara fingerprints do Chromaprint com folga de até 4 quadros (~480 ms)."""
    if min(len(esquerda), len(direita)) < 20:
        return False
    melhor = 0.0
    for deslocamento in range(-max_shift, max_shift + 1):
        trecho_esquerdo, trecho_direito = mapa_alinhado(esquerda, direita, deslocamento)
        tamanho = min(len(trecho_esquerdo), len(trecho_direito))
        if tamanho < 15:
            continue
        iguais = sum(1 for indice in range(tamanho) if trecho_esquerdo[indice] == trecho_direito[indice])
        taxa = iguais / tamanho
        if taxa > melhor:
            melhor = taxa
            if melhor >= 0.85:
                return True
    return melhor >= 0.85


def ritmo_dos_cortes(cortes: int, duracao_cabecalho: float | None) -> float | None:
    """O denominador é a janela lida, no máximo 30 s, não a duração do arquivo inteiro."""
    if duracao_cabecalho is None or duracao_cabecalho <= 0 or cortes < 0:
        return None
    janela = min(float(duracao_cabecalho), float(JANELA_SEGUNDOS))
    return round(cortes / janela, 3)


def duracao_do_ffmpeg(stderr: str) -> float | None:
    encontrado = _DURACAO.search(stderr)
    if not encontrado:
        return None
    horas, minutos, segundos = encontrado.groups()
    return int(horas) * 3600 + int(minutos) * 60 + float(segundos)


def argv_cortes(executavel: str) -> list[str]:
    return [
        executavel,
        "-v",
        "info",
        "-t",
        str(JANELA_SEGUNDOS),
        "-i",
        "pipe:0",
        "-vf",
        "select='gt(scene,0.4)',showinfo",
        "-f",
        "null",
        "-",
    ]


def argv_quadro(executavel: str) -> list[str]:
    return [
        executavel,
        "-v",
        "error",
        "-ss",
        "0",
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
        "-vf",
        "scale=1280:-1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]


def argv_audio(executavel: str) -> list[str]:
    return [
        executavel,
        "-v",
        "error",
        "-t",
        str(JANELA_SEGUNDOS),
        "-i",
        "pipe:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "11025",
        "-f",
        "wav",
        "pipe:1",
    ]


def cortes_do_log(stderr: str) -> tuple[int, float | None]:
    return stderr.count("pts_time:"), duracao_do_ffmpeg(stderr)


def fingerprint_de_wav(wav: bytes) -> list[int] | None:
    executavel = shutil.which("fpcalc")
    if not executavel or not wav:
        return None
    processo = subprocess.run(  # nosec B603
        [executavel, "-json", "-length", str(JANELA_SEGUNDOS), "-"],
        input=wav,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if processo.returncode != 0 or not processo.stdout:
        return None
    try:
        dados = json.loads(processo.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    bruto = dados.get("fingerprint") if isinstance(dados, dict) else None
    if not isinstance(bruto, list):
        return None
    valores = [item for item in bruto if isinstance(item, int) and not isinstance(item, bool)]
    return valores or None


def legenda_do_png(png: bytes) -> str:
    executavel = shutil.which("tesseract")
    if not executavel or not png:
        return ""
    processo = subprocess.run(  # nosec B603
        [executavel, "stdin", "stdout", "-l", "por+eng", "--psm", "6"],
        input=png,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if processo.returncode != 0:
        return ""
    return " ".join(processo.stdout.decode("utf-8", errors="replace").split())[:500]


@dataclass
class LeituraVisao:
    legenda: str = ""
    cortes_por_segundo: float | None = None
    fingerprint: list[int] | None = None


def _drenar(origem: object, destino: bytearray) -> None:
    leitura = getattr(origem, "read", None)
    if leitura is None:
        return
    while True:
        bloco = leitura(65_536)
        if not bloco:
            break
        destino.extend(bloco)


class _Tubo:
    """Drena stdout e stderr em threads. Sem isso o pipe de 64 KiB trava o ffmpeg."""

    def __init__(self, argv: list[str]) -> None:
        self._processo: subprocess.Popen[bytes] | None = None
        self._saida = bytearray()
        self._erro = bytearray()
        self._leitores: list[threading.Thread] = []
        if not argv or not argv[0]:
            return
        self._processo = subprocess.Popen(  # nosec B603
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._leitores = [
            threading.Thread(target=_drenar, args=(self._processo.stdout, self._saida), daemon=True),
            threading.Thread(target=_drenar, args=(self._processo.stderr, self._erro), daemon=True),
        ]
        for leitor in self._leitores:
            leitor.start()

    def escrever(self, bloco: bytes) -> None:
        processo = self._processo
        if processo is None or processo.stdin is None or processo.poll() is not None:
            return
        try:
            processo.stdin.write(bloco)
        except BrokenPipeError:
            return

    def _juntar_leitores(self) -> None:
        for leitor in self._leitores:
            leitor.join(timeout=5)

    def fechar(self) -> tuple[bytes, str]:
        processo = self._processo
        self._processo = None
        if processo is None or processo.stdin is None:
            self._juntar_leitores()
            return b"", ""
        try:
            if not processo.stdin.closed:
                processo.stdin.close()
        except BrokenPipeError:
            pass
        try:
            processo.wait(timeout=20)
        except subprocess.TimeoutExpired:
            processo.kill()
            processo.wait(timeout=5)
            self._juntar_leitores()
            return b"", ""
        self._juntar_leitores()
        return bytes(self._saida), self._erro.decode("utf-8", errors="replace")

    def encerrar(self) -> None:
        processo = self._processo
        self._processo = None
        if processo is not None and processo.poll() is None:
            if processo.stdin is not None and not processo.stdin.closed:
                try:
                    processo.stdin.close()
                except BrokenPipeError:
                    pass
            processo.kill()
            processo.wait(timeout=5)
        self._juntar_leitores()


class ColetorVisao:
    """Três ffmpegs leem o mesmo fluxo. Sem ffmpeg, o coletor não abre processo."""

    def __init__(self) -> None:
        executavel = shutil.which("ffmpeg") or ""
        self._cortes = _Tubo(argv_cortes(executavel) if executavel else [])
        self._quadro = _Tubo(argv_quadro(executavel) if executavel else [])
        self._audio = _Tubo(argv_audio(executavel) if executavel else [])
        self.resultado = LeituraVisao()
        self._fechado = False

    def escrever(self, bloco: bytes) -> None:
        self._cortes.escrever(bloco)
        self._quadro.escrever(bloco)
        self._audio.escrever(bloco)

    def fechar(self) -> LeituraVisao:
        if self._fechado:
            return self.resultado
        self._fechado = True
        _png, log_cortes = self._cortes.fechar()
        png, _log_quadro = self._quadro.fechar()
        wav, _log_audio = self._audio.fechar()
        cortes, duracao = cortes_do_log(log_cortes)
        self.resultado = LeituraVisao(
            legenda=legenda_do_png(png),
            cortes_por_segundo=ritmo_dos_cortes(cortes, duracao),
            fingerprint=fingerprint_de_wav(wav),
        )
        return self.resultado

    def encerrar(self) -> None:
        self._cortes.encerrar()
        self._quadro.encerrar()
        self._audio.encerrar()
