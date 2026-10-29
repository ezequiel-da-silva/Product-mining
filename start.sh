#!/usr/bin/env bash
set -euo pipefail

start_dockerd() {
  if sudo docker info >/dev/null 2>&1; then
    return 0
  fi
  sudo rm -f /var/run/docker.sock /var/run/docker.pid
  sudo sh -c 'setsid dockerd >/var/log/dockerd.log 2>&1 < /dev/null &'
  for _ in $(seq 1 60); do
    if sudo docker info >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "dockerd did not become ready" >&2
  sudo tail -n 100 /var/log/dockerd.log >&2 || true
  return 1
}

allow_docker_cli() {
  if docker info >/dev/null 2>&1; then
    return 0
  fi
  sudo chmod 666 /var/run/docker.sock
  docker info >/dev/null
}

write_dev_env() {
  if [ -f .env ]; then
    return 0
  fi
  cat > .env <<'EOF'
POSTGRES_USER=spy_admin
POSTGRES_PASSWORD=dev_only_postgres
POSTGRES_DB=mineracao_fb
STREAMLIT_DB_PASSWORD=dev_only_streamlit
PIPELINE_DB_PASSWORD=dev_only_pipeline
LIFECYCLE_DB_PASSWORD=dev_only_lifecycle
REDIS_PASSWORD=dev_only_redis
DASHBOARD_PASSWORD=dev_only_dashboard
FLOWER_BASIC_AUTH=admin:dev_only_flower
CDP_HOST=host.docker.internal
CDP_PORT=9222
CDP_BRIDGE_IP=172.17.0.1
GEMINI_API_KEY=
CLOUDFLARE_R2_ACCOUNT_ID=
CLOUDFLARE_R2_ACCESS_KEY=
CLOUDFLARE_R2_SECRET_KEY=
CLOUDFLARE_R2_BUCKET=minerador-criativos
CLOUDFLARE_R2_PUBLIC_URL=
EOF
}

dotenv_get() {
  key="$1"
  if [ ! -f .env ]; then
    printf ''
    return 0
  fi
  grep -E "^${key}=" .env | tail -n 1 | cut -d= -f2- || true
}

effective_env() {
  key="$1"
  if [ -n "${!key+x}" ]; then
    printf '%s' "${!key}"
    return 0
  fi
  dotenv_get "$key"
}

read_file_secret() {
  file="$1"
  if [ ! -f "$file" ]; then
    printf ''
    return 0
  fi
  sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$file" | tr -d '\r'
}

is_unusable_secret() {
  case "$1" in
    ""|sua_chave_google_ai_studio|seu_account_id|sua_access_key|sua_secret_key|defina_uma_senha_forte_aqui|defina_uma_senha_redis_aqui|https://cdn.seudominio.com|admin:defina_uma_senha_forte_aqui)
      return 0
      ;;
  esac
  return 1
}

