#!/bin/sh
# Linha do userlist do PgBouncer. Aspas na senha viram aspas duplicadas.

pgbouncer_gravar_papel() {
  arquivo=$1
  papel=$2
  senha=$3

  case "$papel" in
    pipeline_writer|lifecycle_writer) ;;
    *)
      echo "papel fora do PgBouncer: $papel" >&2
      return 1
      ;;
  esac

  if [ "$(printf '%s' "$senha" | tr -d '\n')" != "$senha" ]; then
    echo "senha com quebra de linha não entra no userlist" >&2
    return 1
  fi

  escaped=$(printf '%s' "$senha" | sed 's/"/""/g')
  umask 077
  mkdir -p "$(dirname "$arquivo")"
  tmp="${arquivo}.tmp"
  if [ -f "$arquivo" ]; then
    grep -v "^\"${papel}\" " "$arquivo" > "$tmp" || true
  else
    : > "$tmp"
  fi
  printf '"%s" "%s"\n' "$papel" "$escaped" >> "$tmp"
  mv "$tmp" "$arquivo"
}
