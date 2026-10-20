#!/bin/bash
# MiMo ISSUE-006 rollback + evidence
set -u
API=/home/ubuntu/alongside/services/api
LOG=/home/ubuntu/alongside/api.log
PY=/home/ubuntu/alongside/.venv/bin/python
TS=20260920-105200
echo "=== ROLLBACK $(date -Is) ==="
cp "$API/core/plugins/migrations.py.bak-${TS}" "$API/core/plugins/migrations.py"
cp "$API/core/app.py.bak-${TS}" "$API/core/app.py"
ls -la "$API/core/plugins/migrations.py" "$API/core/app.py"
md5sum "$API/core/plugins/migrations.py" "$API/core/app.py"
echo "--- agents migration local on server ---"
ls -la "$API/modules/agents/migrations/" 2>&1
echo "----- agents 0001 head -----"
head -n 80 "$API/modules/agents/migrations/0001_init.py" 2>&1
echo "----- grep sys.modules -----"
grep -n 'sys.modules\|_load_models\|importlib' "$API/modules/agents/migrations/0001_init.py" 2>&1 || true
echo "--- restart with old kernel ---"
pids=$(pgrep -f 'uvicorn main:app --host 127.0.0.1 --port 18000' || true)
for p in $pids; do kill -9 "$p" 2>/dev/null || true; done
sleep 1
cd "$API" || exit 1
setsid "$PY" -m uvicorn main:app --host 127.0.0.1 --port 18000 >> "$LOG" 2>&1 < /dev/null &
echo "spawned $!"
sleep 6
pgrep -af 'uvicorn main:app --host 127.0.0.1 --port 18000' || echo "NO_UVICORN"
curl -sS -m 5 -w "\nhttp=%{http_code}\n" http://127.0.0.1:18000/healthz || true
echo "--- ledger after failed attempt ---"
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/home/ubuntu/alongside/data/alongside.db')
print('migration_ledger', c.execute("SELECT COUNT(*) FROM app_setting WHERE key LIKE 'migration.%'").fetchone()[0])
print('agents_task_exists', c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='agents_task'").fetchone()[0])
PY
echo "=== ROLLBACK END ==="
