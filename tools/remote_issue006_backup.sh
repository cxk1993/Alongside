#!/bin/bash
# MiMo ISSUE-006 backup + stamp
set -euo pipefail
TS="${1:?usage: remote_issue006_backup.sh YYYYMMDD-HHMMSS}"
DB=/home/ubuntu/alongside/data/alongside.db
BAK=/home/ubuntu/alongside/data/alongside.db.bak-issue006-${TS}
API=/home/ubuntu/alongside/services/api
echo "=== BACKUP TS=${TS} $(date -Is) ==="
if [ ! -f "$DB" ]; then
  echo "FAIL: missing $DB"
  exit 1
fi
cp "$DB" "$BAK"
python3 - <<PY
import sqlite3
c=sqlite3.connect("$BAK")
print("backup_integrity", c.execute("PRAGMA integrity_check").fetchone()[0])
print("backup_size", __import__("os").path.getsize("$BAK"))
PY
mkdir -p "$API/core/plugins"
cp "$API/core/plugins/migrations.py" "$API/core/plugins/migrations.py.bak-${TS}"
cp "$API/core/app.py" "$API/core/app.py.bak-${TS}"
ls -la "$BAK" "$API/core/plugins/migrations.py.bak-${TS}" "$API/core/app.py.bak-${TS}"
echo "BACKUP_OK bak_issue006_${TS}"