validate_env() {
  err=0
  for key in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB STREAMLIT_DB_PASSWORD PIPELINE_DB_PASSWORD LIFECYCLE_DB_PASSWORD REDIS_PASSWORD DASHBOARD_PASSWORD CDP_BRIDGE_IP FLOWER_BASIC_AUTH; do
    val="$(effective_env "$key")"
    if [ -z "$val" ] || is_unusable_secret "$val"; then
      echo "variável obrigatória ausente ou placeholder: ${key}" >&2
      err=1
    fi
  done
  for key in POSTGRES_PASSWORD STREAMLIT_DB_PASSWORD PIPELINE_DB_PASSWORD LIFECYCLE_DB_PASSWORD REDIS_PASSWORD; do
    val="$(effective_env "$key")"
    case "$val" in
      *[@:/#]*)
        echo "${key} contém @, :, / ou # e quebraria a URI de conexão" >&2
        err=1
        ;;
    esac
  done
  flower="$(effective_env FLOWER_BASIC_AUTH)"
  case "$flower" in
    *:*)
      flower_user="${flower%%:*}"
      flower_pass="${flower#*:}"
      if [ -z "$flower_user" ] || [ -z "$flower_pass" ]; then
        echo "FLOWER_BASIC_AUTH deve ser usuario:senha" >&2
        err=1
      fi
      case "$flower_user" in
        *[@:/#]*)
          echo "FLOWER_BASIC_AUTH usuário contém @, :, / ou #" >&2
          err=1
          ;;
      esac
      case "$flower_pass" in
        *[@:/#]*)
          echo "FLOWER_BASIC_AUTH senha contém @, :, / ou #" >&2
          err=1
          ;;
      esac
      ;;
    *)
      echo "FLOWER_BASIC_AUTH deve ser usuario:senha" >&2
      err=1
      ;;
  esac
  return "$err"
}

effective_gemini() {
  file_val="$(read_file_secret secrets/gemini/GEMINI_API_KEY)"
  file_val="$(printf '%s' "$file_val" | tr -d '\n')"
  if [ -n "$file_val" ]; then
    printf '%s' "$file_val"
    return 0
  fi
  effective_env GEMINI_API_KEY
}

wait_healthy() {
  name="$1"
  for _ in $(seq 1 60); do
    status="$(sudo docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null || true)"
    if [ "$status" = "healthy" ]; then
      return 0
    fi
    sleep 2
  done
  echo "${name} did not become healthy (status=${status:-missing})" >&2
  sudo docker logs --tail 80 "$name" >&2 || true
  return 1
}

wait_celery() {
  name="$1"
  for _ in $(seq 1 45); do
    status="$(sudo docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo missing)"
    case "$status" in
      restarting|exited|dead|missing)
        echo "${name} está ${status}" >&2
        sudo docker logs --tail 80 "$name" >&2 || true
        return 1
        ;;
      running)
        count="$(sudo docker inspect -f '{{.RestartCount}}' "$name")"
        if sudo docker logs "$name" 2>&1 | grep -q ' ready'; then
          sleep 2
          count2="$(sudo docker inspect -f '{{.RestartCount}}' "$name")"
          if [ "$count" = "$count2" ]; then
            return 0
          fi
        fi
        ;;
    esac
    sleep 2
  done
  echo "${name} não ficou pronto" >&2
  sudo docker logs --tail 80 "$name" >&2 || true
  return 1
}

apply_schema() {
  allow_docker_cli
  sudo docker compose exec -T postgres sh /docker-entrypoint-initdb.d/zz-readonly-role.sh
  sudo docker compose exec -T postgres sh /docker-entrypoint-initdb.d/zz-pipeline-role.sh
  sh ./infra/apply-migrations.sh
  sudo docker compose exec -T postgres sh /docker-entrypoint-initdb.d/zz-lifecycle-role.sh
}

main() {
  start_dockerd
  mkdir -p secrets/gemini secrets/r2
  write_dev_env
  validate_env

  gemini="$(effective_gemini)"
  services=(postgres redis)
  sudo docker compose up -d "${services[@]}"
  wait_healthy spy_postgres
  wait_healthy spy_redis
  apply_schema

  app_services=(dashboard-ui flower worker-qualification celery-beat)
  if ! is_unusable_secret "$gemini"; then
    app_services+=(worker-ia)
  else
    echo "worker-ia não sobe: GEMINI_API_KEY ausente ou placeholder; secrets/gemini/GEMINI_API_KEY tem precedência sobre o ambiente" >&2
    sudo docker compose stop worker-ia >/dev/null 2>&1 || true
    sudo docker compose rm -f worker-ia >/dev/null 2>&1 || true
  fi
  sudo docker compose up -d "${app_services[@]}"

  for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8501/_stcore/health >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  if ! curl -fsS http://127.0.0.1:8501/_stcore/health >/dev/null 2>&1; then
    echo "dashboard did not become ready" >&2
    sudo docker compose logs --tail 80 dashboard-ui >&2 || true
    exit 1
  fi

  flower="$(effective_env FLOWER_BASIC_AUTH)"
  flower_code=""
  for _ in $(seq 1 30); do
    flower_code="$(curl -s -o /dev/null -w '%{http_code}' -u "$flower" http://127.0.0.1:5555/ || true)"
    if [ "$flower_code" = "200" ]; then
      break
    fi
    sleep 2
  done
  if [ "$flower_code" != "200" ]; then
    echo "flower respondeu HTTP ${flower_code}" >&2
    sudo docker logs --tail 80 spy_flower >&2 || true
    exit 1
  fi

  wait_celery spy_worker_qualification
  wait_celery spy_scheduler
  if ! is_unusable_secret "$gemini"; then
    wait_celery spy_worker_ia
  fi

  echo "start complete"
}

if [ "${CLOUD_AGENT_ENV_LIB:-}" != "1" ]; then
  main "$@"
fi
