#!/usr/bin/env python
"""数据作用域接线门禁（G16）—— 「边界真的存在」的运行时判据。

★ 为什么需要这个文件
--------------------
2026-11-09 的「翻转默认值」把 `core/db_scope.py` 从**死代码**接进了运行时：
`db/engine.py` 注册 `after_begin` / `checkin`，`core/deps.py` 把会话分成
`kernel_session()`（特权）与 `db_session(plugin_id)`（插件边界），
`registry.mount(scoped=...)` 只给 `kind == "third-party"` 挂作用域依赖。

这四件事任何一件被改回去，数据边界就**静默消失**，而代码看上去一切正常。
⇒ 所以要有机器判据：跑一次就知道「边界还在不在」。

★ 为什么不能只做静态检查
------------------------
同一天实测抓到的反例：`db/engine.py` 的 checkin handler 写成了单参数

    def _scope_checkin(dbapi_connection): ...

而 SQLAlchemy `PoolEvents.checkin` 的签名是 `(dbapi_connection, connection_record)`。
每次**连接归还池**都抛 `TypeError` ⇒ 连接不回收 ⇒ QueuePool(10+20) 耗尽 ⇒
**服务第一个请求就栈死**。而「文件里有 event.listens_for」这句话完全为真。
⇒ 本门禁的 W2~W5 一律**建真引擎、真跑语句、真看拒绝与池状态**。

★ 三态纪律（照 `verify_pack_invariants.py`）
-------------------------------------------
· 每条判据单独跑，一条失败不影响其余；
· 判不了记 SKIP 并写明原因，**不记 PASS**；
· 失败时给出「改哪一行」的定位，而不只是「红了」。

用法：
    python tools/gates/verify_db_scope_wiring.py
    python tools/gates/verify_db_scope_wiring.py --json
    python tools/gates/verify_db_scope_wiring.py --verbose

退出码：0 = 全部 PASS（允许 SKIP）；1 = 有 FAIL / 环境加载失败。
"""
from __future__ import annotations

# pyright: reportMissingImports=false

