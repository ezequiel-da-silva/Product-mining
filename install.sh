#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  -o Dpkg::Options::=--force-confdef \
  -o Dpkg::Options::=--force-confold \
  --no-install-recommends \
  ca-certificates \
  curl \
  git \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  build-essential \
  libpq-dev \
  docker.io \
  docker-compose-v2 \
  fuse-overlayfs \
  iptables

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt 'ruff==0.3.5' pre-commit
if ! git config --get core.hooksPath >/dev/null 2>&1; then
  .venv/bin/pre-commit install
fi

install_wrapper() {
  tool="$1"
  sudo rm -f "/usr/local/bin/$tool"
  sudo tee "/usr/local/bin/$tool" >/dev/null <<EOF
#!/bin/sh
set -eu
tool="$tool"
if root=\$(git rev-parse --show-toplevel 2>/dev/null); then
  if [ -x "\$root/.venv/bin/\$tool" ]; then
    exec "\$root/.venv/bin/\$tool" "\$@"
  fi
fi
dir=\$(pwd)
while [ "\$dir" != "/" ]; do
  if [ -x "\$dir/.venv/bin/\$tool" ]; then
    exec "\$dir/.venv/bin/\$tool" "\$@"
  fi
  dir=\$(dirname "\$dir")
done
echo "\$tool: .venv not found from \$(pwd)" >&2
exit 127
EOF
  sudo chmod 755 "/usr/local/bin/$tool"
}

install_wrapper pytest
install_wrapper ruff
install_wrapper pre-commit

sudo mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
  sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{
  "storage-driver": "fuse-overlayfs",
  "iptables": true
}
JSON
fi

if [ -x /usr/sbin/iptables-legacy ] && [ -x /usr/sbin/ip6tables-legacy ]; then
  sudo update-alternatives --set iptables /usr/sbin/iptables-legacy
  sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy
fi

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

stop_dockerd() {
  if [ ! -f /var/run/docker.pid ]; then
    if ! sudo docker info >/dev/null 2>&1; then
      sudo rm -f /var/run/docker.sock
    fi
    return 0
  fi
  pid="$(sudo cat /var/run/docker.pid)"
  if ! sudo kill -0 "$pid" 2>/dev/null; then
    sudo rm -f /var/run/docker.sock /var/run/docker.pid
    return 0
  fi
  sudo kill "$pid" || true
  for _ in $(seq 1 30); do
    if ! sudo kill -0 "$pid" 2>/dev/null; then
      sudo rm -f /var/run/docker.sock /var/run/docker.pid
      return 0
    fi
    sleep 1
  done
  echo "dockerd pid ${pid} still running; refusing to remove /var/run/docker.sock" >&2
  return 1
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

mkdir -p secrets/gemini secrets/r2
write_dev_env
start_dockerd
sudo docker compose pull postgres redis flower
sudo docker compose build dashboard-ui worker-qualification worker-ia celery-beat
stop_dockerd
sync
echo "install complete"
