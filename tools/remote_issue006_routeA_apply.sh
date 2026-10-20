#!/bin/bash
# ISSUE-006 Route A · preseed 15 applied markers + restart + postcheck
set -u
TS="${1:?ts required}"
API=/home/ubuntu/alongside/services/api
LOG=/home/ubuntu/alongside/api.log
PY=/home/ubuntu/alongside/.venv/bin/python
DB=/home/ubuntu/alongside/data/alongside.db
echo "=== ROUTE_A_APPLY $(date -Is) TS=$TS ==="

echo "--- remote md5 after scp ---"
cd "$API" || exit 1
md5sum \
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
  modules/web/migrations/0001_init.py

echo "--- stop alongside ---"
pids=$(pgrep -f 'uvicorn main:app --host 127.0.0.1 --port 18000' || true)
echo "pids=${pids:-none}"
for p in $pids; do kill -TERM "$p" 2>/dev/null || true; done
sleep 2
left=$(pgrep -f 'uvicorn main:app --host 127.0.0.1 --port 18000' || true)
if [ -n "${left:-}" ]; then
  for p in $left; do kill -9 "$p" 2>/dev/null || true; done
  sleep 1
fi
pgrep -af 'uvicorn main:app --host 127.0.0.1 --port 18000' || echo "alongside_down"

echo "--- preseed ledger Route A ---"
python3 - <<'PY'
import sqlite3, uuid
from datetime import datetime, timezone
keys = [
    "migration.agents.0001_init",
    "migration.calendar.0001_init",
    "migration.calendar.0002_reminder_log",
    "migration.catalog.0001_init",
    "migration.docs.0001_init",
    "migration.docs.0002_fts",
    "migration.finance.0001_init",
    "migration.finance.0002_snapshot",
    "migration.habits.0001_init",
    "migration.health.0001_init",
    "migration.mcp.0001_init",
    "migration.notes.0001_init",
    "migration.review.0001_init",
    "migration.todo.0001_init",
    "migration.web.0001_init",
]
db = "/home/ubuntu/alongside/data/alongside.db"
conn = sqlite3.connect(db)
cur = conn.cursor()
# ensure app_setting exists
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_setting'")
if not cur.fetchone():
    raise SystemExit("FAIL: app_setting missing")
now = datetime.now(timezone.utc).isoformat()
inserted = 0
skipped = 0
for key in keys:
    cur.execute("SELECT id FROM app_setting WHERE key=?", (key,))
    if cur.fetchone():
        skipped += 1
        continue
    cur.execute(
        "INSERT INTO app_setting (id, key, value_json, created_at, updated_at) VALUES (?,?,?,?,?)",
        (uuid.uuid4().hex, key, f'"{now}"', now, now),
    )
    inserted += 1
conn.commit()
n = cur.execute("SELECT COUNT(*) FROM app_setting WHERE key LIKE 'migration.%'").fetchone()[0]
print(f"preseed_inserted={inserted} skipped={skipped} ledger_now={n}")
for row in cur.execute("SELECT key FROM app_setting WHERE key LIKE 'migration.%' ORDER BY key"):
    print(" ", row[0])
conn.close()
if n != 15:
    raise SystemExit(f"FAIL: ledger count {n} != 15")
print("PRESEED_OK ledger=15")
PY

echo "--- restart ---"
cd "$API" || exit 1
setsid "$PY" -m uvicorn main:app --host 127.0.0.1 --port 18000 >> "$LOG" 2>&1 < /dev/null &
echo "spawned $!"
sleep 7
echo "--- after ---"
pgrep -af 'uvicorn main:app --host 127.0.0.1 --port 18000' || echo "NO_UVICORN_AFTER"
curl -sS -m 5 -w "\nhttp=%{http_code}\n" http://127.0.0.1:18000/healthz || true
echo "--- modules ---"
curl -sS -m 8 http://127.0.0.1:18000/api/v1/modules | python3 -c "import sys,json;d=json.load(sys.stdin);print('count',d.get('count'));print('ids',sorted(m.get('id') for m in d.get('modules',[])))" || true
echo "--- ledger + fts after restart ---"
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/home/ubuntu/alongside/data/alongside.db')
print('migration_ledger', c.execute("SELECT COUNT(*) FROM app_setting WHERE key LIKE 'migration.%'").fetchone()[0])
try:
    print('docs_fts', c.execute("SELECT COUNT(*) FROM docs_fts").fetchone()[0])
except Exception as e:
    print('docs_fts ERR', e)
try:
    print('docs_node_doc', c.execute("SELECT COUNT(*) FROM docs_node WHERE kind='doc'").fetchone()[0])
    print('docs_node_all', c.execute("SELECT COUNT(*) FROM docs_node").fetchone()[0])
except Exception as e:
    print('docs_node ERR', e)
PY
echo "--- migrations log lines ---"
grep -a -n 'kernel.plugins.migrations\|插件迁移对账\|migrations' "$LOG" | tail -n 40 || true
echo "--- api.log tail ---"
tail -n 50 "$LOG" | tr -cd '\11\12\15\40-\176\200-\377' | tail -n 50
echo "=== ROUTE_A_APPLY END ==="
