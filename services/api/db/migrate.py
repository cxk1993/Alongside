"""迁移执行器（T04 · 步骤 5）。

两种迁移，一个入口 `python -m db.migrate <cmd>`：

  upgrade head      ① alembic 升到 head（内核五表）
                    ② 扫描 modules/<id>/api/migrations/*.py 按文件名序执行（插件表）
  downgrade base    反向：插件迁移逐个 downgrade（倒序）→ alembic 回 base
  status            内核当前版本 + 各插件已执行版本

插件迁移的执行记录存 app_setting：key = "migration.<plugin_id>.<version>"，
value_json = 执行时间（UTC ISO8601）。迁移只增不改——已执行的不重复跑。
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

from alembic import command
from db.engine import init_engine

_API_ROOT = Path(__file__).resolve().parent.parent  # services/api/
_ALEMBIC_DIR = _API_ROOT / "alembic"
_MODULES_DIR = _API_ROOT / "modules"


# ---------------------------------------------------------------------------
# alembic（内核表）
# ---------------------------------------------------------------------------
def _alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


def _alembic_upgrade(engine: Engine, rev: str) -> None:
    command.upgrade(_alembic_config(engine), rev)


def _alembic_downgrade(engine: Engine, rev: str) -> None:
    command.downgrade(_alembic_config(engine), rev)


# ---------------------------------------------------------------------------
# 插件迁移（modules/<id>/api/migrations/*.py）
# ---------------------------------------------------------------------------
def _record_key(plugin_id: str, version: str) -> str:
    return f"migration.{plugin_id}.{version}"


def _recorded_versions(engine: Engine) -> dict[str, str]:
    """已执行的插件迁移 {key: executed_at}。app_setting 不存在（未迁移）时返回空。"""
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='app_setting'")
        ).fetchone()
        if not exists:
            return {}
        rows = conn.execute(
            text("SELECT key, value_json FROM app_setting WHERE key LIKE 'migration.%'")
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def _record(engine: Engine, plugin_id: str, version: str) -> None:
    from db.base import utcnow

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_setting (id, key, value_json, created_at, updated_at) "
                "VALUES (:id, :key, :value_json, :now, :now)"
            ),
            {
                "id": uuid.uuid4().hex,
                "key": _record_key(plugin_id, version),
                "value_json": f'"{utcnow().isoformat()}"',
                "now": utcnow().isoformat(),
            },
        )


def _unrecord(engine: Engine, plugin_id: str, version: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM app_setting WHERE key = :key"),
            {"key": _record_key(plugin_id, version)},
        )


def _load_migration_file(path: Path, plugin_id: str, version: str) -> ModuleType:
    """按路径加载迁移模块（插件迁移不是包，必须按文件加载）。"""
    name = f"plugin_migration_{plugin_id}_{version}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载迁移文件：{path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    for fn in ("upgrade", "downgrade"):
        if not callable(getattr(mod, fn, None)):
            raise RuntimeError(f"迁移文件 {path} 缺少 {fn}() 函数")
    return mod


def _plugin_migration_dir(plugin_dir: Path) -> Path | None:
    """插件迁移目录：内置插件在模块根 `migrations/`，第三方插件在 `api/migrations/`。

    ★ 2026-11-13 修复：这里原先只认 `api/migrations`（第三方布局），
      于是 `python -m db.migrate upgrade head` **从不执行内置插件的迁移**
      （新建的内置插件表永远建不出来，只能靠服务启动时的 lifespan 对账兜底）。
    """
    for candidate in (plugin_dir / "migrations", plugin_dir / "api" / "migrations"):
        if candidate.is_dir():
            return candidate
    return None


def discover_plugin_migrations(modules_dir: Path | None = None) -> list[tuple[str, str, Path]]:
    """[(plugin_id, version, path)]，按插件 id、文件名序排好。"""
    root = modules_dir or _MODULES_DIR
    found: list[tuple[str, str, Path]] = []
    if not root.is_dir():
        return found
    for plugin_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        mig_dir = _plugin_migration_dir(plugin_dir)
        if mig_dir is None:
            continue
        for path in sorted(mig_dir.glob("*.py")):
            if path.name.startswith("__"):
                continue
            found.append((plugin_dir.name, path.stem, path))
    return found


def run_plugin_migrations(
    engine: Engine, modules_dir: Path | None = None, *, dry_run: bool = False
) -> list[str]:
    """执行所有未跑过的插件迁移，返回本次执行的 "<plugin>/<version>" 列表。"""
    done = _recorded_versions(engine)
    executed: list[str] = []
    for plugin_id, version, path in discover_plugin_migrations(modules_dir):
        key = _record_key(plugin_id, version)
        if key in done:
            continue
        if dry_run:
            executed.append(f"{plugin_id}/{version} (dry-run)")
            continue
        mod = _load_migration_file(path, plugin_id, version)
        up: Any = mod.upgrade
        up(engine)
        _record(engine, plugin_id, version)
        executed.append(f"{plugin_id}/{version}")
        print(f"[migrate] 插件迁移已执行：{plugin_id}/{version}")
    return executed


def downgrade_plugin_migrations(
    engine: Engine, modules_dir: Path | None = None
) -> list[str]:
    """倒序执行已记录的插件迁移 downgrade，并清除记录。"""
    done = _recorded_versions(engine)
    rolled_back: list[str] = []
    for plugin_id, version, path in reversed(discover_plugin_migrations(modules_dir)):
        key = _record_key(plugin_id, version)
        if key not in done:
            continue
        mod = _load_migration_file(path, plugin_id, version)
        down: Any = mod.downgrade
        down(engine)
        _unrecord(engine, plugin_id, version)
        rolled_back.append(f"{plugin_id}/{version}")
        print(f"[migrate] 插件迁移已回滚：{plugin_id}/{version}")
    return rolled_back


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _upgrade_head(modules_dir: Path | None = None) -> None:
    engine = init_engine()
    _alembic_upgrade(engine, "head")
    run_plugin_migrations(engine, modules_dir)
    print("[migrate] upgrade head 完成（内核 + 插件）")


def _downgrade_base(modules_dir: Path | None = None) -> None:
    engine = init_engine()
    downgrade_plugin_migrations(engine, modules_dir)
    _alembic_downgrade(engine, "base")
    print("[migrate] downgrade base 完成（内核 + 插件）")


def _status() -> None:
    engine = init_engine()
    cfg = _alembic_config(engine)
    print("[migrate] 内核（alembic）当前版本：")
    command.current(cfg)
    done = _recorded_versions(engine)
    print(f"[migrate] 插件迁移已执行 {len(done)} 项：")
    for key in sorted(done):
        print(f"  - {key} @ {done[key]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m db.migrate")
    parser.add_argument("cmd", choices=["upgrade", "downgrade", "status"])
    parser.add_argument("rev", nargs="?", default="head", help="upgrade/downgrade 的目标版本")
    parser.add_argument("--modules-dir", default=None, help="覆盖插件扫描目录（测试用）")
    args = parser.parse_args(argv)
    modules_dir = Path(args.modules_dir) if args.modules_dir else None

    if args.cmd == "upgrade":
        rev = args.rev or "head"
        if rev != "head":
            parser.error("目前只支持 upgrade head（插件迁移按序全量执行）")
        _upgrade_head(modules_dir)
    elif args.cmd == "downgrade":
        if args.rev not in ("base", None):
            parser.error("目前只支持 downgrade base")
        _downgrade_base(modules_dir)
    else:
        _status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
