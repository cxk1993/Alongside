#!/bin/bash
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/home/ubuntu/alongside/data/alongside.db')
print('ledger', c.execute("SELECT COUNT(*) FROM app_setting WHERE key LIKE 'migration.%'").fetchone()[0])
print('fts', c.execute("SELECT COUNT(*) FROM docs_fts").fetchone()[0])
print('node_doc', c.execute("SELECT COUNT(*) FROM docs_node WHERE kind='doc'").fetchone()[0])
print('node_all', c.execute("SELECT COUNT(*) FROM docs_node").fetchone()[0])
PY
echo "--- startup markers ---"
grep -a 'Application startup\|kernel.plugins.migrations\|插件迁移对账\|Started server process' /home/ubuntu/alongside/api.log | tail -n 25
echo "--- public ---"
curl -sS -m 8 -w "\nhttp=%{http_code}\n" http://192.0.2.10:18080/healthz
