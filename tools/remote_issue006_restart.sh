#!/bin/bash
# MiMo ISSUE-006 restart API (two-step; call AFTER kill)
set -euo pipefail
API=/home/ubuntu/alongside/services/api
LOG=/home/ubuntu/alongside/api.log
PY=/home/ubuntu/alongside/.venv/bin/python
cd "$API"
echo "=== RESTART $(date -Is) ==="
echo "pre_port_check:"
ss -lntp 2>/dev/null | grep 18000 || netstat -lntp 2>/dev/null | grep 18000 || echo "port 18000 free or unknown"
setsid "$PY" -m uvicorn main:app --host 127.0.0.1 --port 18000 >> "$LOG" 2>&1 < /dev/null &
echo "spawned_pid=$!"
sleep 4
echo "--- post uvicorn ---"
pgrep -af 'uvicorn main:app' || echo "NO_UVICORN_AFTER"
echo "--- healthz local ---"
curl -sS -m 5 -o /tmp/hz.json -w "healthz_http=%{http_code}\n" http://127.0.0.1:18000/healthz || true
cat /tmp/hz.json 2>/dev/null || true
echo
echo "--- startup log scan ---"
tail -n 80 "$LOG"
echo "--- migrations lines ---"
grep -n 'kernel.plugins.migrations\|插件迁移对账\|migrations' "$LOG" | tail -n 30 || true
echo "=== RESTART END ==="
