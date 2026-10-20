#!/bin/bash
# ISSUE-006 Route A · precheck + backup
set -euo pipefail
TS="${1:?ts required}"
API=/home/ubuntu/alongside/services/api
DB=/home/ubuntu/alongside/data/alongside.db
echo "=== PRECHECK $(date -Is) TS=$TS ==="
pgrep -af 'uvicorn main:app --host 127.0.0.1 --port 18000' || echo "NO_ALONGSIDE_PID"
curl -sS -m 5 -w "\nhealthz_http=%{http_code}\n" http://127.0.0.1:18000/healthz || true
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/home/ubuntu/alongside/data/alongside.db')
print('integrity', c.execute('PRAGMA integrity_check').fetchone()[0])
print('db_size', __import__('os').path.getsize('/home/ubuntu/alongside/data/alongside.db'))
print('migration_ledger', c.execute("SELECT COUNT(*) FROM app_setting WHERE key LIKE 'migration.%'").fetchone()[0])
print('docs_fts', c.execute("SELECT COUNT(*) FROM docs_fts").fetchone()[0] if c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='docs_fts'").fetchone()[0] else 'NO_TABLE')
print('docs_node_doc', c.execute("SELECT COUNT(*) FROM docs_node WHERE kind='doc'").fetchone()[0] if c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='docs_node'").fetchone()[0] else 'NO_TABLE')
print('docs_node_all', c.execute("SELECT COUNT(*) FROM docs_node").fetchone()[0] if c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='docs_node'").fetchone()[0] else 'NO_TABLE')
PY
echo "--- backup ---"
cp "$DB" "/home/ubuntu/alongside/data/alongside.db.bak-issue006-${TS}"
python3 - <<PY
import sqlite3,os
p="/home/ubuntu/alongside/data/alongside.db.bak-issue006-${TS}"
print("backup_integrity", sqlite3.connect(p).execute("PRAGMA integrity_check").fetchone()[0])
print("backup_size", os.path.getsize(p))
PY
# backup target files if present
for rel in \
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
do
  f="$API/$rel"
  if [ -f "$f" ]; then
    cp "$f" "$f.bak-${TS}"
    echo "bak $rel"
  else
    mkdir -p "$(dirname "$f")"
    echo "missing_will_create $rel"
  fi
done
ls -la "/home/ubuntu/alongside/data/alongside.db.bak-issue006-${TS}"
echo "=== PRECHECK_BACKUP END ==="
