#!/bin/bash
# MiMo ISSUE-006 kill Alongside 18000 then restart
set -u
API=/home/ubuntu/lifeos/services/api
LOG=/home/ubuntu/lifeos/api.log
PY=/home/ubuntu/lifeos/.venv/bin/python
echo "=== KILL+RESTART $(date -Is) ==="
echo "--- before ---"
pgrep -af 'uvicorn main:app' || echo "none_before"
# kill only lifeos main:app on 18000
pids=$(pgrep -f 'uvicorn main:app --host 127.0.0.1 --port 18000' || true)
echo "target_pids=${pids:-none}"
for p in $pids; do
  echo "kill TERM $p"
  kill -TERM "$p" 2>/dev/null || true
done
sleep 2
left=$(pgrep -f 'uvicorn main:app --host 127.0.0.1 --port 18000' || true)
if [ -n "${left:-}" ]; then
  echo "still_alive=$left -> KILL9"
  for p in $left; do kill -9 "$p" 2>/dev/null || true; done
  sleep 1
fi
pgrep -af 'uvicorn main:app --host 127.0.0.1 --port 18000' || echo "lifeos_down"
echo "--- restart ---"
cd "$API" || exit 1
setsid "$PY" -m uvicorn main:app --host 127.0.0.1 --port 18000 >> "$LOG" 2>&1 < /dev/null &
echo "spawned $!"
sleep 6
echo "--- after ---"
pgrep -af 'uvicorn main:app --host 127.0.0.1 --port 18000' || echo "NO_UVICORN_AFTER"
echo "--- healthz ---"
curl -sS -m 5 -w "\nhttp=%{http_code}\n" http://127.0.0.1:18000/healthz || true
echo "--- log tail ---"
tail -n 60 "$LOG"
echo "--- migrations grep ---"
grep -n 'kernel.plugins.migrations\|插件迁移对账\|迁移' "$LOG" | tail -n 40 || true
echo "=== END ==="
