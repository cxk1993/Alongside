"""db.migrate.discover_plugin_migrations 的插件迁移目录解析口径（层 0.3 回归）。

背景缺陷：`discover_plugin_migrations()` 早先硬编码只扫
`modules/<id>/api/migrations/`，而 15 个内置插件的迁移全在
`modules/<id>/migrations/`（manifest 也写 `"migrations"`）⇒ 命中 0 条，
`python -m db.migrate upgrade head` 对内置插件一条都不跑，且静默退出 0。

本文件锁定两件事：
1. 口径与内核 `core.plugins.migrations._migration_dir` 一致：
   manifest 声明优先（声明了却不存在 ⇒ MigrationError），未声明才约定位探测
   （`api/migrations/` 优先、`migrations/` 兜底）；
2. ★ 真实回归：对真实 `services/api/modules/` 跑一遍，内置插件的迁移必须能被
   发现（不是只测沙箱）；
3. 多迁移插件（calendar 0001/0002）的 models 模块必须只加载一次——修复让内置
   迁移真的被执行后，这条曾因两个迁移各用不同模块名而炸「表重复定义」。

★ 纪律：所有沙箱插件都造在 pytest 的 `tmp_path` 里，绝不在
  `services/api/modules/` 下造探针目录，绝不写真实源码目录；本文件不连数据库。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.errors import MigrationError
from db.migrate import discover_plugin_migrations

API_ROOT = Path(__file__).resolve().parents[1]  # services/api/
REAL_MODULES_DIR = API_ROOT / "modules"

MIGRATION_STUB = '''"""沙箱迁移（不执行，只验发现）"""


def upgrade(engine):  # pragma: no cover - 本文件不跑迁移
    raise AssertionError("discover 不该执行迁移")


def downgrade(engine):  # pragma: no cover
    raise AssertionError("discover 不该执行迁移")
'''


def _write_migration(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(MIGRATION_STUB, encoding="utf-8")
    return path


def _make_plugin(
    root: Path,
    plugin_id: str,
    *,
    manifest: dict | str | None | object = ...,
    api_migrations: bool = False,
    legacy_migrations: bool = False,
) -> Path:
    """在沙箱 root 下造插件目录。

    manifest=... ⇒ 写一个最小合法 manifest（未含 migrations 字段）；
    manifest=None ⇒ 不写 manifest.json；manifest 为 str ⇒ 原样写入（造非法 JSON）；
    manifest 为 dict ⇒ 写该 dict（可含 "migrations" 键）。
    """
    d = root / plugin_id
    d.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        if isinstance(manifest, str):
            (d / "manifest.json").write_text(manifest, encoding="utf-8")
        else:
            body = {"id": plugin_id, "name": plugin_id, "version": "0.1.0", "kind": "builtin"}
            if isinstance(manifest, dict):
                body.update(manifest)
            (d / "manifest.json").write_text(
                json.dumps(body, ensure_ascii=False), encoding="utf-8"
            )
    if api_migrations:
        _write_migration(d / "api" / "migrations", "0001_api.py")
    if legacy_migrations:
        _write_migration(d / "migrations", "0001_legacy.py")
    return d


def _versions(items: list[tuple[str, str, Path]], plugin_id: str) -> list[str]:
    return [v for pid, v, _ in items if pid == plugin_id]


# ─────────────── 1. 内置插件的真实布局：migrations/ + 声明 "migrations" ───────────────


def test_legacy_dir_with_declared_manifest_is_discovered(tmp_path: Path) -> None:
    """15 个内置插件的现状：迁移在 migrations/，manifest 写 "migrations"。"""
    _make_plugin(
        tmp_path, "calendar", manifest={"migrations": "migrations"}, legacy_migrations=True
    )
    items = discover_plugin_migrations(tmp_path)
    assert _versions(items, "calendar") == ["0001_legacy"]
    assert items[0][2].parent == tmp_path / "calendar" / "migrations"


# ─────────────── 2. 契约位 api/migrations/ + 声明 "api/migrations" ───────────────


def test_api_dir_with_declared_manifest_is_discovered(tmp_path: Path) -> None:
    _make_plugin(
        tmp_path,
        "demo3p",
        manifest={"migrations": "api/migrations"},
        api_migrations=True,
    )
    items = discover_plugin_migrations(tmp_path)
    assert _versions(items, "demo3p") == ["0001_api"]
    assert items[0][2].parent == tmp_path / "demo3p" / "api" / "migrations"


def test_declared_nonconventional_dir_is_honored(tmp_path: Path) -> None:
    """声明了自定义目录（不是两个约定位置）也必须按声明走。"""
    d = _make_plugin(tmp_path, "custom", manifest={"migrations": "db/migrations"})
    _write_migration(d / "db" / "migrations", "0001_custom.py")
    _write_migration(d / "migrations", "0001_legacy.py")  # 约定位也有，但声明优先
    assert _versions(discover_plugin_migrations(tmp_path), "custom") == ["0001_custom"]


# ─────────────── 3. 未声明（null）⇒ 约定位兜底 ───────────────


def test_undeclared_manifest_falls_back_to_legacy_dir(tmp_path: Path) -> None:
    _make_plugin(tmp_path, "nodecl", manifest={"migrations": None}, legacy_migrations=True)
    assert _versions(discover_plugin_migrations(tmp_path), "nodecl") == ["0001_legacy"]


def test_missing_migrations_key_falls_back_to_legacy_dir(tmp_path: Path) -> None:
    """manifest 里根本没有 migrations 键 ⇒ 等同未声明 ⇒ 兜底。"""
    _make_plugin(tmp_path, "nokey", manifest=..., legacy_migrations=True)
    assert _versions(discover_plugin_migrations(tmp_path), "nokey") == ["0001_legacy"]


def test_undeclared_with_both_dirs_prefers_api_dir(tmp_path: Path) -> None:
    _make_plugin(
        tmp_path,
        "both",
        manifest={"migrations": None},
        api_migrations=True,
        legacy_migrations=True,
    )
    # 契约位获胜，legacy 不重复发现（不会双跑）
    assert _versions(discover_plugin_migrations(tmp_path), "both") == ["0001_api"]


# ─────────────── 4. 声明了却不存在 ⇒ 报错（不静默退回） ───────────────


def test_declared_but_missing_dir_raises(tmp_path: Path) -> None:
    _make_plugin(
        tmp_path,
        "ghost",
        manifest={"migrations": "migrations"},
        # 声明了 migrations/ 但目录不存在（只有约定位 api/migrations/）
        api_migrations=True,
    )
    with pytest.raises(MigrationError) as ei:
        discover_plugin_migrations(tmp_path)
    msg = str(ei.value)
    assert "ghost" in msg, "报错必须点名是哪个插件"
    assert "migrations" in msg


# ─────────────── 5. manifest 缺失 / 非法 JSON ⇒ 不崩（CLI 是部署入口） ───────────────


def test_manifest_missing_does_not_crash(tmp_path: Path) -> None:
    _make_plugin(tmp_path, "nomanifest", manifest=None, legacy_migrations=True)
    _make_plugin(tmp_path, "empty_dir", manifest=None)  # 连迁移目录都没有
    items = discover_plugin_migrations(tmp_path)
    # 无 manifest ⇒ 未声明 ⇒ 兜底到 migrations/
    assert _versions(items, "nomanifest") == ["0001_legacy"]
    assert _versions(items, "empty_dir") == []


def test_invalid_manifest_json_does_not_crash(tmp_path: Path) -> None:
    _make_plugin(tmp_path, "brokenjson", manifest="{ not json ,,,", legacy_migrations=True)
    items = discover_plugin_migrations(tmp_path)
    assert _versions(items, "brokenjson") == ["0001_legacy"]


def test_manifest_not_an_object_does_not_crash(tmp_path: Path) -> None:
    _make_plugin(tmp_path, "listjson", manifest="[]", legacy_migrations=True)
    assert _versions(discover_plugin_migrations(tmp_path), "listjson") == ["0001_legacy"]


def test_dunder_dir_is_skipped(tmp_path: Path) -> None:
    """modules/ 根下的 __pycache__ 不是插件，不该被当成插件扫。"""
    pycache = tmp_path / "__pycache__" / "migrations"
    _write_migration(pycache, "0001_x.py")
    assert discover_plugin_migrations(tmp_path) == []


def test_nonexistent_root_returns_empty(tmp_path: Path) -> None:
    assert discover_plugin_migrations(tmp_path / "no_such_dir") == []


# ─────────────── 6. ★ 真实回归：内置插件迁移真的被发现 ───────────────


def test_real_modules_discover_builtin_migrations() -> None:
    """对真实 services/api/modules/ 跑：修复必须真的生效（不是只测沙箱）。"""
    items = discover_plugin_migrations()  # 不传参 ⇒ 默认扫真实 modules/
    assert items, "内置插件迁移一条都没发现——修复未生效"

    plugin_ids = {pid for pid, _, _ in items}
    assert len(plugin_ids) >= 15, f"内置插件覆盖不足：{sorted(plugin_ids)}"
    # legacy 位（modules/<id>/migrations/）的代表插件必须在列
    assert {"calendar", "todo", "finance"} <= plugin_ids
    # 迁移路径必须真的落在插件目录里、且是 .py
    for pid, version, path in items:
        assert path.is_file(), f"{pid}/{version} 指向不存在的文件：{path}"
        assert path.suffix == ".py"
        assert path.parent.name == "migrations", f"{pid} 的迁移不在 migrations/ 下：{path}"
        assert REAL_MODULES_DIR / pid in path.parents, f"{pid} 的迁移跑出了插件根：{path}"


def test_real_modules_cover_known_versions() -> None:
    """抽验具体版本号：calendar/todo/finance 的多版本迁移都要在。"""
    items = discover_plugin_migrations()
    assert "0001_init" in _versions(items, "calendar")
    assert "0002_reminder_log" in _versions(items, "calendar")
    assert "0002_origin" in _versions(items, "todo")
    assert "0002_snapshot" in _versions(items, "finance")


# ─────────────── 7. 多迁移插件必须复用同一个 models 模块（表只定义一次） ───────────────


def test_multi_migration_plugin_reuses_single_models_module() -> None:
    """回归：修复后内置插件迁移真的会被执行，calendar 的 0001/0002 曾各用不同
    模块名加载 models.py，导致 calendar_event 在 SQLModel 共享 metadata 上被
    定义两遍（InvalidRequestError）。_load_migration_file 必须先以包路径 import
    models，让各迁移文件的 sys.modules 守卫命中同一个模块对象。"""
    import sys

    from db import migrate as M

    calendar_migs = [(v, p) for pid, v, p in M.discover_plugin_migrations() if pid == "calendar"]
    assert len(calendar_migs) >= 2, "calendar 应有 0001/0002 两条迁移"

    first = M._load_migration_file(calendar_migs[0][1], "calendar", calendar_migs[0][0])
    second = M._load_migration_file(calendar_migs[1][1], "calendar", calendar_migs[1][0])
    assert first._load_models() is second._load_models(), (
        "同一插件的多个迁移必须复用同一个 models 模块（否则表会被重复定义）"
    )
    assert sys.modules.get("modules.calendar.models") is first._load_models()
