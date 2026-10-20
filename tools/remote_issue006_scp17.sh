#!/bin/bash
set -euo pipefail
KEY="$HOME/.ssh/deploy-key.pem"
HOST="ubuntu@192.0.2.10"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${SRC:-$REPO_ROOT/services/api}"
DST="$HOST:/home/ubuntu/alongside/services/api"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no -o BatchMode=yes)

upload() {
  local rel="$1"
  echo "UPLOAD $rel"
  scp "${OPTS[@]}" "$SRC/$rel" "$DST/$rel"
}

# B15 module migrations first
upload modules/agents/migrations/0001_init.py
upload modules/calendar/migrations/0001_init.py
upload modules/calendar/migrations/0002_reminder_log.py
upload modules/catalog/migrations/0001_init.py
upload modules/docs/migrations/0001_init.py
upload modules/docs/migrations/0002_fts.py
upload modules/finance/migrations/0001_init.py
upload modules/finance/migrations/0002_snapshot.py
upload modules/habits/migrations/0001_init.py
upload modules/health/migrations/0001_init.py
upload modules/mcp/migrations/0001_init.py
upload modules/notes/migrations/0001_init.py
upload modules/review/migrations/0001_init.py
upload modules/todo/migrations/0001_init.py
upload modules/web/migrations/0001_init.py
# A2 kernel
upload core/plugins/migrations.py
upload core/app.py

echo "=== remote md5 ==="
ssh "${OPTS[@]}" "$HOST" "cd /home/ubuntu/alongside/services/api && md5sum \
  core/plugins/migrations.py core/app.py \
  modules/agents/migrations/0001_init.py \
  modules/calendar/migrations/0001_init.py \
  modules/calendar/migrations/0002_reminder_log.py \
  modules/catalog/migrations/0001_init.py \
  modules/docs/migrations/0001_init.py \
  modules/docs/migrations/0002_fts.py \
  modules/finance/migrations/0001_init.py \
  modules/finance/migrations/0002_snapshot.py \
  modules/habits/migrations/0001_init.py \
  modules/health/migrations/0001_init.py \
  modules/mcp/migrations/0001_init.py \
  modules/notes/migrations/0001_init.py \
  modules/review/migrations/0001_init.py \
  modules/todo/migrations/0001_init.py \
  modules/web/migrations/0001_init.py"
echo "SCP_SCRIPT_DONE"
