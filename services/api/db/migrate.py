"""迁移执行器（T04 · 步骤 5）。

两种迁移，一个入口 `python -m db.migrate <cmd>`：

  upgrade head      ① alembic 升到 head（内核五表）
                    ② 扫描各插件的迁移目录、按文件名序执行（插件表）——
                       目录解析口径与内核一致（manifest 声明优先、约定位兜底），
                       见 `discover_plugin_migrations`
  downgrade base    反向：插件迁移逐个 downgrade（倒序）→ alembic 回 base
  status            内核当前版本 + 各插件已执行版本

插件迁移的执行记录存 app_setting：key = "migration.<plugin_id>.<version>"，
value_json = 执行时间（UTC ISO8601）。迁移只增不改——已执行的不重复跑。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from alembic import command
from alembic.config import Config
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
# 插件迁移（目录解析口径与 core.plugins.migrations._migration_dir 一致）
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


def _plugin_root_for(migration_path: Path) -> Path | None:
    """从迁移文件往上找插件根（带 manifest.json 的那层）。"""
    for parent in migration_path.parents:
        if (parent / "manifest.json").is_file():
            return parent
    return None


def _ensure_models_loaded(plugin_id: str, migration_path: Path) -> None:
    """跑迁移前，先把插件 models.py 以**包路径** import 一次。

    ★ 为什么（层 0.3 修复暴露出来的既有隐患）
    ----------------------------------------
    迁移文件被内核/CLI 按文件路径 exec，它们各自 `spec_from_file_location`
    加载 `models.py`；同一插件的多个迁移若用了**不同的模块名**（calendar 的
    0001 = `calendar_models`、0002 = `calendar_models_0002`，finance/todo 同款），
    就会把同一批表在 SQLModel 的**共享 metadata** 上定义两遍 ⇒
    `InvalidRequestError: Table 'calendar_event' is already defined`。
    迁移文件自己的守卫只看 `sys.modules` 里的 `modules.<id>.models`（应用启动时
    create_app 会 import 它），CLI 路径没人 import ⇒ 守卫落空。
    ⇒ 这里补上「先以包路径 import」这一步，让各迁移文件命中同一模块对象。
      这也正是服务启动路径的既有事实（create_app → 插件 router → models）。

    没有 models.py / 无 manifest 的插件直接跳过；import 失败也不在此吞成致命错
    （迁移文件自己会再试一次，真错会在那里带着真实栈暴露）。
    """
    plugin_root = _plugin_root_for(migration_path)
    if plugin_root is None or not (plugin_root / "models.py").is_file():
        return
    mod_name = f"{plugin_root.parent.name}.{plugin_id}.models"
    if mod_name in sys.modules:
        return
    try:
        importlib.import_module(mod_name)
    except Exception:  # noqa: BLE001 — 导入失败交给迁移文件自己的加载路径报错
        return


def _load_migration_file(path: Path, plugin_id: str, version: str) -> ModuleType:
    """按路径加载迁移模块（插件迁移不是包，必须按文件加载）。"""
    _ensure_models_loaded(plugin_id, path)
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


def _plugin_manifest(plugin_dir: Path) -> dict[str, Any] | None:
    """容错读插件 manifest.json：缺失 / 非法 JSON / 非对象 ⇒ None（按「未声明」处理）。

    ★ 为什么容错而不抛
    ------------------
    `python -m db.migrate upgrade head` 是部署脚本入口，扫描目录里混进一个没有
    manifest 的目录（`__pycache__`、未完工的插件、被误拷进来的东西）不该让整条
    迁移链崩掉。manifest 读不出来 = 没声明迁移目录 ⇒ 走约定位兜底（与内核
    `_migration_dir` 对「未声明」的语义一致）。真正的错（声明了目录却不存在）
    仍由 `_migration_dir` 抛 MigrationError，不在这里吞掉。
    """
    manifest_path = plugin_dir / "manifest.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # 不存在 / 读不了 / 非法 JSON
        return None
    return data if isinstance(data, dict) else None


def _kernel_migration_dir(info: SimpleNamespace) -> Path:
    """复用内核的迁移目录解析（唯一真相源），不在本文件另写一套规则。

    ★ 为什么是函数内 import
    ----------------------
    `db/` 是数据层、`core/` 是内核层，模块级 import core 会把分层方向倒过来，
    且 `core.plugins.__init__` 会连带拉起 manager/db.models —— 部署 CLI 没必要
    为此付出导入成本。解析口径本身必须只有一处，所以这里**直接调**
    `core.plugins.migrations._migration_dir`（而不是复制规则）：规则再改一次时
    两边不会分叉。
    """
    from core.plugins.migrations import _migration_dir

    return _migration_dir(info)


def discover_plugin_migrations(modules_dir: Path | None = None) -> list[tuple[str, str, Path]]:
    """[(plugin_id, version, path)]，按插件 id、文件名序排好。

    ★ 目录解析口径（与 core.plugins.migrations._migration_dir 完全一致，直接复用）
    -----------------------------------------------------------------------------
    1. manifest 声明了 `migrations`（非空字符串）⇒ 按声明相对插件根解析；
       声明了却不存在 ⇒ 抛 MigrationError（不静默退回约定位：否则「manifest
       说的不算数」又活过来，作者以为自己声明的迁移跑了，其实跑的是别的）。
    2. 未声明（null / 缺失 / manifest 读不出来）⇒ 约定探测：
       `api/migrations/` 优先、`migrations/` 兜底。

    ★ 为什么必须这样（历史缺陷）
    ---------------------------
    早先本函数硬编码只扫 `modules/<id>/api/migrations/`，而 15 个内置插件的迁移
    全在 `modules/<id>/migrations/`（manifest 也写的 `"migrations"`）⇒ 命中数 0，
    `upgrade head` 对内置插件一条都不跑，且静默退出 0。现在按 manifest 走，
    `modules/` 与第三方 `plugins/` 两种布局一视同仁。
    """
    root = modules_dir or _MODULES_DIR
    found: list[tuple[str, str, Path]] = []
    if not root.is_dir():
        return found
    for plugin_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if plugin_dir.name.startswith("__"):  # __pycache__ 之类，不是插件
            continue
        info = SimpleNamespace(
            id=plugin_dir.name,
            directory=plugin_dir,
            manifest=_plugin_manifest(plugin_dir),
        )
        mig_dir = _kernel_migration_dir(info)
        if not mig_dir.is_dir():
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
