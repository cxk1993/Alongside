# ── 数据作用域 · 接线判据（G16） ──
"""数据作用域的**运行时**判据。

★ 为什么这一组判据必须「跑起来」，而不是 grep 字符串
  --------------------------------------------------
2026-11-09 实测抓到的反例：`db/engine.py` 里

    @event.listens_for(target, "checkin")
    def _scope_checkin(dbapi_connection):        # ← 只收 1 个参数
        clear_scope_on_checkin(dbapi_connection)

而 SQLAlchemy 的 `PoolEvents.checkin` 签名是
`(dbapi_connection, connection_record)` —— 每次**连接归还池**都抛
`TypeError: takes 1 positional argument but 2 were given`，
连接因此不被回收，第一个请求就把 QueuePool（10 + 20）耗尽：

    sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 20 reached

「文件里有 `event.listens_for`」这句话在这个 bug 面前**完全为真**。
⇒ 所以本文件的判据是：**建真引擎 → 真跑语句 → 真看拒绝/放行与池状态**。

★ 三态语义（`core/db_scope.py:apply_scope_on_begin`）
  · Session 上没有 `db_scope` 键  → deny-all（忘了声明 = 拒绝）
  · `db_scope = None`（显式）     → 内核特权，放行
  · `db_scope = "<id>"`           → 只放行 `{id}_*` 的表
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))


# ───────────────────────── 夹具 ─────────────────────────


@pytest.fixture()
def engine(tmp_path):
    """真引擎（走 `db.engine.make_engine`，因此接线是否生效会如实反映）。"""
    from db.engine import make_engine

    db_file = tmp_path / f"scope_{uuid.uuid4().hex[:8]}.db"
    eng = make_engine(f"sqlite:///{db_file.as_posix()}")
    # 建两张表：一张内核表、一张模块表
    from db.models.system import AppSetting
    from modules.todo.models import TodoItem

    with eng.begin() as conn:
        AppSetting.__table__.create(bind=conn, checkfirst=True)
        TodoItem.__table__.create(bind=conn, checkfirst=True)
    try:
        yield eng
    finally:
        eng.dispose()


def _as_scope(session, scope):
    from core.db_scope import scope_session

    if scope is not _UNDECLARED:
        scope_session(session, scope)


class _Undeclared:
    """哨兵：表示「一个字都没声明」（不调用 scope_session）。"""


_UNDECLARED = _Undeclared()


def _query(engine, scope, sql):
    """在给定作用域下执行一条语句，返回 'ok' 或异常。"""
    from sqlalchemy import text
    from sqlmodel import Session

    s = Session(engine)
    _as_scope(s, scope)
    try:
        s.exec(text(sql)).all()
        return None
    except Exception as exc:  # noqa: BLE001 —— 判据要看异常类型
        return exc
    finally:
        s.close()


# ───────────────────────── 1. 接线存在性 ─────────────────────────


def test_after_begin_is_registered(tmp_path):
    """`apply_scope_on_begin` 必须真的挂在 Session 类的 after_begin 上。"""
    from sqlalchemy import event
    from sqlalchemy.orm import Session as SASession

    from core.db_scope import apply_scope_on_begin
    from db.engine import make_engine

    # 注意：不能用 `sqlite://`（内存库走 SingletonThreadPool，不认 pool 参数）
    make_engine(f"sqlite:///{(tmp_path / 'wiring.db').as_posix()}")
    assert event.contains(SASession, "after_begin", apply_scope_on_begin), (
        "after_begin 未注册：core/db_scope.apply_scope_on_begin 是死代码，"
        "数据边界在运行时不存在"
    )


# ───────────────────────── 2. 三态语义 ─────────────────────────


def test_undeclared_session_is_denied(engine):
    """**没有声明作用域**的 Session 必须被 deny-all 拦下（默认拒绝）。"""
    exc = _query(engine, _UNDECLARED, "SELECT COUNT(*) FROM app_setting")
    assert exc is not None, (
        "未声明作用域的 Session 读到了内核表 ⇒ 默认放行，"
        "定时任务 / 事件处理器 / 生命周期钩子全部在边界外"
    )
    assert "not authorized" in str(exc).lower()


def test_kernel_scope_allows_all(engine):
    """显式 `None`（kernel_session 语义）放行一切表。"""
    assert _query(engine, None, "SELECT COUNT(*) FROM app_setting") is None
    assert _query(engine, None, "SELECT COUNT(*) FROM todo_item") is None


def test_plugin_scope_allows_own_and_denies_other(engine):
    """插件作用域：自己的表放行，内核表与别人的表拒绝。"""
    assert _query(engine, "todo", "SELECT COUNT(*) FROM todo_item") is None
    exc = _query(engine, "todo", "SELECT COUNT(*) FROM app_setting")
    assert exc is not None and "not authorized" in str(exc).lower()
    # 写也要拦（只拦 SELECT 是历史实测过的漏洞）
    exc = _query(engine, "todo", "DELETE FROM app_setting")
    assert exc is not None and "not authorized" in str(exc).lower()


def test_pragma_is_allowed(engine):
    """PRAGMA 必须放行，否则 ORM 与迁移代码自己就挂。"""
    assert _query(engine, "todo", "PRAGMA table_info(todo_item)") is None


# ───────────────────────── 3. 连接不泄漏（checkin 签名） ─────────────────────────


def test_checkin_handler_does_not_leak_pool(engine):
    """★ 端到端判据：60 次 Session 往返后，池里不能有借出未还的连接。

    这一条专抓 `PoolEvents.checkin` 的 handler 签名错误：
    签名错 ⇒ 归还时抛 TypeError ⇒ 连接不回收 ⇒ 池耗尽 ⇒ 第一个请求就栈死。
    它**不能**被任何静态检查替代。
    """
    from sqlalchemy import text
    from sqlmodel import Session

    for _ in range(60):
        s = Session(engine)
        try:
            s.exec(text("SELECT 1"))
        finally:
            s.close()

    status = engine.pool.status()
    assert "Current Checked out connections: 0" in status, f"连接泄漏：{status}"


# ───────────────────────── 4. 只约束第三方 ─────────────────────────


def test_mount_plugin_scopes_only_third_party(monkeypatch):
    """`mount_plugin` 必须按 `kind` 决定挂不挂作用域。

    内置模块是系统的一部分（dashboard / query 直接读 calendar_event、todo_item…），
    给它们挂作用域会把正常功能拦死，且因为那些代码里有 `except: pass`，
    症状会是**数据静默变空**而不是报错。
    """

    class FakeRegistry:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        def mounted(self):
            return []

        def mount(self, plugin_id, router, prefix, scoped=True):
            self.calls.append((plugin_id, scoped))

    from fastapi import APIRouter

    import core.plugins.discover as disc
    from core.plugins.discover import PluginInfo

    # 不落真实 router 文件：判据只关心 scoped 传参
    monkeypatch.setattr(disc, "load_plugin_router", lambda info: APIRouter())

    def info(kind: str) -> PluginInfo:
        return PluginInfo(
            id=f"probe-{kind}",
            kind=kind,
            source="third-party" if kind == "third-party" else "builtin",
            manifest={"id": f"probe-{kind}", "api": {"base": "/p"}},
            directory=_API_ROOT,
        )

    reg = FakeRegistry()
    disc.mount_plugin(info("third-party"), reg)
    disc.mount_plugin(info("builtin"), reg)
    disc.mount_plugin(info("core"), reg)

    got = dict(reg.calls)
    assert got["probe-third-party"] is True, "第三方插件必须挂作用域"
    assert got["probe-builtin"] is False, "内置模块不能挂作用域（会误伤跨模块读）"
    assert got["probe-core"] is False, "核心模块不能挂作用域"


def test_registry_mount_scoped_flag_controls_dependency():
    """`ModuleRegistry.mount(scoped=...)` 必须真的决定路由上有没有作用域依赖。"""
    from fastapi import APIRouter, FastAPI
    from fastapi.routing import _IncludedRouter

    from core.registry import ModuleRegistry

    app = FastAPI()
    reg = ModuleRegistry()
    reg.bind(app)

    def router():
        r = APIRouter()

        @r.get("/x")
        def _x():
            return {"ok": True}

        return r

    reg.mount("demo-scoped", router(), "/a", scoped=True)
    reg.mount("demo-unscoped", router(), "/b", scoped=False)

    def deps_of(prefix: str):
        for route in app.routes:
            if isinstance(route, _IncludedRouter) and route.include_context.prefix == prefix:
                return route.include_context.dependencies
        raise AssertionError(f"没找到 {prefix} 的挂载")

    assert deps_of("/a"), "scoped=True 却没挂依赖"
    assert not deps_of("/b"), "scoped=False 却挂了依赖"


# ───────────────────────── 5. 第三方不许拿内核特权 ─────────────────────────


def test_third_party_plugins_never_import_privileged_session():
    """`plugins/**`（第三方插件目录）不许 import `kernel_session`。

    ★ 口径：`core/` 与 `modules/`（内置模块）是系统的一部分，可以用；
      第三方插件只能 `db_session("<id>")`。
      这条与 `registry.mount(scoped=(kind == "third-party"))` 是同一个判断的两面。
    """
    plugins_dir = _API_ROOT.parent.parent / "plugins"
    if not plugins_dir.is_dir():
        pytest.skip("本机没有 plugins/ 目录（无第三方插件）")

    offenders = []
    for py in plugins_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        if "kernel_session" in text:
            offenders.append(str(py.relative_to(plugins_dir)))
    assert not offenders, f"第三方插件拿到了内核会话特权：{offenders}"
