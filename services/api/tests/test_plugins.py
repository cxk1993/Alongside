"""插件框架集成测试（T14）。

覆盖验收清单里能用机器证明的项：
  - 契约校验精确到字段（缺 kernelApi / 未知字段 / 非法 id）
  - kernelApi 不兼容明确拒绝（人话）
  - 声明式权限：未声明 → 403 且说明缺哪个权限
  - 设置按 settingsSchema 校验，写错类型被拒
  - 四态：install → enable → disable → uninstall，且 uninstall 把表/文件/状态清干净
  - 注册表与运行时分离：disable 摘掉路由、enable 重新挂上
  - core 不可禁用、builtin 不可卸载（明确拒绝）

测试用一个真实的第三方插件（落在 plugins/<id>/，测试后由 uninstall 删除），
不依赖 mock 冒充真实行为。
"""
from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from core.app import create_app
from core.errors import ManifestError
from core.plugins import satisfies
from core.plugins.validate import validate_manifest
from core.plugins.version import check_compatibility
from core.security import create_access_token
from db.engine import get_engine, init_engine

API_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = API_ROOT.parent.parent / "plugins"  # 项目根/plugins
DEMO_ID = f"demo3p-{uuid.uuid4().hex[:6]}"
# 表名跟随插件 id 动态生成：每次测试 uuid 不同，表名随之隔离；
# 且必须以 `{id 连字符→下划线}_` 开头，否则 authorizer 拒绝访问。
DEMO_TABLE = DEMO_ID.replace("-", "_") + "_thing"

