#!/bin/bash
# MiMo ISSUE-006 production precheck
set -u
echo "=== PRECHECK $(date -Is) ==="
echo "HOST=$(hostname)"
echo "USER=$(whoami)"
echo "--- uvicorn ---"
pgrep -af 'uvicorn' || echo "NO_UVICORN"
echo "--- files ---"
ls -la /home/ubuntu/alongside/data/alongside.db 2>&1
ls -la /home/ubuntu/alongside/services/api/core/plugins/migrations.py 2>&1
ls -la /home/ubuntu/alongside/services/api/core/app.py 2>&1
ls -la /home/ubuntu/alongside/.venv/bin/python 2>&1
echo "--- db ledger ---"
python3 - <<'PY'
import sqlite3
p='/home/ubuntu/alongside/data/alongside.db'
c=sqlite3.connect(p)
print('integrity', c.execute('PRAGMA integrity_check').fetchone()[0])
print('tables', c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0])
print('migration_ledger', c.execute("SELECT COUNT(*) FROM app_setting WHERE key LIKE 'migration.%'").fetchone()[0])
for t in ('docs_node','docs_revision','docs_fts','app_setting','agents_task'):
    n=c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
    print(f'table_{t}', n)
rows=c.execute("SELECT key FROM app_setting WHERE key LIKE 'migration.%' ORDER BY key").fetchall()
print('ledger_keys', len(rows))
for r in rows[:20]:
    print(' ', r[0])
PY
echo "--- api.log tail ---"
tail -n 40 /home/ubuntu/alongside/api.log 2>&1 || true
echo "=== PRECHECK END ==="