import argparse
import json
import os
import sys
import tempfile
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1]
_REPO = _TOOLS.parent
_API = _REPO / "services" / "api"
for _entry in (str(_TOOLS), str(_API)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


@dataclass
class Finding:
    key: str
    title: str
    level: str
    detail: str = ""
    hint: str = ""


@dataclass
class Ctx:
    engine: object | None = None
    findings: list[Finding] = field(default_factory=list)
    load_error: str = ""

    def add(self, key: str, title: str, level: str, detail: str = "", hint: str = "") -> None:
        self.findings.append(Finding(key, title, level, detail, hint))


def _bootstrap() -> Ctx:
    """导入内核并由 `db.engine.make_engine` 建一个临时引擎。

    ★ 必须走 make_engine：接线（`_apply_db_scope`）就在它内部。
      自己 create_engine 会绕过被检对象，测出来的是「我另建的引擎」。
    """
    ctx = Ctx()
    # Settings 的三个必填项兜底（门禁不读、也不打印任何真实凭据）
    for k in ("SECRET_KEY", "ADMIN_PASSWORD_HASH", "TOTP_SECRET"):
        os.environ.setdefault(k, "gate-" + "x" * 32)

    try:
        from db.engine import make_engine

        tmp = Path(tempfile.mkdtemp(prefix="db_scope_gate_"))
        db_file = tmp / f"gate_{uuid.uuid4().hex[:8]}.db"
        ctx.engine = make_engine(f"sqlite:///{db_file.as_posix()}")
    except Exception as exc:  # noqa: BLE001
        ctx.load_error = f"{type(exc).__name__}: {exc}"
    return ctx


def _create_tables(engine) -> None:
    from db.models.system import AppSetting
    from modules.todo.models import TodoItem

    with engine.begin() as conn:
        AppSetting.__table__.create(bind=conn, checkfirst=True)
        TodoItem.__table__.create(bind=conn, checkfirst=True)


_UNDECLARED = object()


def _run_sql(engine, scope, sql: str):
    """在指定作用域下执行一条语句；返回 None=放行，异常=被拒。"""
    from sqlalchemy import text
    from sqlmodel import Session

    from core.db_scope import scope_session

    s = Session(engine)
    if scope is not _UNDECLARED:
        scope_session(s, scope)
    try:
        s.exec(text(sql)).all()
        return None
    except Exception as exc:  # noqa: BLE001
        return exc
    finally:
        s.close()


# ─────────────────────────── 判据 ───────────────────────────


def w1_after_begin_registered(ctx: Ctx) -> None:
    """W1 · `after_begin` 真的挂在 Session 类上。"""
    key, title = "W1", "after_begin 已注册"
    try:
        from sqlalchemy import event
        from sqlalchemy.orm import Session as SASession

        from core.db_scope import apply_scope_on_begin

        if event.contains(SASession, "after_begin", apply_scope_on_begin):
            ctx.add(key, title, PASS)
        else:
            ctx.add(
                key, title, FAIL,
                "core/db_scope.apply_scope_on_begin 没有任何监听器 —— 它是死代码，"
                "数据边界在运行时根本不存在",
                "在 db/engine.py 的 _apply_db_scope() 里 event.listens_for(Session, 'after_begin')",
            )
    except Exception as exc:  # noqa: BLE001
        ctx.add(key, title, FAIL, f"{type(exc).__name__}: {exc}")


def w2_undeclared_denied(ctx: Ctx) -> None:
    """W2 · 没有声明作用域的会话读任何表都被拒（默认拒绝）。"""
    key, title = "W2", "未声明作用域 = 拒绝"
    if ctx.engine is None:
        ctx.add(key, title, SKIP, "引擎未建成，无法判定")
        return
    exc = _run_sql(ctx.engine, _UNDECLARED, "SELECT COUNT(*) FROM app_setting")
    if exc is None:
        ctx.add(
            key, title, FAIL,
            "未声明作用域的 Session 读到了内核表 app_setting ⇒ 默认是**放行**，"
            "定时任务 / 事件处理器 / 生命周期钩子全部在边界外",
            "core/db_scope.py:apply_scope_on_begin 的『没有 db_scope 键』分支要挂 make_deny_all_authorizer()",
        )
    elif "not authorized" in str(exc).lower():
        ctx.add(key, title, PASS)
    else:
        ctx.add(key, title, FAIL, f"被拒但原因不是 authorizer：{type(exc).__name__}: {exc}")


def w3_kernel_privilege(ctx: Ctx) -> None:
    """W3 · 显式 `None`（kernel_session 语义）放行一切表。"""
    key, title = "W3", "kernel_session 特权放行"
    if ctx.engine is None:
        ctx.add(key, title, SKIP, "引擎未建成，无法判定")
        return
    exc = _run_sql(ctx.engine, None, "SELECT COUNT(*) FROM app_setting")
    if exc is None:
        ctx.add(key, title, PASS)
    else:
        ctx.add(
            key, title, FAIL,
            f"显式 None 仍被拒：{type(exc).__name__}: {exc}",
            "kernel_session() 的 scope_session(session, None) 必须映射到『卸下 authorizer』",
        )


def w4_plugin_whitelist(ctx: Ctx) -> None:
    """W4 · 插件作用域只放行自己的前缀（读/写/删都要拦）。"""
    key, title = "W4", "插件作用域白名单（读/写/删）"
    if ctx.engine is None:
        ctx.add(key, title, SKIP, "引擎未建成，无法判定")
        return
    problems: list[str] = []

    own = _run_sql(ctx.engine, "todo", "SELECT COUNT(*) FROM todo_item")
    if own is not None:
        problems.append(f"自己的表被拒：{str(own).splitlines()[0][:80]}")

    for sql, what in (
        ("SELECT COUNT(*) FROM app_setting", "读内核表"),
        ("DELETE FROM app_setting", "删内核表"),
        ("INSERT INTO app_setting (key, value_json) VALUES ('x','1')", "写内核表"),
    ):
        exc = _run_sql(ctx.engine, "todo", sql)
        if exc is None:
            problems.append(f"{what}被放行")
        elif "not authorized" not in str(exc).lower():
            problems.append(f"{what}被拒但原因可疑：{type(exc).__name__}")

    if problems:
        ctx.add(key, title, FAIL, "；".join(problems))
    else:
        ctx.add(key, title, PASS)


def w5_pool_not_leaked(ctx: Ctx) -> None:
    """W5 · 连接归还时不抛错、不泄漏（专抓 checkin handler 签名）。"""
    key, title = "W5", "连接不泄漏（checkin 签名）"
    if ctx.engine is None:
        ctx.add(key, title, SKIP, "引擎未建成，无法判定")
        return
    from sqlalchemy import text
    from sqlmodel import Session

    try:
        for _ in range(60):
            s = Session(ctx.engine)
            try:
                s.exec(text("SELECT 1"))
            finally:
                s.close()
    except TypeError as exc:
        ctx.add(
            key, title, FAIL,
            f"归还连接时抛 TypeError：{exc}",
            "db/engine.py 的 checkin handler 必须是 "
            "`def _scope_checkin(dbapi_connection, connection_record):`"
            "（SQLAlchemy PoolEvents.checkin 固定传两个参数）",
        )
        return
    except Exception as exc:  # noqa: BLE001
        ctx.add(key, title, FAIL, f"{type(exc).__name__}: {exc}")
        return

    status = ctx.engine.pool.status()  # type: ignore[attr-defined]
    if "Current Checked out connections: 0" in status:
        ctx.add(key, title, PASS, status)
    else:
        ctx.add(key, title, FAIL, f"连接未归还：{status}")


def w6_scope_only_third_party(ctx: Ctx) -> None:
    """W6 · 只有 `kind == "third-party"` 才挂作用域依赖。"""
    key, title = "W6", "作用域只约束第三方插件"
    try:
        from fastapi import APIRouter

        import core.plugins.discover as disc
        from core.plugins.discover import PluginInfo

        class FakeRegistry:
            def __init__(self) -> None:
                self.calls: dict[str, bool] = {}

            def mounted(self):
                return []

            def mount(self, plugin_id, router, prefix, scoped=True):
                self.calls[plugin_id] = scoped

        real_loader = disc.load_plugin_router
        disc.load_plugin_router = lambda info: APIRouter()  # type: ignore[assignment]
        try:
            reg = FakeRegistry()
            for kind in ("third-party", "builtin", "core"):
                info = PluginInfo(
                    id=f"gate-{kind}",
                    kind=kind,
                    source="third-party" if kind == "third-party" else "builtin",
                    manifest={"id": f"gate-{kind}", "api": {"base": "/gate"}},
                    directory=_API,
                )
                disc.mount_plugin(info, reg)
        finally:
            disc.load_plugin_router = real_loader  # type: ignore[assignment]

        wrong = []
        if reg.calls.get("gate-third-party") is not True:
            wrong.append("third-party 没有挂作用域（外来者无边界）")
        if reg.calls.get("gate-builtin") is not False:
            wrong.append("builtin 挂了作用域（会误伤 dashboard/query 的跨模块读）")
        if reg.calls.get("gate-core") is not False:
            wrong.append("core 挂了作用域")
        if wrong:
            ctx.add(key, title, FAIL, "；".join(wrong),
                    "core/plugins/discover.py:mount_plugin 的 scoped=(info.kind == 'third-party')")
        else:
            ctx.add(key, title, PASS)
    except Exception as exc:  # noqa: BLE001
        ctx.add(key, title, FAIL, f"{type(exc).__name__}: {exc}")


def w7_registry_mount_dependency(ctx: Ctx) -> None:
    """W7 · `scoped=` 真的决定路由上有没有作用域依赖（不是摆设参数）。"""
    key, title = "W7", "scoped= 决定路由依赖"
    try:
        from fastapi import APIRouter, FastAPI
        from fastapi.routing import _IncludedRouter

        from core.registry import ModuleRegistry

        app = FastAPI()
        reg = ModuleRegistry()
        reg.bind(app)

        def make_router():
            r = APIRouter()

            @r.get("/x")
            def _x():
                return {"ok": True}

            return r

        reg.mount("gate-scoped", make_router(), "/s", scoped=True)
        reg.mount("gate-unscoped", make_router(), "/u", scoped=False)

        def deps(prefix: str):
            for route in app.routes:
                if isinstance(route, _IncludedRouter) and route.include_context.prefix == prefix:
                    return route.include_context.dependencies
            return None

        d_s, d_u = deps("/s"), deps("/u")
        if d_s is None or d_u is None:
            ctx.add(key, title, FAIL, "没找到本门禁挂载的路由（FastAPI 版本行为变了？）")
        elif not d_s:
            ctx.add(key, title, FAIL, "scoped=True 但路由上没有依赖")
        elif d_u:
            ctx.add(key, title, FAIL, "scoped=False 但路由上仍挂了依赖")
        else:
            ctx.add(key, title, PASS)
    except Exception as exc:  # noqa: BLE001
        ctx.add(key, title, FAIL, f"{type(exc).__name__}: {exc}")


def w8_third_party_no_privilege(ctx: Ctx) -> None:
    """W8 · 第三方插件目录不许 import `kernel_session`。"""
    key, title = "W8", "第三方不拿内核特权"
    plugins_dir = _REPO / "plugins"
    if not plugins_dir.is_dir():
        ctx.add(key, title, SKIP, "本机没有 plugins/ 目录（无第三方插件）")
        return
    offenders = []
    for py in plugins_dir.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "kernel_session" in text:
            offenders.append(str(py.relative_to(_REPO)))
    if offenders:
        ctx.add(key, title, FAIL, "第三方插件拿到了内核会话特权：" + "、".join(offenders),
                "第三方只能 db_session('<plugin_id>')")
    else:
        ctx.add(key, title, PASS)


CHECKS = (
    w1_after_begin_registered,
    w2_undeclared_denied,
    w3_kernel_privilege,
    w4_plugin_whitelist,
    w5_pool_not_leaked,
    w6_scope_only_third_party,
    w7_registry_mount_dependency,
    w8_third_party_no_privilege,
)

MARK = {PASS: "✓", FAIL: "✗", SKIP: "△"}


def run() -> Ctx:
    ctx = _bootstrap()
    if ctx.engine is not None:
        try:
            _create_tables(ctx.engine)
        except Exception as exc:  # noqa: BLE001
            # 建表失败通常是 checkin 签名错误的连带症状；W5 会给出精确定位
            ctx.add("env", "建表（前置）", FAIL, f"{type(exc).__name__}: {exc}")

    for check in CHECKS:
        try:
            check(ctx)
        except Exception as exc:  # noqa: BLE001
            ctx.add(check.__name__, check.__doc__ or check.__name__, FAIL,
                    f"判据自身崩了：{type(exc).__name__}: {exc}")
            if os.environ.get("DB_SCOPE_GATE_DEBUG"):
                traceback.print_exc()

    if ctx.engine is not None:
        try:
            ctx.engine.dispose()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    return ctx


def render(ctx: Ctx) -> str:
    lines = ["数据作用域接线门禁（G16）· 运行时判据", ""]
    if ctx.load_error:
        lines.append(f"✗ 内核加载失败：{ctx.load_error}")
        lines.append("  门禁不能在没有实现的情况下假装通过。")
        return "\n".join(lines)

    for f in ctx.findings:
        lines.append(f"{MARK[f.level]} {f.key} {f.title}：{f.detail or f.level}")
        if f.level == FAIL and f.hint:
            lines.append(f"    → 定位：{f.hint}")

    fails = [f for f in ctx.findings if f.level == FAIL]
    skips = [f for f in ctx.findings if f.level == SKIP]
    lines.append("")
    lines.append(f"结论：{'✗ 有 ' + str(len(fails)) + ' 条不成立' if fails else '✓ 边界存在且按预期生效'}"
                 + (f"（{len(skips)} 条判不了，已如实标注）" if skips else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="数据作用域接线门禁（G16）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--verbose", action="store_true", help="打印每条判据细节")
    args = ap.parse_args(argv)

    ctx = run()
    fails = [f for f in ctx.findings if f.level == FAIL]
    code = 1 if (fails or ctx.load_error) else 0

    if args.json:
        print(json.dumps({
            "ok": not fails and not ctx.load_error,
            "exit_code": code,
            "load_error": ctx.load_error,
            "checks": [
                {"key": f.key, "title": f.title, "level": f.level,
                 "detail": f.detail, "hint": f.hint}
                for f in ctx.findings
            ],
            "summary": {
                lvl: sum(1 for f in ctx.findings if f.level == lvl)
                for lvl in (PASS, FAIL, SKIP)
            },
        }, ensure_ascii=False, indent=2))
    else:
        print(render(ctx))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
