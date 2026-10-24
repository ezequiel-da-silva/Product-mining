#!/usr/bin/env python3
"""Valida que cada migração em infra/migrations é idempotente e está registrada.

Usado pelo workflow migrations-check.yml. Pode ser executado localmente:

    python3 infra/check_migrations.py
"""

import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = BASE_DIR / "infra" / "migrations"
INIT_DB_SQL = BASE_DIR / "infra" / "init-db.sql"
APPLY_MIGRATIONS_SH = BASE_DIR / "infra" / "apply-migrations.sh"

IDEMPOTENT_MARKERS = re.compile(r"IF\s+NOT\s+EXISTS|IF\s+EXISTS|ON\s+CONFLICT|DO\s+\$\$", re.IGNORECASE)

# GRANT/REVOKE/COMMENT ON são idempotentes por natureza no PostgreSQL: reexecutar
# não falha nem muda o resultado, então não exigimos marcador textual para eles.
INHERENTLY_IDEMPOTENT_STATEMENT = re.compile(r"^\s*(GRANT|REVOKE|COMMENT\s+ON)\b", re.IGNORECASE)


def _statements(sql: str) -> list[str]:
    sem_comentarios = re.sub(r"--.*", "", sql)
    return [statement.strip() for statement in sem_comentarios.split(";") if statement.strip()]


def _arquivo_e_idempotente(sql: str) -> bool:
    if IDEMPOTENT_MARKERS.search(sql):
        return True
    statements = _statements(sql)
    return bool(statements) and all(INHERENTLY_IDEMPOTENT_STATEMENT.match(statement) for statement in statements)


def main() -> int:
    if not MIGRATIONS_DIR.is_dir():
        print(f"Diretório não encontrado: {MIGRATIONS_DIR}", file=sys.stderr)
        return 1

    if not APPLY_MIGRATIONS_SH.is_file():
        print(f"Arquivo não encontrado: {APPLY_MIGRATIONS_SH}", file=sys.stderr)
        return 1

    init_db_sql = INIT_DB_SQL.read_text(encoding="utf-8")
    apply_sh = APPLY_MIGRATIONS_SH.read_text(encoding="utf-8")
    arquivos = sorted(MIGRATIONS_DIR.glob("*.sql"))
    erros: list[str] = []

    numeros = [arquivo.name.split("_", 1)[0] for arquivo in arquivos]
    duplicados = {numero for numero in numeros if numeros.count(numero) > 1}
    if duplicados:
        erros.append(f"Há prefixos numéricos repetidos em infra/migrations: {', '.join(sorted(duplicados))}.")

    for arquivo in arquivos:
        versao = arquivo.stem
        sql = arquivo.read_text(encoding="utf-8")

        if not sql.strip():
            erros.append(f"{arquivo.name}: arquivo vazio.")
            continue

        if not _arquivo_e_idempotente(sql):
            erros.append(
                f"{arquivo.name}: não contém marcador de idempotência "
                "(IF NOT EXISTS / IF EXISTS / ON CONFLICT / DO $$) nem é composto só por "
                "GRANT/REVOKE/COMMENT ON."
            )

        if f"'{versao}'" not in init_db_sql:
            erros.append(f"{arquivo.name}: versão '{versao}' não está registrada em infra/init-db.sql.")

        if f"infra/migrations/{arquivo.name}" not in apply_sh:
            erros.append(f"{arquivo.name}: não está na lista de infra/apply-migrations.sh.")

    if erros:
        print("Falhas na validação de migrações:", file=sys.stderr)
        for erro in erros:
            print(f"  - {erro}", file=sys.stderr)
        return 1

    print(f"OK: {len(arquivos)} migração(ões) validada(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
