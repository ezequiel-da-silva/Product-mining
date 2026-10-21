import os


def ler_segredo(nome: str, diretorio: str = "/run/secrets") -> str:
    """Prefere o arquivo em /run/secrets e cai na variável de ambiente se ele estiver vazio."""
    caminho = os.path.join(diretorio, nome)
    try:
        with open(caminho, encoding="utf-8") as arquivo:
            valor = arquivo.read().strip()
    except OSError:
        valor = ""
    if valor:
        return valor
    return os.environ.get(nome, "")
