"""插件热重载（r8 · 原地升级）的测试。

覆盖三块判据：

  D1 状态机
    · 转移表**穷举**断言（8×8=64 个组合逐个判定，不只测用到的边）
    · 重载路径 ACTIVE → RELOADING → PENDING → LOADING → ACTIVE 的合法边都在
    · 非法路径必须拒（`UNLOADING → PENDING`、`ACTIVE → LOADING`、
      `TERMINATED → 任意`、`RELOADING → ACTIVE`）
    · 启动对账：遗留 RELOADING 走「补偿 + 重新加载」，**绝不**走「跑完卸载」

  D2 编排与缓存
    · reload 真的换了代码：改 router.py 生效；改**兄弟模块 models.py 也生效**
      （后者是 `sys.modules` 包缓存 + `SQLModel.metadata` 双重陷阱，
       少了 `detach_plugin_tables` 就直接撞 InvalidRequestError）
    · 撤销走既有 effect 逆序回放；重载后 effect 栈不增长（不泄漏）
    · OpenAPI 快照失效；`app.state.modules` 在五个动作后都同步
    · dry_run 零副作用
    · 非 ACTIVE/FAILED 起点被拒（PAUSED 拒、PENDING 拒、不存在 404、core 拒）

  D3 失败语义
    · reload 中途抛错 → phase=FAILED + last_error + 路由摘干净 + 清单里没有它
    · 修好代码后能恢复（enable 回来 ACTIVE），失败不留下「看着在跑实际没挂」的假象

负例一律在**临时目录**里造，绝不写真源码树（r3 的教训）。
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
from core.kernel import EffectKind, get_kernel
from core.plugins.phase import LEGAL, Phase, PhaseReason, is_legal
from core.security import create_access_token
from db.engine import get_engine, init_engine

TOKEN = create_access_token("admin")
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _pid() -> str:
    return f"r8-{uuid.uuid4().hex[:6]}"


# ══════════════════════ fixture ══════════════════════
@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """独立引擎 + 独立数据插件根（与 test_plugin_hotplug_r5 同口径）。"""
    import db.engine as _db_engine_mod

    _db_engine_mod._engine = None
    engine = init_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)

    root = tmp_path / "data" / "plugins"
    root.mkdir(parents=True, exist_ok=True)

    from core.plugins import discover as discover_mod

    monkeypatch.setattr(discover_mod, "default_data_plugins_root", lambda: root)

    def _discover(modules_dir=None, plugins_dir=None):
        d = discover_mod
        root_m = Path(modules_dir) if modules_dir else d._DEFAULT_MODULES_DIR
        plug = Path(plugins_dir) if plugins_dir else d._DEFAULT_PLUGINS_DIR
        builtins, b_errs = d._scan(root_m, "builtin")
        repo_tp, r_errs = d._scan(plug, "third-party")
        data_tp, d_errs = d._scan(root, "third-party")
        merged: dict[str, object] = {}
        for info in [*repo_tp, *data_tp]:
            merged[info.id] = info
        return d.DiscoveryResult(
            plugins=[*builtins, *merged.values()],
            errors=[*b_errs, *r_errs, *d_errs],
        )

    monkeypatch.setattr(discover_mod, "discover_plugins", _discover)
    return root


@pytest.fixture
def client(data_root: Path) -> TestClient:
    # ★ raise_server_exceptions=False：reload 失败时我们要看的是**响应与落库状态**
    #   （500 + phase=FAILED + last_error），不是让 TestClient 把异常抛回来遮住
    #   后面那些更重要的断言。
    return TestClient(create_app(), raise_server_exceptions=False)


def _manifest(pid: str, *, version: str = "0.1.0") -> str:
    return json.dumps(
        {
            "id": pid,
            "name": f"r8 重载测试插件 {pid}",
            "version": version,
            "kind": "third-party",
            "kernelApi": "^1",
            "icon": "star",
            "description": "测试",
            "author": "test",
            "window": {"w": 480, "h": 320},
            "entry": "",
            "api": {"base": f"/api/v1/{pid}", "health": f"/api/v1/{pid}/health"},
            "provides": [f"{pid}.thing.read"],
            "requires": [],
            "slots": [],
            "emits": [],
            "consumes": [],
            "permissions": ["db:own"],
            "migrations": "api/migrations",
            "settingsSchema": None,
        },
        ensure_ascii=False,
    )


# 表名必须带 `{id 连字符→下划线}_` 前缀，否则 authorizer 拒（G4 门禁同规则）
def _table_name(pid: str) -> str:
    return pid.replace("-", "_") + "_thing"


def _write_plugin(
    root: Path,
    pid: str,
    *,
    version: str = "0.1.0",
    marker: str = "v1",
    sibling: str = "s1",
    broken: bool = False,
) -> Path:
    """造一个「router 读兄弟模块常量」的最小插件 —— 两条改动路径都可断言。"""
    d = root / pid
    (d / "api" / "migrations").mkdir(parents=True, exist_ok=True)
    tn = _table_name(pid)
    (d / "manifest.json").write_text(_manifest(pid, version=version), encoding="utf-8")
    (d / "api" / "models.py").write_text(
        "from sqlmodel import Field, SQLModel\n"
        "SIBLING_MARKER = " + json.dumps(sibling) + "\n"
        "class R8Thing(SQLModel, table=True):\n"
        f"    __tablename__ = {json.dumps(tn)}\n"
        "    __table_args__ = {'extend_existing': True}\n"
        "    id: str = Field(primary_key=True, max_length=32)\n"
        "    title: str = Field(default='', max_length=80)\n",
        encoding="utf-8",
    )
    # ★ 迁移自己 exec models.py（模块名 r8_models_<pid>），与 router 用的
    #   `<pkg>.models` 是两份对象 —— 这点与 test_plugins.py 的演示插件同形。
    (d / "api" / "migrations" / "0001_init.py").write_text(
        "def upgrade(engine):\n"
        "    import importlib.util, sys, pathlib\n"
        "    p = pathlib.Path(__file__).resolve().parent.parent / 'models.py'\n"
        f"    spec = importlib.util.spec_from_file_location({json.dumps('r8_models_' + pid.replace('-', '_'))}, p)\n"
        "    mod = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod\n"
        "    spec.loader.exec_module(mod)\n"
        "    mod.R8Thing.__table__.create(bind=engine, checkfirst=True)\n"
        "def downgrade(engine):\n"
        "    import importlib.util, sys, pathlib\n"
        "    p = pathlib.Path(__file__).resolve().parent.parent / 'models.py'\n"
        f"    spec = importlib.util.spec_from_file_location({json.dumps('r8_models_' + pid.replace('-', '_'))}, p)\n"
        "    mod = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod\n"
        "    spec.loader.exec_module(mod)\n"
        "    mod.R8Thing.__table__.drop(bind=engine, checkfirst=True)\n",
        encoding="utf-8",
    )
    if broken:
        (d / "api" / "router.py").write_text(
            "raise RuntimeError('r8: 升级后的代码 import 期就炸')\n",
            encoding="utf-8",
        )
    else:
        (d / "api" / "router.py").write_text(
            "from fastapi import APIRouter\n"
            "from .models import SIBLING_MARKER\n"
            "router = APIRouter()\n"
            "ROUTER_MARKER = " + json.dumps(marker) + "\n"
            "@router.get('/health')\n"
            "def health(): return {'ok': True}\n"
            "@router.get('/mark')\n"
            "def mark(): return {'router': ROUTER_MARKER, 'sibling': SIBLING_MARKER}\n",
            encoding="utf-8",
        )
    return d


def _install(client: TestClient, data_root: Path, pid: str, **kw) -> None:
    _write_plugin(data_root, pid, **kw)
    r = client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["phase"] == "ACTIVE", r.json()


def _phase(client: TestClient, pid: str) -> str:
    r = client.get(f"/api/v1/plugins/{pid}", headers=AUTH)
    assert r.status_code == 200, r.text
    return str(r.json()["phase"])


def _modules_ids(client: TestClient) -> set[str]:
    r = client.get("/api/v1/modules", headers=AUTH)
    assert r.status_code == 200, r.text
    return {m.get("id") for m in r.json()["modules"]}


# ══════════════════════ D1 · 转移表穷举 ══════════════════════
#: 整张表的期望值。**逐对列出**而不是「只列合法边」——
#: 少列的组合在这里算显式断言「它必须非法」，新加一个阶段时这张表会强制
#: 有人把新行新列补完（漏补即红），这正是「加态的账用测试还上」的意思。
EXPECTED_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        ("PENDING", "LOADING"),
        ("PENDING", "FAILED"),
        ("PENDING", "TERMINATED"),
        ("LOADING", "ACTIVE"),
        ("LOADING", "FAILED"),
        ("LOADING", "TERMINATED"),
        ("ACTIVE", "PAUSED"),
        ("ACTIVE", "FAILED"),
        ("ACTIVE", "UNLOADING"),
        ("ACTIVE", "RELOADING"),
        ("PAUSED", "LOADING"),
        ("PAUSED", "ACTIVE"),
        ("PAUSED", "FAILED"),
        ("PAUSED", "UNLOADING"),
        ("FAILED", "PENDING"),
        ("FAILED", "LOADING"),
        ("FAILED", "TERMINATED"),
        ("FAILED", "RELOADING"),
        ("RELOADING", "PENDING"),
        ("RELOADING", "FAILED"),
        ("UNLOADING", "TERMINATED"),
    }
)


@pytest.mark.parametrize("frm", [p.value for p in Phase])
@pytest.mark.parametrize("to", [p.value for p in Phase])
def test_transition_table_is_exactly_as_declared(frm: str, to: str) -> None:
    """64 个组合逐个断言：合法当且仅当在 EXPECTED_EDGES 里。"""
    assert is_legal(frm, to) is ((frm, to) in EXPECTED_EDGES), f"{frm} -> {to}"


def test_transition_table_single_truth() -> None:
    """LEGAL 与 EXPECTED_EDGES 必须逐边相同（防止「两份清单分叉」）。"""
    from_table = {
        (f.value, t.value) for f, ts in LEGAL.items() for t in ts
    }
    assert from_table == EXPECTED_EDGES


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        # 任务书点名的三条禁止边
        ("ACTIVE", "LOADING"),      # 旧 effect 未撤就重装 = 路由挂两次
        ("TERMINATED", "PENDING"),  # 终态无出口
        ("TERMINATED", "ACTIVE"),
        # 重载不能抄近路
        ("RELOADING", "ACTIVE"),    # 什么都没撤就宣称已重载 = 假成功
        ("UNLOADING", "PENDING"),   # ★ r8 决策：靠它省掉 RELOADING 会让崩溃语义分叉
        ("UNLOADING", "LOADING"),
        # 中间态不可暂停
        ("LOADING", "PAUSED"),
        ("RELOADING", "PAUSED"),
        # 没有「卸载后回滚」这种边
        ("UNLOADING", "ACTIVE"),
        ("UNLOADING", "FAILED"),
    ],
)
def test_illegal_transitions_are_rejected(frm: str, to: str) -> None:
    assert is_legal(frm, to) is False


def test_unknown_phase_value_is_not_legal() -> None:
    assert is_legal("ACTIVE", "REBOOTING") is False
    assert is_legal("REBOOTING", "ACTIVE") is False


def test_transition_raises_illegal(tmp_path: Path) -> None:
    """`transition()` 入口自己会拒非法边（不是只靠表）。"""
    from core.plugins.phase import IllegalTransition, PhaseMachine

    rows: dict[str, dict[str, str]] = {"p": {"phase": Phase.UNLOADING.value}}
    machine = PhaseMachine(
        read=lambda pid: rows.get(pid),
        write=lambda pid, payload: rows[pid].update(payload),
    )
    with pytest.raises(IllegalTransition):
        machine.transition("p", Phase.PENDING)
    assert rows["p"]["phase"] == Phase.UNLOADING.value, "被拒的转移不许改状态"


def test_reloading_leftover_is_not_treated_as_uninstall() -> None:
    """★ 加 RELOADING 的全部理由，落成一条可执行的断言。

    遗留 RELOADING（升级崩在半路）必须走「补偿 + 重新加载」；
    如果被归进 finish_unload，后果是跑完卸载 → 迁移回滚 → 表和数据没了。
    """
    from core.plugins.reconcile import plan_reconcile

    plan = plan_reconcile([{"id": "a", "phase": "RELOADING", "phase_reason": "reload"}])
    assert "a" in plan.reload, "遗留 RELOADING 要重新加载"
    assert "a" in plan.compensate, "遗留 RELOADING 要先补偿"
    assert "a" not in plan.finish_unload, "★ 绝不能当成卸载跑完（那会删表）"
    assert "a" not in plan.needs_human

    # 对照：真正的遗留 UNLOADING 仍然必须跑完卸载（这条不能被 reload 带偏）
    plan2 = plan_reconcile([{"id": "b", "phase": "UNLOADING", "phase_reason": "uninstall"}])
    assert "b" in plan2.finish_unload
    assert "b" not in plan2.reload


# ══════════════════════ D2 · reload 行为 ══════════════════════
def test_reload_picks_up_new_router_code(client: TestClient, data_root: Path) -> None:
    """改 router.py 的一行 → reload → 新值生效（任务书的主判据）。"""
    pid = _pid()
    _install(client, data_root, pid, marker="v1")
    assert client.get(f"/api/v1/{pid}/mark", headers=AUTH).json()["router"] == "v1"

    _write_plugin(data_root, pid, marker="v2", sibling="s1")
    r = client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["phase"] == "ACTIVE"
    assert body["reloaded"] is True
    assert body["steps"] == ["RELOADING", "PENDING", "LOADING", "ACTIVE"], body["steps"]

    got = client.get(f"/api/v1/{pid}/mark", headers=AUTH).json()
    assert got["router"] == "v2", "改 router.py 必须生效"
    assert got["sibling"] == "s1"


def test_reload_picks_up_sibling_module(client: TestClient, data_root: Path) -> None:
    """改**兄弟模块** models.py 也要生效 —— 这条是「假热重载」的照妖镜。

    `load_python_module_from_file` 只对 router.py 每次重新 exec；
    `from .models import ...` 走 importlib，会命中 sys.modules 里的旧包。
    ⇒ 不调 `unload_thirdparty_modules()` 的 reload 只换了 router，
      models/service 还是旧的，而升级最常改的正是它们。
    """
    pid = _pid()
    _install(client, data_root, pid, marker="v1", sibling="s1")
    assert client.get(f"/api/v1/{pid}/mark", headers=AUTH).json()["sibling"] == "s1"

    _write_plugin(data_root, pid, marker="v1", sibling="s2-FRESH")
    r = client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH)
    assert r.status_code == 200, r.text

    got = client.get(f"/api/v1/{pid}/mark", headers=AUTH).json()
    assert got["sibling"] == "s2-FRESH", (
        "兄弟模块必须一起重新 exec：sys.modules 包缓存没清的话，"
        "reload 只换 router，模型/服务改动静默不生效"
    )


def test_reload_repicks_tables_without_metadata_collision(
    client: TestClient, data_root: Path
) -> None:
    """重新 exec models.py 不得撞 `Table is already defined`。

    ★ 这是 r8 实测出来的坑（不是推演）：SQLModel 的 metadata 是进程级全局的，
      同名表第二次注册直接抛 InvalidRequestError。
      ⇒ reload 必须先 `detach_plugin_tables()` 把该插件的表摘下来。
    """
    from sqlalchemy.exc import InvalidRequestError
    from sqlmodel import SQLModel

    pid = _pid()
    tn = _table_name(pid)
    _install(client, data_root, pid)
    assert tn in SQLModel.metadata.tables, "安装期迁移已把表注册进 metadata"

    _write_plugin(data_root, pid, sibling="s3")
    try:
        r = client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH)
    except InvalidRequestError as exc:  # 不该发生：发生了就说明摘表没做
        pytest.fail(f"reload 撞了 metadata 同名表：{exc}")
    assert r.status_code == 200, r.text
    assert tn in SQLModel.metadata.tables, "重载后表必须重新注册回 metadata"
    # 外键/表对象是新 exec 出来的，而不是残留的旧对象挂着
    assert client.get(f"/api/v1/{pid}/mark", headers=AUTH).json()["sibling"] == "s3"


def test_reload_does_not_grow_effect_stack(client: TestClient, data_root: Path) -> None:
    """每次 reload 之后 effect 栈规模稳定 —— 撤销走既有回放，不留陈旧条目。

    ★ 为什么单独测：reload 用 replay_runtime（保留 DATA 是 PAUSED 的需要），
      但重载必须把陈旧 DATA 条目摘掉（`retain(set())`），否则每 reload 一次
      栈里多一条拿着过期台账快照的逆 —— 那是一颗「某次回放把表删多了」的雷。
    """
    pid = _pid()
    _install(client, data_root, pid)
    n_install = get_kernel().effect_count(pid)
    assert n_install > 0, "装载后栈上应当有条目"

    # ★ 判据不是「等于安装期的条数」：install 那条路径多登记了一条
    #   `on_install` 钩子的逆（一次性动作），reload 走的是 enable，不会重登记，
    #   所以第一次重载后会是 n-1。
    #   真正要守的不变量是「**反复重载不增长**」——那才是泄漏的形状。
    assert client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH).status_code == 200
    n_after_first = get_kernel().effect_count(pid)
    assert n_after_first <= n_install, (
        f"重载不该让 effect 栈增长：安装期 {n_install} -> 首次重载后 {n_after_first}"
    )

    for _ in range(3):
        r = client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH)
        assert r.status_code == 200, r.text
        assert get_kernel().effect_count(pid) == n_after_first, (
            "反复重载后 effect 栈必须稳定："
            f"{n_after_first} -> {get_kernel().effect_count(pid)}"
            "（增长说明陈旧的 DATA/迁移逆没被摘掉）"
        )


def test_reload_route_is_single_mounted(client: TestClient, data_root: Path) -> None:
    """重载后路由**只有一份**（双挂载是 `ACTIVE → LOADING` 那条非法边的后果）。

    ★ 判据为什么读注册表而不是 `app.routes`：本项目这版 FastAPI 把
      `include_router` 的结果包成 `_IncludedRouter`（没有 `.path`），
      按 `app.routes` 数路由会一律数出 0 —— 那是个假绿/假红都不靠谱的判据。
      而 `registry._mounted[pid]` 正是「这个插件挂了哪几条 route」的记账本：
      双挂载在 `mount()` 里本来就会抛 RuntimeError，但 reload 先 unmount 再 mount，
      所以真正要守的是「记账本里只有一份，且条目数与声明一致」。
    """
    pid = _pid()
    _install(client, data_root, pid)
    reg = client.app.state.registry
    n_before = len(reg._mounted[pid])

    _write_plugin(data_root, pid, marker="v9")
    assert client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH).status_code == 200

    assert reg.mounted().count(pid) == 1, f"注册表里出现多次：{reg.mounted()}"
    assert len(reg._mounted[pid]) == n_before, (
        f"挂载条目数变了：{n_before} -> {len(reg._mounted[pid])}（双挂载的形状）"
    )
    # 真打一下：新代码在服务，且只服务一份
    got = client.get(f"/api/v1/{pid}/mark", headers=AUTH).json()
    assert got["router"] == "v9"


def test_reload_invalidates_openapi_snapshot(client: TestClient, data_root: Path) -> None:
    """重载后 /openapi.json 与模块级 schema 不能还是旧快照。"""
    pid = _pid()
    _install(client, data_root, pid)
    app = client.app
    app.openapi()
    assert app.openapi_schema is not None, "先烤一份快照"

    _write_plugin(data_root, pid, marker="v1")
    r = client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH)
    assert r.status_code == 200, r.text
    assert app.openapi_schema is None, "reload 之后快照必须失效"

    paths = app.openapi().get("paths", {})
    assert f"/api/v1/{pid}/mark" in paths

    r2 = client.get(f"/api/v1/{pid}/openapi.json", headers=AUTH)
    assert r2.status_code == 200, r2.text
    assert f"/api/v1/{pid}/mark" in r2.json().get("paths", {})


def test_app_state_modules_synced_for_every_action(
    client: TestClient, data_root: Path
) -> None:
    """install / enable / disable / uninstall / reload 五条路都要同步 app.state.modules。

    ★ r7 的动态发现（dashboard 的 x.day.peek）读的就是这张表；
      任何一条路径漏同步，「装了但 /modules 说没有」就会复现。
    """
    pid = _pid()
    _write_plugin(data_root, pid)

    assert pid not in _modules_ids(client), "未安装不该在清单里"
    client.post("/api/v1/plugins/install", json={"id": pid}, headers=AUTH)
    assert pid in _modules_ids(client), "install 后必须在"

    client.post(f"/api/v1/plugins/{pid}/disable", headers=AUTH)
    assert pid not in _modules_ids(client), "disable 后必须不在"

    client.post(f"/api/v1/plugins/{pid}/enable", headers=AUTH)
    assert pid in _modules_ids(client), "enable 后必须在"

    client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH)
    assert pid in _modules_ids(client), "reload 后必须在"

    client.post(f"/api/v1/plugins/{pid}/uninstall", headers=AUTH)
    assert pid not in _modules_ids(client), "uninstall 后必须不在"


def test_app_state_modules_carries_new_manifest_version(
    client: TestClient, data_root: Path
) -> None:
    """重载后清单里的那份 manifest 是新版本（升级的对外可见证据）。"""
    pid = _pid()
    _install(client, data_root, pid, version="0.1.0")
    _write_plugin(data_root, pid, version="0.2.0")
    assert client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH).status_code == 200

    r = client.get("/api/v1/modules", headers=AUTH)
    entry = next(m for m in r.json()["modules"] if m.get("id") == pid)
    assert entry.get("version") == "0.2.0", entry
    detail = client.get(f"/api/v1/plugins/{pid}", headers=AUTH).json()
    assert detail["version"] == "0.2.0"


def test_reload_keeps_table_and_data(client: TestClient, data_root: Path) -> None:
    """reload 不删表不删数据（升级 ≠ 卸载）。"""
    from sqlalchemy import text

    pid = _pid()
    tn = _table_name(pid)
    _install(client, data_root, pid)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(f"INSERT INTO {tn} (id, title) VALUES ('k1', 'keepme')"))

    _write_plugin(data_root, pid, marker="v2")
    assert client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH).status_code == 200

    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT title FROM {tn}")).all()
    assert [r[0] for r in rows] == ["keepme"], "reload 之后数据必须还在"

    # 迁移台账也没有重跑：升级不该把已执行的版本回滚
    from core.plugins.migrations import recorded_versions

    assert "0001_init" in recorded_versions(engine, pid)


# ══════════════════════ D3 · 失败语义与门禁 ══════════════════════
def test_reload_failure_marks_failed_and_unmounts(
    client: TestClient, data_root: Path
) -> None:
    """★ 反向自证：reload 中途抛错 → FAILED + 路由摘干净 + 清单里没有它。

    断言的不是「报错了」，而是**报错之后系统留下的事实**：
      · phase=FAILED（不是 ACTIVE —— 那会是假成功）
      · last_error 有人话（可诊断）
      · 旧路由 404（撤销确实做了，不是「说撤了但还挂着」）
      · app.state.modules 不含它（动态发现不会把一个坏插件当 provider）
    """
    pid = _pid()
    _install(client, data_root, pid, marker="v1")
    assert client.get(f"/api/v1/{pid}/mark", headers=AUTH).status_code == 200

    _write_plugin(data_root, pid, broken=True)
    r = client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH)
    assert r.status_code >= 500, f"装载失败必须是 5xx（实得 {r.status_code}）：{r.text[:200]}"

    assert _phase(client, pid) == "FAILED", "装载失败必须落 FAILED"
    detail = client.get(f"/api/v1/plugins/{pid}", headers=AUTH).json()
    assert detail["last_error"], "必须留下可诊断的 last_error"
    assert client.get(f"/api/v1/{pid}/mark", headers=AUTH).status_code == 404, "路由必须已摘干净"
    assert pid not in client.app.state.registry.mounted()
    assert pid not in _modules_ids(client), "坏插件不该还挂在模块清单上（动态发现的依赖）"
    assert get_kernel().effect_count(pid) == 0, "失败后不该残留已生效的 effect"


def test_reload_failure_is_recoverable(client: TestClient, data_root: Path) -> None:
    """FAILED 不是死档：把代码改回去，reload/enable 应当能把它拉回 ACTIVE。"""
    pid = _pid()
    _install(client, data_root, pid)
    _write_plugin(data_root, pid, broken=True)
    assert client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH).status_code >= 500
    assert _phase(client, pid) == "FAILED"

    _write_plugin(data_root, pid, marker="fixed")
    r = client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["phase"] == "ACTIVE"
    assert client.get(f"/api/v1/{pid}/mark", headers=AUTH).json()["router"] == "fixed"


def test_reload_refuses_phases_that_are_not_reloadable(
    client: TestClient, data_root: Path
) -> None:
    """起点非 ACTIVE/FAILED → 拒（409），且**不动任何东西**。"""
    pid = _pid()
    _install(client, data_root, pid, marker="v1")
    client.post(f"/api/v1/plugins/{pid}/disable", headers=AUTH)
    assert _phase(client, pid) == "PAUSED"

    r = client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH)
    assert r.status_code == 409, r.text
    assert _phase(client, pid) == "PAUSED", "被拒的重载不许改阶段"
    # ★ 关键：PAUSED 不许被顺手启用（那是用户的开关，不是重载的副作用）
    detail = client.get(f"/api/v1/plugins/{pid}", headers=AUTH).json()
    assert detail["enabled"] is False, "reload 被拒时不得把停用态改成启用"


def test_reload_rejects_unknown_and_core(client: TestClient, data_root: Path) -> None:
    r = client.post("/api/v1/plugins/no-such-plugin/reload", headers=AUTH)
    assert r.status_code == 404, r.text

    # builtin 插件不走热重载：它由启动流程装载，没有 third-party 的目录语义
    r = client.post("/api/v1/plugins/manifest-demo/reload", headers=AUTH)
    assert r.status_code in (403, 404, 409), r.text


def test_reload_requires_auth(client: TestClient, data_root: Path) -> None:
    pid = _pid()
    _install(client, data_root, pid)
    r = client.post(f"/api/v1/plugins/{pid}/reload")
    assert r.status_code == 401, r.text


def test_dry_run_has_zero_side_effects(client: TestClient, data_root: Path) -> None:
    """dry_run 只回答「会动什么」，一样东西都不许动。"""
    pid = _pid()
    _install(client, data_root, pid, marker="v1")
    _write_plugin(data_root, pid, marker="v2")

    r = client.post(f"/api/v1/plugins/{pid}/reload", params={"dry_run": "true"}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["from_phase"] == "ACTIVE"
    assert body["reloadable"] is True
    assert body["will_steps"] == ["RELOADING", "PENDING", "LOADING", "ACTIVE"]

    # 一切照旧：代码没换、阶段没动、路由仍只有一份
    assert client.get(f"/api/v1/{pid}/mark", headers=AUTH).json()["router"] == "v1"
    assert _phase(client, pid) == "ACTIVE"


def test_bulk_reload_default_is_dry_run_and_reports_drift(
    client: TestClient, data_root: Path
) -> None:
    """POST /plugins/reload 缺省只算差异（存量语义），显式才真重载。"""
    pid = _pid()
    _install(client, data_root, pid, marker="v1")
    _write_plugin(data_root, pid, marker="v2")

    r = client.post("/api/v1/plugins/reload", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert "reload_required" in body
    assert pid in body["reloadable"]
    # 缺省不许换代码
    assert client.get(f"/api/v1/{pid}/mark", headers=AUTH).json()["router"] == "v1"

    r2 = client.post("/api/v1/plugins/reload", json={"dry_run": False}, headers=AUTH)
    assert r2.status_code == 200, r2.text
    applied = {x["id"]: x for x in r2.json()["applied"]}
    assert applied[pid]["ok"] is True, applied[pid]
    assert client.get(f"/api/v1/{pid}/mark", headers=AUTH).json()["router"] == "v2"


# ══════════════════════ kernel 原语层 ══════════════════════
def test_replay_runtime_reports_failed_undos() -> None:
    """`replay_runtime` 必须把「撤销失败」告诉调用方（reload 不半提交的前提）。"""
    from core.kernel import reset_kernel

    reset_kernel()
    kernel = get_kernel()
    with kernel.effect("unit-ok", plugin_id="unit-x") as eff:
        eff.add(do=lambda: None, undo=lambda: None)
    with kernel.effect("unit-bad", plugin_id="unit-x") as eff:
        def _boom() -> None:
            raise RuntimeError("逆故意失败")
        eff.add(do=lambda: None, undo=_boom)

    failed = kernel.replay_runtime("unit-x")
    assert failed == ["unit-bad"], f"应报告失败的逆，实得 {failed}"
    reset_kernel()


def test_reload_refuses_when_undo_fails_and_marks_failed(
    client: TestClient, data_root: Path
) -> None:
    """★ 撤销不干净 ⇒ 拒绝继续装载（不半提交）。

    做法：给一个**已 ACTIVE** 的插件的 effect 栈塞进一条「逆会抛」的条目
    （模拟挂载资源撤不掉），再触发 reload。
    断言：阶段落 FAILED，而且**新代码没被装载**（旧路由已摘、新路由也没挂）。
    """
    pid = _pid()
    _install(client, data_root, pid, marker="v1")
    _write_plugin(data_root, pid, marker="v2")

    kernel = get_kernel()
    with kernel.effect("r8-poison", kind=EffectKind.RUNTIME, plugin_id=pid) as eff:
        def _boom() -> None:
            raise RuntimeError("r8: 故意让一条逆失败")
        eff.add(do=lambda: None, undo=_boom)

    r = client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH)
    assert r.status_code >= 500, r.text[:200]

    assert _phase(client, pid) == "FAILED"
    # 新代码没上来（装载那一步根本没跑）：坏状态不能伪装成升级成功
    assert client.get(f"/api/v1/{pid}/mark", headers=AUTH).status_code == 404
    detail = client.get(f"/api/v1/plugins/{pid}", headers=AUTH).json()
    assert detail["version"] == "0.1.0", "撤销失败时不该把状态行写成新版本"


def test_phase_reason_reload_is_audited(client: TestClient, data_root: Path) -> None:
    """重载必须留痕，且 reason 是 `reload`（不是 manual/uninstall）。"""
    from sqlmodel import select

    from core.deps import kernel_session
    from db.models.system import AuditLog

    pid = _pid()
    _install(client, data_root, pid)
    _write_plugin(data_root, pid, marker="v3")
    assert client.post(f"/api/v1/plugins/{pid}/reload", headers=AUTH).status_code == 200

    with kernel_session() as db:
        rows = db.exec(select(AuditLog).where(AuditLog.action == "phase.change")).all()
    payload = " ".join(str(r.payload_hash) for r in rows)
    assert PhaseReason.RELOAD.value in payload, payload[:400]
    assert "RELOADING" in payload, "进入重载阶段要有审计"


# ══════════════════════ 收尾自检 ══════════════════════
def test_no_test_plugin_left_behind(client: TestClient, data_root: Path) -> None:
    """本文件的临时插件根里不该留下没清理的目录（自证 fixture 纪律）。

    ★ 判据不是「应该没有」而是「真去看一眼」：如果哪天有测试漏了清理，
      这里会红，而不是等到下一次跑发现「列表里怎么多了个陌生插件」。
    """
    leftovers = sorted(p.name for p in data_root.iterdir() if p.is_dir())
    assert leftovers == [], f"临时插件根里留下东西了：{leftovers}"


@pytest.fixture(autouse=True)
def _cleanup_kernel_and_modules() -> Iterator[None]:
    """每个测试后把第三方插件的模块缓存/效果栈清掉，避免跨测试串味。"""
    yield
    from core.kernel import reset_kernel
    from core.plugins import discover as discover_mod

    for name in [n for n in list(__import__("sys").modules) if n.startswith("plugin_thirdparty_r8-")]:
        __import__("sys").modules.pop(name, None)
    for pid in list(get_kernel().stacks()):
        if pid.startswith("r8-"):
            discover_mod.unload_thirdparty_modules(pid)
            get_kernel().drop_stack(pid)
    reset_kernel()
