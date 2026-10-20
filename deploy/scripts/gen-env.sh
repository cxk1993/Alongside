#!/usr/bin/env bash
# 在服务器生成 Alongside .env（不打印明文密钥到已提交文件）
# 用法：bash deploy/scripts/gen-env.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENVF="$ROOT/.env"
if [[ -f "$ENVF" ]]; then
  echo "[gen-env] 已存在 $ENVF ，跳过（如需轮换请手动编辑）"
  exit 0
fi
PY="${PYTHON:-python3}"
ADMIN_PW="${ALONGSIDE_ADMIN_PASSWORD:-${LIFEOS_ADMIN_PASSWORD:-}}"
if [[ -z "$ADMIN_PW" ]]; then
  ADMIN_PW="$($PY -c 'import secrets;print(secrets.token_urlsafe(16))')"
  echo "[gen-env] 生成临时管理员密码（请立即保存并改掉）：$ADMIN_PW"
fi
read -r SECRET_KEY TOTP_SECRET HASH <<<"$($PY - <<PY
from argon2 import PasswordHasher
import secrets, base64
print(secrets.token_hex(32), end=' ')
print(base64.b32encode(secrets.token_bytes(20)).decode().rstrip('='), end=' ')
print(PasswordHasher().hash("$ADMIN_PW"))
PY
)"
# 若上面拆分失败则用分步
if [[ -z "${HASH:-}" ]]; then
  SECRET_KEY=$($PY -c 'import secrets;print(secrets.token_hex(32))')
  TOTP_SECRET=$($PY -c 'import secrets,base64;print(base64.b32encode(secrets.token_bytes(20)).decode().rstrip("="))')
  HASH=$($PY -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('''$ADMIN_PW'''))")
fi
BEE_TOKEN="${BEECOUNT_MCP_TOKEN:-}"
cat >"$ENVF" <<EOF
APP_ENV=production
TZ=Asia/Shanghai
SECRET_KEY=$SECRET_KEY
JWT_ACCESS_MINUTES=30
JWT_REFRESH_DAYS=14
ADMIN_PASSWORD_HASH=$HASH
TOTP_SECRET=$TOTP_SECRET
DB_PATH=./data/alongside.db
BACKUP_DIR=./data/backups
CORS_ALLOW_ORIGINS=*
FINANCE_UPSTREAM=mcp
BEECOUNT_BASE_URL=http://172.17.0.1:8870
BEECOUNT_MCP_TOKEN=$BEE_TOKEN
REVIEW_UPSTREAM=mock
WORK_REVIEW_BASE_URL=http://127.0.0.1:49996
WORK_REVIEW_TOKEN=
WORK_REVIEW_BRIDGE=false
DASHBOARD_SELF_BASE=http://127.0.0.1:8000
EOF
chmod 600 "$ENVF"
mkdir -p "$ROOT/data"
echo "[gen-env] wrote $ENVF"
echo "[gen-env] ADMIN password is only shown when generated above — save it."
