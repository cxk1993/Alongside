#!/bin/bash
# MiMo ISSUE-006 post-deploy ledger + modules probe (local API)
set -u
echo "=== POSTCHECK $(date -Is) ==="
echo "--- healthz ---"
curl -sS -m 5 -w "\nhttp=%{http_code}\n" http://127.0.0.1:18000/healthz || true
echo "--- modules ---"
curl -sS -m 8 http://127.0.0.1:18000/api/v1/modules | python3 -c "import sys,json;d=json.load(sys.stdin);print('count',d.get('count'));print('ids',sorted(m.get('id') for m in d.get('modules',[])))" || true
echo "--- ledger ---"
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/home/ubuntu/alongside/data/alongside.db')
n=c.execute("SELECT COUNT(*) FROM app_setting WHERE key LIKE 'migration.%'").fetchone()[0]
print('migration_ledger', n)
rows=c.execute("SELECT key FROM app_setting WHERE key LIKE 'migration.%' ORDER BY key").fetchall()
for r in rows:
    print(' ', r[0])
for t in ('docs_node','docs_revision','docs_fts'):
    print('has', t, c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0])
PY
echo "--- public healthz via nginx ---"
curl -sS -m 8 -w "\nhttp=%{http_code}\n" http://192.0.2.10:18080/healthz || true
echo "=== POSTCHECK END ==="
