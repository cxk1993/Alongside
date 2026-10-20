"""SQLite 在线备份 + 保留 30 天 + --verify 恢复演练（T04 · 步骤 12）。

用法：
    python scripts/backup.py                # 做一次备份 → data/backups/alongside-YYYYMMDD-HHMM.db
    python scripts/backup.py --verify       # 对最新备份做恢复演练（integrity_check）
    python scripts/backup.py --verify <path>

恢复演练是验收项：**没演练过的备份等于没有备份**（总纲雷区 #14）。
每天 03:00 的 APScheduler 调度注册由 T13 负责（本脚本只提供 backup() 纯函数）。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# 直接以脚本方式运行（python scripts/backup.py）时，把 services/api 加进 sys.path，
# 否则 `from core.config import ...` 找不到内核包。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_API_ROOT = Path(__file__).resolve().parent.parent  # services/api/
#: 备份文件名（`alongside-YYYYMMDD-HHMM.db`）。
#: ★ 改名兼容：旧品牌期生成的是 `lifos-*`，必须一并认 —— 否则「清理过期备份」
#: 与「取最新备份」都会对既有备份视而不见（旧备份越积越多、`--verify latest` 找不到）。
_BACKUP_NAME_RE = re.compile(r"^(?:alongside|lifos)-\d{8}-\d{4}\.db$")

#: 改名前的库文件名；仅用于兼容回退（同 db/engine.py）。
_LEGACY_DB_NAME = "lifos.db"


def _default_db_path() -> Path:
    from core.config import get_settings

    p = Path(get_settings().db_path)
    if not p.is_absolute():
        p = _API_ROOT / p
    # ★ 改名兼容：新名库还不存在、旧名库还在 → 备份旧库（别因为改名就备了个空）。
    legacy = p.with_name(_LEGACY_DB_NAME)
    if p.name != _LEGACY_DB_NAME and not p.exists() and legacy.exists():
        return legacy
    return p


def _default_backup_dir() -> Path:
    from core.config import get_settings

    p = Path(get_settings().backup_dir)
    if not p.is_absolute():
        p = _API_ROOT / p
    return p


def backup(db_path: Path, backup_dir: Path) -> Path:
    """sqlite3 .backup API 在线备份（不锁主库，WAL 也安全）。"""
    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在：{db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    dest = backup_dir / f"alongside-{stamp}.db"
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            with dst:
                src.backup(dst)  # 在线备份：主库继续读写
        finally:
            dst.close()
    finally:
        src.close()
    print(f"[backup] 已备份：{dest}（{dest.stat().st_size:,} bytes）")
    return dest


def verify(backup_path: Path) -> bool:
    """恢复演练：打开备份文件，跑一致性检查 + 清点表。"""
    if not backup_path.exists():
        print(f"[verify] 备份不存在：{backup_path}")
        return False
    conn = sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
    finally:
        conn.close()
    ok = integrity == "ok" and quick == "ok"
    print(f"[verify] {backup_path.name}")
    print(f"[verify]   integrity_check = {integrity}")
    print(f"[verify]   quick_check     = {quick}")
    print(f"[verify]   表数量          = {len(tables)}: {tables}")
    print(f"[verify]   结果            = {'PASS' if ok else 'FAIL'}")
    return ok


def cleanup(backup_dir: Path, keep_days: int = 30) -> list[Path]:
    """删除超过保留期的备份（默认 30 天，Settings.backup_keep_days）。"""
    cutoff = time.time() - keep_days * 86400
    removed: list[Path] = []
    if not backup_dir.is_dir():
        return removed
    for f in backup_dir.iterdir():
        if f.is_file() and _BACKUP_NAME_RE.match(f.name) and f.stat().st_mtime < cutoff:
            f.unlink()
            removed.append(f)
            print(f"[cleanup] 已删除过期备份：{f.name}")
    return removed


def latest_backup(backup_dir: Path) -> Path | None:
    if not backup_dir.is_dir():
        return None
    candidates = sorted(f for f in backup_dir.iterdir() if _BACKUP_NAME_RE.match(f.name))
    return candidates[-1] if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/backup.py")
    parser.add_argument("--verify", nargs="?", const="latest", default=None,
                        help="恢复演练：不带参数验最新备份，或指定备份文件路径")
    parser.add_argument("--db", default=None, help="覆盖数据库路径（测试用）")
    parser.add_argument("--dir", default=None, help="覆盖备份目录（测试用）")
    args = parser.parse_args(argv)

    if args.verify is not None:
        if args.verify == "latest":
            target = latest_backup(_default_backup_dir() if not args.dir else Path(args.dir))
            if target is None:
                print("[verify] 没有可验证的备份（先跑一次备份）")
                return 1
        else:
            target = Path(args.verify)
        return 0 if verify(target) else 1

    db_path = Path(args.db) if args.db else _default_db_path()
    backup_dir = Path(args.dir) if args.dir else _default_backup_dir()
    dest = backup(db_path, backup_dir)

    from core.config import get_settings

    cleanup(backup_dir, keep_days=get_settings().backup_keep_days)
    return 0 if verify(dest) else 1


if __name__ == "__main__":
    sys.exit(main())
