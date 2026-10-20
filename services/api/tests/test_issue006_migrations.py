"""ISSUE-006 内核迁移扫描路径修复测试（只跑这一个文件）。

覆盖四组（全真实路径，无 mock 冒充）：
1. 双目录探测：api/migrations/ 优先、migrations/ 兜底、两者都有契约位获胜、都无 → 空；
2. 启动对账：新库自动补建台账 app_setting、补跑迁移并记录、二次对账幂等；
   另附 api/migrations/ 契约位回归（第三方用法不被双探测破坏）；
3. fail fast：坏迁移启动即抛，报错点名插件；
4. lifespan 集成：真实 modules/ 下造临时插件，with TestClient 进 lifespan →
   插件表自动出现（生产同路径；测试文件互不进 lifespan，零交叉污染）。

数据库隔离：临时库 ./data/tmp_issue006_<随机>.db，绝不碰主库 alongside.db。
临时插件 modules/i006probe/ 测试结束强制删除（test_kernel 同款纪律）。
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import uuid
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

_TMP_DB = f"./data/tmp_issue006_{uuid.uuid4().hex[:8]}.db"
os.environ["DB_PATH"] = _TMP_DB


def _cleanup_tmp_db() -> None:
    with suppress(FileNotFoundError, PermissionError):
        # Windows 上 SQLite 引擎句柄可能未释放，删不掉就算了（名字唯一不互踩）
        os.remove(_TMP_DB)


atexit.register(_cleanup_tmp_db)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.plugins.migrations import (  # noqa: E402
    _discover,
    reconcile_migrations,
    run_migrations,
)
from db.engine import get_engine, init_engine, make_engine  # noqa: E402

MODULES_DIR = Path(__file__).resolve().parents[1] / "modules"
PROBE_ID = "i006probe"

MIGRATION_0001 = '''"""probe migration 0001"""
from typing import Any


def upgrade(engine: Any) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS i006_thing (id TEXT PRIMARY KEY, name TEXT)"
        )


def downgrade(engine: Any) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS i006_thing")
'''

MIGRATION_BROKEN = '''"""probe migration broken（测试 fail fast 用）"""
from typing import Any


def upgrade(engine: Any) -> None:
    raise RuntimeError("boom: 迁移本身坏了")


def downgrade(engine: Any) -> None:
    pass
'''

ROUTER = '''
from fastapi import APIRouter

router = APIRouter()


@router.get("/ping")
def ping() -> dict[str, str]:
    return {"pong": "probe"}
'''


def _manifest() -> str:
    base = {
        "id": PROBE_ID,
        "name": "ISSUE006 探针",
        "version": "0.1.0",
        "kind": "builtin",
        "minKernel": "0.1.0",
        "kernelApi": "^1",
        "icon": "probe",
        "description": "ISSUE-006 测试用临时模块",
        "author": "test",
        "window": {"w": 480, "h": 320},
        "entry": "@apps/probe",
        "api": {
            "base": f"/api/v1/{PROBE_ID}",
            "openapi": f"/api/v1/{PROBE_ID}/openapi.json",
            "health": f"/api/v1/{PROBE_ID}/health",
        },
        "provides": [],
        "requires": [],
        "slots": [],
        "emits": [],
        "consumes": [],
        "permissions": [],
        "migrations": None,
        "settingsSchema": None,
        "lifecycle": {
            "onInstall": None,
            "onEnable": None,
            "onDisable": None,
            "onUninstall": None,
        },
    }
    return json.dumps(base, ensure_ascii=False)


def _write_plugin(
    root: Path,
    *,
    legacy: bool = False,
    broken: bool = False,
    with_api_layout: bool = False,
) -> Path:
    """在 tmp 目录造探针插件（reconcile 不校验 manifest，直接吃 (id, dir)）。"""
    d = root / PROBE_ID
    d.mkdir(parents=True, exist_ok=True)
    if with_api_layout:
        api_dir = d / "api" / "migrations"
        api_dir.mkdir(parents=True, exist_ok=True)
        (api_dir / "0001_api.py").write_text(MIGRATION_0001, encoding="utf-8")
    if legacy:
        leg_dir = d / "migrations"
        leg_dir.mkdir(parents=True, exist_ok=True)
        name = "0001_broken.py" if broken else "0001_legacy.py"
        (leg_dir / name).write_text(
            MIGRATION_BROKEN if broken else MIGRATION_0001, encoding="utf-8"
        )
    return d


def _info(d: Path) -> SimpleNamespace:
    return SimpleNamespace(id=PROBE_ID, directory=d)


# ───────────────── 1. 双目录探测（_migration_dir 行为） ─────────────────


def test_discover_prefers_api_layout(tmp_path: Path) -> None:
    d = _write_plugin(tmp_path, with_api_layout=True)
    assert [v for v, _ in _discover(_info(d))] == ["0001_api"]


def test_discover_falls_back_to_legacy_layout(tmp_path: Path) -> None:
    d = _write_plugin(tmp_path, legacy=True)  # 实盘 12 个 builtin 的现状
    assert [v for v, _ in _discover(_info(d))] == ["0001_legacy"]


def test_discover_api_layout_wins_when_both_exist(tmp_path: Path) -> None:
    d = _write_plugin(tmp_path, legacy=True, with_api_layout=True)
    # 契约位获胜，legacy 不重复发现 → 不会双跑
    assert [v for v, _ in _discover(_info(d))] == ["0001_api"]


def test_discover_empty_when_neither_exists(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    assert _discover(_info(d)) == []


# ───────────────── 2. 启动对账：新库补建 + 幂等 + 契约位回归 ─────────────────


def test_reconcile_backfills_and_is_idempotent(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{(tmp_path / 'recon.db').as_posix()}")
    d = _write_plugin(tmp_path, legacy=True)

    # 新库：app_setting 尚不存在 → 对账自建台账并补跑迁移
    first = reconcile_migrations(engine, [(PROBE_ID, d)])
    assert first == {PROBE_ID: ["0001_legacy"]}
    with engine.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
    assert "i006_thing" in tables
    assert "app_setting" in tables

    # 幂等：二次对账不重复执行（台账只增不改）
    second = reconcile_migrations(engine, [(PROBE_ID, d)])
    assert second == {PROBE_ID: []}
    engine.dispose()


def test_run_migrations_backward_compat_api_layout(tmp_path: Path) -> None:
    """回归：契约位 api/migrations/（第三方插件用法）不被双探测破坏。"""
    engine = make_engine(f"sqlite:///{(tmp_path / 'compat.db').as_posix()}")
    d = _write_plugin(tmp_path, with_api_layout=True)
    assert run_migrations(engine, _info(d)) == ["0001_api"]
    engine.dispose()


# ───────────────── 3. fail fast：坏迁移点名插件 ─────────────────


def test_reconcile_fails_fast_with_plugin_name(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{(tmp_path / 'broken.db').as_posix()}")
    d = _write_plugin(tmp_path, legacy=True, broken=True)
    with pytest.raises(RuntimeError) as ei:
        reconcile_migrations(engine, [(PROBE_ID, d)])
    assert PROBE_ID in str(ei.value), "报错必须点名是哪个插件"
    engine.dispose()


# ───────────────── 4. lifespan 集成（生产同路径，全真） ─────────────────


@pytest.fixture
def probe_module() -> Iterator[Path]:
    """在真实 modules/ 下造临时插件（带 legacy 迁移），测试后必删。"""
    d = MODULES_DIR / PROBE_ID
    if d.exists():
        shutil.rmtree(d)
    (d / "migrations").mkdir(parents=True)
    (d / "__init__.py").write_text("", encoding="utf-8")
    (d / "manifest.json").write_text(_manifest(), encoding="utf-8")
    (d / "router.py").write_text(ROUTER, encoding="utf-8")
    (d / "migrations" / "0001_init.py").write_text(MIGRATION_0001, encoding="utf-8")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_lifespan_reconcile_creates_plugin_tables(probe_module: Path) -> None:
    init_engine()  # 用 DB_PATH 指向的临时库
    engine = get_engine()

    # 前置：i006_thing 尚不存在
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='i006_thing'"
            )
        ).fetchone()
    assert row is None

    # with 上下文 → lifespan 启动 → 对账补跑 → 表出现（生产 uvicorn 同路径）
    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get(f"/api/v1/{PROBE_ID}/ping").json() == {"pong": "probe"}

    # 对账在 lifespan 里真的跑了：探针表 + 台账记录都在
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='i006_thing'"
            )
        ).fetchone()
        recs = conn.execute(
            text("SELECT key FROM app_setting WHERE key LIKE :p"),
            {"p": f"migration.{PROBE_ID}.%"},
        ).fetchall()
    assert row is not None, "lifespan 对账应已建出 i006_thing"
    assert recs, "lifespan 对账应已写入迁移台账"
