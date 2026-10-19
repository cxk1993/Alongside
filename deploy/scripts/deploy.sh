#!/usr/bin/env bash
# Alongside 一键部署（幂等，可重复执行）
# 用法：在服务器上 /home/ubuntu/lifeos 目录执行
#   sudo bash deploy/scripts/deploy.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

echo "[deploy] project root: $ROOT"
if [[ ! -f .env ]]; then
  echo "[deploy] 缺少 .env —— 先执行 deploy/scripts/gen-env.sh"
  exit 1
fi
if [[ ! -f apps/web/dist/index.html ]]; then
  echo "[deploy] 缺少 apps/web/dist —— 请在开发机 build 后上传"
  exit 1
fi

# BeeCount 同机：容器访问宿主 8870
# docker0 常见为 172.17.0.1；若不通可改 host.docker.internal
export BEECOUNT_BASE_URL="${BEECOUNT_BASE_URL:-http://172.17.0.1:8870}"

echo "[deploy] docker compose build + up"
cd deploy
sudo docker compose -f docker-compose.prod.yml build
sudo docker compose -f docker-compose.prod.yml up -d
cd "$ROOT"

echo "[deploy] wait healthz"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:18000/healthz >/dev/null 2>&1; then
    echo "[deploy] api healthz OK"
    break
  fi
  sleep 2
  if [[ $i -eq 30 ]]; then
    echo "[deploy] api healthz timeout"
    sudo docker compose -f deploy/docker-compose.prod.yml logs --tail=80 api || true
    exit 1
  fi
done

curl -fsS http://127.0.0.1:18080/healthz && echo
echo "[deploy] web http://0.0.0.0:18080  api http://127.0.0.1:18000"
echo "[deploy] done"
