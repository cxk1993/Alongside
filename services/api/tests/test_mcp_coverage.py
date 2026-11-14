"""★ 治本测试：MCP 工具面对后端的**完整性**（发现式，非清单式）。

## 为什么需要这个文件

2026-09-27 那次「打通往路径参数型端点」只给 docs / habits / todo 三个模块补了
`provides`，其余 **12 个模块**的 PATCH/DELETE 端点后端明明有、工具面却一个都没有。
后果：AI **只能新建、不能改也不能删** —— 改一条日程标题都只能请主人手动点。

这个洞在 **42 个测试全绿**的情况下潜伏了 7 天。原因不是没测试，而是
测试只断言「若干已知工具存在」（**清单式**：只证明已知的还在），
没有任何一条会去问「**后端有、工具面没有的，还有多少？**」（**发现式**）。

本文件补的就是后者：**不写期望清单，直接扫后端真身**。
以后任何模块新增 PATCH/PUT/DELETE 端点而忘了补 provides，这里立刻变红。

## 双向验证纪律（坑谱 #67(d)）

「测试全绿 ≠ 修复生效」。本文件另有 `test_coverage_guard_can_fail` 一条
自证测试：它验证本守卫**真的会失败**（用一个已知不覆盖的假端点）。
只写正向断言的话，守卫自身坏掉时照样全绿。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from core.app import create_app
from db.engine import get_engine, init_engine


@pytest.fixture(scope="module")
def client():
    """独立 client（与 test_mcp.py 同款 setup 纪律）。

    建全部已注册表（checkfirst 幂等）+ 显式 import mcp models 消除 import 顺序依赖，
    否则会「首跑红、复跑绿」。本文件只做只读扫描，但 openapi 端点仍需 app 起来。
    """
    init_engine()
    engine = get_engine()
    from modules.mcp import models as _mcp_models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()

# ── 豁免表：这些改动型端点**有意不进工具面** ─────────────────────────
# 判准：不是「AI 该不该能干」，而是「它是不是给 AI 用的能力」。
#   - 调度器/心跳类：由服务端 cron 触发，不是人/AI 的操作单位
#   - 同步/导入导出类：批量运维动作，误触代价大且非幂等
#   - 健康/元信息类：只读探针与内核自省
# 新增豁免必须在这里写明理由 —— 这就是这条守卫的全部意义。
_EXEMPT_PATTERNS: list[tuple[str, str]] = [
    (r"/health$", "健康探针，只读运维"),
    (r"/manifest$", "插件清单，内核自省"),
    (r"/reminders/tick$", "提醒调度器心跳，服务端触发"),
    (r"/due/tick$", "到期调度器心跳，服务端触发"),
    (r"/(due|reminders)/scheduler$", "调度器状态查询"),
    (r"/followups/reconcile$", "健康随访对账，后台批量作业"),
    (r"/snapshots/sync$", "财务快照同步，后台批量作业"),
    (r"/libs/\{[^}]+\}/sync$", "笔记库同步，后台批量作业"),
    (r"/sync$", "笔记总同步，后台批量作业"),
    (r"/session$", "iSDU 退出登录：只清进程内存里的凭据，安全动作，不暴露给 AI 调用"),
    (r"/ingest(-all)?$", "复盘源批量灌入，后台作业"),
    (r"/export$", "批量导出，人工触发的运维动作"),
    (r"/import$", "批量导入，人工触发的运维动作"),
    (r"/consolidate$", "日记归并，后台作业"),
    (r"/reorder$", "侧栏重排，一次提交整套顺序（前端专用批量语义）"),
    (r"/touch$", "最近访问打点，前端渲染副作用"),
    # ★ 2026-10-04 主人裁定：这两条**也放开**（原为豁免待裁）。
    #   记录在此仅为留痕，不再豁免 —— 它们已补进 mcp / plugins 的 manifest provides。
    #   暴露的代价与对策：PAT 自我管理 = AI 能改自己的 scope（权限自我提升面），
    #   内核插件配置误改波及其它模块 —— 主任已明确知晓并同意。
]

_EXEMPT_RE = [(re.compile(p), why) for p, why in _EXEMPT_PATTERNS]

# 只看这三类方法：它们才是「改动既有数据」的能力，也才是曾被整类漏掉的那批。
_MUTATING = {"patch", "put", "delete"}


def _module_ids() -> list[str]:
    """从磁盘枚举模块（不依赖注册表，测试更稳）。"""
    api_root = Path(__file__).resolve().parents[1]
    return sorted(
        p.parent.name
        for p in (api_root / "modules").glob("*/manifest.json")
    )


def _is_exempt(path: str) -> str | None:
    for rx, why in _EXEMPT_RE:
        if rx.search(path):
            return why
    return None


def _mutation_endpoints(client) -> dict[str, list[tuple[str, str]]]:
    """{module: [(METHOD, path), ...]} —— 后端真实存在的改动型端点。"""
    found: dict[str, list[tuple[str, str]]] = {}
    for module in _module_ids():
        try:
            spec = client.get(f"/api/{module}/openapi.json").json()
        except Exception:  # noqa: BLE001 —— 模块没有 openapi 就跳过，不误报
            continue
        rows: list[tuple[str, str]] = []
        for path, ops in (spec.get("paths") or {}).items():
            if not isinstance(ops, dict):
                continue
            for method in ops:
                if method.lower() in _MUTATING:
                    rows.append((method.upper(), path))
        if rows:
            found[module] = sorted(rows)
    return found


def _tool_surface(client) -> set[tuple[str, str]]:
    """工具面已暴露的 (METHOD, path) 集合。"""
    from modules.mcp.registry_adapter import build_tool_map

    return {(t.method, t.path) for t in build_tool_map()}


def test_no_mutating_endpoint_left_behind(client):
    """★ 核心守卫：后端每个改动型端点，要么工具面可达，要么显式豁免。

    失败信息的读法：
        某模块下列出 (METHOD, path) —— 说明后端有、工具面没有。
        修法二选一：① 在 modules/<模块>/manifest.json 里补一条 provides
        （配 api.tools 的细粒度 `resource.verb` 键）；② 若确实不该给 AI，
        在 _EXEMPT_PATTERNS 里加一条并写明理由。
    """
    surface = _tool_surface(client)
    endpoints = _mutation_endpoints(client)
    assert endpoints, "一个改动型端点都没扫到 —— OpenAPI 路径可能变了，守卫已失效"

    uncovered: dict[str, list[str]] = {}
    for module, rows in endpoints.items():
        misses = []
        for method, path in rows:
            if (method, path) in surface:
                continue
            if _is_exempt(path):
                continue
            misses.append(f"{method} {path}")
        if misses:
            uncovered[module] = misses

    assert not uncovered, (
        "以下改动型端点在工具面**不可达**（AI 只能新建、不能改/删）：\n"
        + "\n".join(
            f"  [{m}] " + "; ".join(rows) for m, rows in sorted(uncovered.items())
        )
        + "\n修法：补 manifest provides（+ api.tools 细粒度键），"
          "或在 tests/test_mcp_coverage.py 的 _EXEMPT_PATTERNS 里显式豁免并写明理由。"
    )


def test_coverage_guard_can_fail():
    """★ 守卫自证：本守卫**真的会失败**。

    「测试全绿 ≠ 修复生效」—— 守卫自身坏掉时，正向断言照样绿。
    这里用一条已知不在工具面、也不在豁免表里的假端点，验证判定逻辑会抓它。
    若哪天有人把 _is_exempt 写成「一律豁免」，这条会立刻红。
    """
    fake_method, fake_path = "DELETE", "/api/v1/nowhere/does-not-exist/{id}"
    assert _is_exempt(fake_path) is None, "豁免表把陌生路径也放行了 —— 守卫已失效"

    # 模拟判定逻辑（与主守卫同一套分支）
    surface: set[tuple[str, str]] = {("DELETE", "/api/v1/todo/items/{item_id}")}
    assert (fake_method, fake_path) not in surface
    assert _is_exempt(fake_path) is None  # → 主守卫会把它判为 uncovered


def test_exemptions_all_have_reasons():
    """豁免表的每一条都必须写明理由 —— 豁免是纪律，不是后门。"""
    for pattern, why in _EXEMPT_PATTERNS:
        assert pattern.strip(), "空的正则"
        assert len(why.strip()) >= 6, f"豁免 {pattern!r} 的理由太短，等于没写"
        re.compile(pattern)  # 正则可编译