TOKEN = create_access_token("admin")
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _write_demo_plugin(root: Path, *, kernel_api: str = "^1") -> None:
    (root / "api" / "migrations").mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "id": root.name,
                "name": "演示第三方插件",
                "version": "0.1.0",
                "kind": "third-party",
                "minKernel": "0.1.0",
                "kernelApi": kernel_api,
                "icon": "clock",
                "description": "测试用第三方插件",
                "author": "test",
                "window": {"w": 480, "h": 320},
                "entry": "",
                "api": {
                    "base": f"/api/v1/{root.name}",
                    "openapi": f"/api/v1/{root.name}/openapi.json",
                    "health": f"/api/v1/{root.name}/health",
                },
                "provides": [f"{root.name}.thing.read"],
                "requires": [],
                "slots": ["dashboard.card"],
                "emits": [f"{root.name}.created"],
                "consumes": [],
                "permissions": ["db:own"],
                "migrations": "api/migrations",
                "settingsSchema": "api/settings.schema.json",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "api" / "models.py").write_text(
        "from sqlmodel import Field, SQLModel\n"
        "class DemoThing(SQLModel, table=True):\n"
        f"    __tablename__ = '{DEMO_TABLE}'\n"
        "    __table_args__ = {'extend_existing': True}\n"
        "    id: str = Field(primary_key=True, max_length=32)\n"
        "    title: str = Field(default='', max_length=80)\n",
        encoding="utf-8",
    )
    (root / "api" / "migrations" / "0001_init.py").write_text(
        "def upgrade(engine):\n"
        "    from sqlmodel import SQLModel\n"
        "    import importlib.util, sys\n"
        "    p = __import__('pathlib').Path(__file__).resolve().parent.parent / 'models.py'\n"
        "    spec = importlib.util.spec_from_file_location('demo_models', p)\n"
        "    mod = importlib.util.module_from_spec(spec); sys.modules['demo_models']=mod\n"
        "    spec.loader.exec_module(mod)\n"
        "    mod.DemoThing.__table__.create(bind=engine, checkfirst=True)\n"
        "def downgrade(engine):\n"
        "    from sqlmodel import SQLModel\n"
        "    import importlib.util, sys\n"
        "    p = __import__('pathlib').Path(__file__).resolve().parent.parent / 'models.py'\n"
        "    spec = importlib.util.spec_from_file_location('demo_models', p)\n"
        "    mod = importlib.util.module_from_spec(spec); sys.modules['demo_models']=mod\n"
        "    spec.loader.exec_module(mod)\n"
        "    mod.DemoThing.__table__.drop(bind=engine, checkfirst=True)\n",
        encoding="utf-8",
    )
    (root / "api" / "router.py").write_text(
        "from fastapi import APIRouter\n"
        "from sqlmodel import Session, select\n"
        "from core.deps import get_db\n"
        "router = APIRouter()\n"
        "@router.get('/health')\n"
        "def health(): return {'ok': True}\n"
        "@router.get('/things')\n"
        "def things(db: Session = __import__('fastapi').Depends(get_db)):\n"
        "    mod = __import__('importlib').import_module('demo_models')\n"
        "    rows = db.exec(select(mod.DemoThing)).all()\n"
        "    return [r.title for r in rows]\n",
        encoding="utf-8",
    )
    (root / "api" / "settings.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"refresh_minutes": {"type": "integer", "minimum": 1}},
                "required": ["refresh_minutes"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    # 每个测试用独立引擎 + 独立 SQLite：init_engine 幂等缓存首引擎，
    # 不重置会导致跨测试共用同一库、plugin_state 残留造成污染。
    import db.engine as _db_engine_mod

    _db_engine_mod._engine = None
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = init_engine(db_url)
    SQLModel.metadata.create_all(engine)  # 建 plugin_state/plugin_setting/app_setting 等
    yield TestClient(create_app())
    # 测试结束清理临时第三方插件目录（uninstall 应已删除，这里兜底）
    d = PLUGINS_DIR / DEMO_ID
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def demo_plugin_dir() -> Iterator[Path]:
    root = PLUGINS_DIR / DEMO_ID
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    _write_demo_plugin(root)
    yield root
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


# ───────────────────── 契约 / 版本（单元） ─────────────────────
def test_schema_rejects_missing_kernel_api() -> None:
    raw = {
        "id": "x",
        "name": "x",
        "version": "0.1.0",
        "kind": "builtin",
        "window": {"w": 480, "h": 320},
        "entry": "@apps/x",
        "api": {"base": "/api/v1/x"},
        "provides": [],
        "requires": [],
        "slots": [],
        "emits": [],
        "consumes": [],
        "permissions": [],
    }
    with pytest.raises(ManifestError):
        validate_manifest(raw)


def test_schema_rejects_unknown_field() -> None:
    raw = {
        "id": "x",
        "name": "x",
        "version": "0.1.0",
        "kind": "builtin",
        "kernelApi": "^1",
        "window": {"w": 480, "h": 320},
        "entry": "@apps/x",
        "api": {"base": "/api/v1/x"},
        "provides": [],
        "requires": [],
        "slots": [],
        "emits": [],
        "consumes": [],
        "permissions": [],
        "bogus_field": 1,
    }
    with pytest.raises(ManifestError):
        validate_manifest(raw)


def test_incompatible_kernel_api_rejected_with_human_message() -> None:
    from core.plugins.version import IncompatibleKernelApiError

    with pytest.raises(IncompatibleKernelApiError) as ei:
        check_compatibility("^99")
    assert "内核接口" in str(ei.value)


def test_satisfies_caret() -> None:
    assert satisfies("^1", "1.0.0")
    assert not satisfies("^1", "2.0.0")
    assert not satisfies("^2", "1.0.0")


# ───────────────────── 权限门 ─────────────────────
def test_undeclared_permission_rejected() -> None:
    from core.errors import ForbiddenError
    from core.plugins.permissions import assert_permission

    with pytest.raises(ForbiddenError) as ei:
        assert_permission(["db:own"], "net:out:example.com")
    assert "net:out:example.com" in str(ei.value)


# ───────────────────── 设置校验 ─────────────────────
def test_settings_reject_wrong_type(client: TestClient, demo_plugin_dir: Path) -> None:
    # 先安装，才有 plugin_state
    r = client.post("/api/v1/plugins/install", json={"id": DEMO_ID}, headers=AUTH)
    assert r.status_code == 200, r.text
    # 写错类型：refresh_minutes 应是 integer
    r = client.patch(
        f"/api/v1/plugins/{DEMO_ID}/settings",
        json={"refresh_minutes": "soon"},
        headers=AUTH,
    )
    assert r.status_code == 422, r.text
    # 写正确类型
    r = client.patch(
        f"/api/v1/plugins/{DEMO_ID}/settings",
        json={"refresh_minutes": 5},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["refresh_minutes"] == 5


# ───────────────────── 四态生命周期 ─────────────────────
def test_full_lifecycle_install_enable_disable_uninstall(
    client: TestClient, demo_plugin_dir: Path
) -> None:
    base = f"/api/v1/{DEMO_ID}"

    # 安装（第三方）
    r = client.post("/api/v1/plugins/install", json={"id": DEMO_ID}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True

    # 路由已挂上、表已建
    assert client.get(f"{base}/health", headers=AUTH).status_code == 200
    r = client.get(f"{base}/things", headers=AUTH)
    assert r.status_code == 200

    # 出现在列表里
    listed = client.get("/api/v1/plugins", headers=AUTH).json()
    ids = [p["id"] for p in listed["plugins"] if p.get("id")]
    assert DEMO_ID in ids

    # 扩展点贡献可见
    slots = client.get("/api/v1/plugins/slots/dashboard.card", headers=AUTH).json()
    assert any(c["plugin_id"] == DEMO_ID for c in slots["contributions"])

    # 禁用：路由摘掉（注册表与运行时分离）
    r = client.post(f"/api/v1/plugins/{DEMO_ID}/disable", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    health = client.get(f"{base}/health", headers=AUTH)
    assert health.status_code in (404, 410), health.status_code  # 已摘掉

    # 启用：重新挂上
    r = client.post(f"/api/v1/plugins/{DEMO_ID}/enable", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True
    assert client.get(f"{base}/health", headers=AUTH).status_code == 200

    # 卸载：清表 + 清状态 + 删文件
    r = client.post(f"/api/v1/plugins/{DEMO_ID}/uninstall", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["uninstalled"] is True

    # 表没了
    from sqlalchemy import text

    with get_engine().connect() as conn:
        tbl = conn.execute(
            text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{DEMO_TABLE}'")
        ).fetchone()
    assert tbl is None, "卸载后插件表应被清理"

    # 状态表清了
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT id FROM plugin_state WHERE id=:i"), {"i": DEMO_ID}
        ).fetchone()
    assert row is None, "卸载后 plugin_state 应被清理"

    # 文件删了
    assert not demo_plugin_dir.exists(), "卸载后插件目录应被删除"


# ───────────────────── 口径统一：third-party 无状态行 = 未启用 ─────────────────────
def test_third_party_without_state_row_is_not_enabled(
    client: TestClient, demo_plugin_dir: Path
) -> None:
    """口径统一（2026-09-27 · 主人件①）：third-party 无状态行 = 未启用。

    防回归 countdown 404：插件放进 plugins/ 但从未 enable 时——
    清单可见（发现）但 enabled=False；slots 贡献不标启用；MCP 不暴露
    幽灵工具（工具看得见、路由 404）；/reload 不误报 needs_restart。
    builtin 无状态行仍默认启用（随系统发布）。
    """
    # 清单可见但未启用
    listed = client.get("/api/v1/plugins", headers=AUTH).json()
    item = next(p for p in listed["plugins"] if p.get("id") == DEMO_ID)
    assert item["enabled"] is False

    # builtin 无状态行仍默认启用
    builtins = [p for p in listed["plugins"] if p.get("source") == "builtin"]
    assert builtins, "测试环境应有内置插件"
    assert all(p["enabled"] is True for p in builtins)

    # 扩展点贡献可见但不标启用
    slots = client.get("/api/v1/plugins/slots/dashboard.card", headers=AUTH).json()
    contrib = next(c for c in slots["contributions"] if c["plugin_id"] == DEMO_ID)
    assert contrib["enabled"] is False

    # MCP 不暴露幽灵工具
    from modules.mcp.registry_adapter import build_tool_map

    tool_plugin_ids = {t.plugin_id for t in build_tool_map()}
    assert DEMO_ID not in tool_plugin_ids

    # /reload 不把「无行未启用」误报为需要重启
    reload_body = client.post("/api/v1/plugins/reload", headers=AUTH).json()
    assert not any(d["id"] == DEMO_ID for d in reload_body["reload_required"])


# ───────────── 回归：enable() 必须补跑迁移（countdown 500 根因）─────────────
def test_enable_runs_migrations_when_table_missing(
    client: TestClient, demo_plugin_dir: Path
) -> None:
    """★ 回归（2026-09-26 修 · 2026-09-28 补钉子）：enable() 会补跑迁移。

    背景（countdown 500 的完整成因）：
      插件**直接放进 plugins/ + 手工建 plugin_state 行启用**（从未走 install）时，
      旧版 enable() 只挂载、**不跑迁移** ⇒ 表永远不建 ⇒ 接口恒 500（no such table）。
      而 core/app.py 的「启动恢复」走的正是 enable()，故这是唯一的自动触发点。

    本测试钉住的契约：
      1. enable() 后，插件声明的迁移**确实执行**（表被建出来）
      2. 台账 app_setting 里**留下 migration.<id>.<ver> 记录**
      3. 重复 enable() **幂等**（台账只增不改，不重复建表、不报错）
    """
    from sqlalchemy import text

    base = f"/api/v1/{DEMO_ID}"
    engine = get_engine()

    # ── ① 装一个"从未安装过"的第三方插件：只建状态行 + 建目录，不调 install ──
    #（模拟 countdown 场景：插件在 plugins/ 里，plugin_state 有行且 enabled=1）
    from db.models.system import PluginState
    from core.plugins import discover as discover_mod
    from core.deps import kernel_session

    _res = discover_mod.discover_plugins()
    info = next((p for p in _res.plugins if p.id == DEMO_ID), None)
    assert info is not None, "测试插件应被发现"

    # ★ 前置：确保"未跑迁移"的状态 —— 主动 DROP 表 + 清台账
    #（不能假设表不存在：同模块别的用例可能已装过同 id 插件，表会残留）
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {DEMO_TABLE}"))
        conn.execute(text("DELETE FROM app_setting WHERE key LIKE :p"), {"p": f"migration.{DEMO_ID}.%"})

    with engine.connect() as conn:
        before = conn.execute(
            text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{DEMO_TABLE}'")
        ).fetchone()
    assert before is None, f"前置条件：此刻 {DEMO_TABLE} 表不应存在（已 DROP）"

    # 手工写 plugin_state 行（enabled=1）—— 等价于"直接放进 plugins/ 后启用"
    with kernel_session() as db:
        row = db.get(PluginState, DEMO_ID)
        if row is None:
            db.add(
                PluginState(
                    id=DEMO_ID,
                    version="0.1.0",
                    kind="third-party",
                    enabled=True,
                    granted_permissions='["db:own"]',
                )
            )
        else:
            row.enabled = True

    # ── ② 调 enable()（走 manager，与启动恢复同一条路）──
    from core.plugins.manager import PluginManager

    mgr = PluginManager()
    mgr.enable(DEMO_ID, client.app.state.registry)

    # ── ③ 断言：表被建出来了（迁移真跑了）──
    with engine.connect() as conn:
        after = conn.execute(
            text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{DEMO_TABLE}'")
        ).fetchone()
    assert after is not None, "★ enable() 应补跑迁移建表（回归：countdown 500 根因）"

    # ── ④ 断言：台账留痕 migration.<id>.0001_init ──
    with engine.connect() as conn:
        led = conn.execute(
            text("SELECT key FROM app_setting WHERE key LIKE :p"),
            {"p": f"migration.{DEMO_ID}.%"},
        ).fetchall()
    assert led, f"★ 迁移台账应记 migration.{DEMO_ID}.0001_init"
    assert any(f"migration.{DEMO_ID}.0001_init" == r[0] for r in led), [r[0] for r in led]

    # ── ⑤ 幂等：再 enable 一次不报错、表还在 ──
    mgr.enable(DEMO_ID, client.app.state.registry)
    with engine.connect() as conn:
        still = conn.execute(
            text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{DEMO_TABLE}'")
        ).fetchone()
    assert still is not None, "重复 enable 后表应仍存在（迁移幂等）"

    # ── ⑥ 路由可达（表在 + 路由挂上 = 端点不再 500）──
    r = client.get(f"{base}/things", headers=AUTH)
    assert r.status_code == 200, f"表建好后端点应 200（原病灶是 500）：{r.text}"

    # 清理状态行（表由 fixture 兜底删目录，这里顺手清 plugin_state）
    from db.models.system import PluginState as _PS

    with kernel_session() as db:
        row = db.get(_PS, DEMO_ID)
        if row is not None:
            db.delete(row)


# ───────────────────── 硬规则：core 不可禁用 / builtin 不可卸载 ─────────────────────
def test_disable_core_rejected(client: TestClient) -> None:
    # 管理模块本身是 core
    r = client.post("/api/v1/plugins/plugins/disable", headers=AUTH)
    assert r.status_code == 403, r.text


def test_uninstall_builtin_rejected(client: TestClient) -> None:
    # auth 是 builtin
    r = client.post("/api/v1/plugins/auth/uninstall", headers=AUTH)
    assert r.status_code == 403, r.text


# ───────────────────────── ISSUE-012 契约：清单必须携带 manifest ─────────────────────────
def test_list_plugins_carries_manifest(client: TestClient) -> None:
    """ISSUE-012：/api/v1/plugins 每项必须含 manifest（前端 p.manifest.entry 依赖它）。

    回归背景：list_plugins() 组装时漏了 manifest 字段 → 前端 `p.manifest.entry` 必抛
    TypeError → 插件扩展点贡献（E5 sidecar / D1 dashboard.card）结构化无法挂载。
    修法：每项加 `"manifest": p.manifest`；本测试钉住该契约。
    """
    r = client.get("/api/v1/plugins", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    plugins = body if isinstance(body, list) else body.get("plugins", body.get("items", []))
    assert len(plugins) > 0, "至少应发现一个插件"

    for p in plugins:
        assert "manifest" in p, f"插件 {p.get('id')} 缺 manifest 字段（ISSUE-012）"
        m = p["manifest"]
        # entry/window/slots 应可读（允许合法缺 entry：纯 API 插件无 UI 入口）
        assert isinstance(m.get("slots", []), list), f"{p.get('id')} slots 非列表"
        assert "window" in m or "entry" in m or not m.get("slots"), (
            f"{p.get('id')} 声明了 slots 却无 entry/window（manifest 不完整）"
        )


def test_list_plugins_manifest_slots_include_sidecar(client: TestClient) -> None:
    """ISSUE-012 配套：todo/health 的 manifest.slots 含 window.sidecar（E5/D1 生产前置）。"""
    r = client.get("/api/v1/plugins", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    plugins = body if isinstance(body, list) else body.get("plugins", body.get("items", []))
    by_id = {p.get("id"): p for p in plugins}
    for pid in ("todo", "health"):
        assert pid in by_id, f"{pid} 插件缺失"
        slots = by_id[pid].get("manifest", {}).get("slots", [])
        assert "window.sidecar" in slots, f"{pid} manifest.slots 缺 window.sidecar: {slots}"
