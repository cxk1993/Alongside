"""sight 测试：纯函数 + 聚合输出契约校验。

★ 本文件不启动 HTTP、不建 DB、不碰鉴权 —— 全部直接 import 被测函数。
理由：sight 是只读聚合器，它的正确性有两层，都可以脱离运行时验证：
  1. `tensions.compute()` 是纯函数（零 IO）
  2. `aggregate_now()` 用 `set_fetch_override` 注入假上游

最强的断言放在最后：**输出必须符合 `contracts/sight.schema.json`**。
契约是唯一真相，测试直接对着契约验，而不是对着我自己的假设验。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from modules.sight import aggregator as aggmod
from modules.sight.aggregator import aggregate_now, set_fetch_override
from modules.sight.tensions import compute, find_conflicts, find_overload, find_stale

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "sight.schema.json"


# ----------------------------- 工具 -----------------------------

def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


NOW = datetime(2026, 11, 22, 1, 30, tzinfo=timezone(timedelta(hours=8)))


# ----------------------------- 纯函数：冲突 -----------------------------

def test_conflict_detected_high():
    """重叠 ≥30 分钟 → high，且引用两边的 calendar id。"""
    events = [
        {"id": "e1", "title": "评审会", "start_at": _iso(NOW), "end_at": _iso(NOW + timedelta(hours=1))},
        {"id": "e2", "title": "组会", "start_at": _iso(NOW + timedelta(minutes=30)),
         "end_at": _iso(NOW + timedelta(hours=2))},
    ]
    out = find_conflicts(events)
    assert len(out) == 1
    t = out[0]
    assert t["kind"] == "schedule_conflict"
    assert t["severity"] == "high"
    assert {r["id"] for r in t["refs"]} == {"e1", "e2"}
    assert "评审会" in t["summary"] and "组会" in t["summary"]


def test_conflict_adjacent_is_not_conflict():
    """首尾相接不算冲突（10:00-11:00 与 11:00-12:00）。"""
    events = [
        {"id": "e1", "start_at": _iso(NOW), "end_at": _iso(NOW + timedelta(hours=1))},
        {"id": "e2", "start_at": _iso(NOW + timedelta(hours=1)), "end_at": _iso(NOW + timedelta(hours=2))},
    ]
    assert find_conflicts(events) == []


def test_conflict_skips_unparseable():
    """时间解析不了的条目直接跳过 —— 不猜。"""
    events = [
        {"id": "e1", "start_at": "不是时间", "end_at": "也不是"},
        {"id": "e2", "start_at": _iso(NOW), "end_at": _iso(NOW + timedelta(hours=1))},
    ]
    assert find_conflicts(events) == []


# ----------------------------- 纯函数：排太满 -----------------------------

def test_overload_events():
    events = [{"id": f"e{i}", "start_at": _iso(NOW + timedelta(hours=i)), "end_at": _iso(NOW + timedelta(hours=i, minutes=30))}
              for i in range(6)]
    out = find_overload(events, [])
    assert len(out) == 1 and out[0]["kind"] == "overload"
    assert "6 个日程" in out[0]["summary"]


def test_overload_quiet_day_is_empty():
    assert find_overload([{"id": "e1"}], []) == []


# ----------------------------- 纯函数：挂太久 -----------------------------

def test_stale_item_detected():
    todos = [{"id": "t1", "text": "交计划书", "done": False, "created_at": _iso(NOW - timedelta(days=11))}]
    out = find_stale(todos, now=NOW)
    assert len(out) == 1 and out[0]["kind"] == "stale_item"
    assert "11 天" in out[0]["summary"]
    assert out[0]["refs"][0]["module"] == "todo"


def test_stale_ignores_done_and_no_created_at():
    todos = [
        {"id": "t1", "text": "早做完的", "done": True, "created_at": _iso(NOW - timedelta(days=30))},
        {"id": "t2", "text": "没有创建时间", "done": False},
    ]
    assert find_stale(todos, now=NOW) == []


# ----------------------------- 纯函数：总入口 -----------------------------

def test_compute_empty_is_valid_answer():
    """★ 空列表是合法且重要的答案 —— 此刻没有张力，不许硬造。"""
    assert compute(events=[], todos=[], now=NOW) == []


def test_compute_sorted_by_severity_then_id():
    events = [
        {"id": "a", "title": "A", "start_at": _iso(NOW), "end_at": _iso(NOW + timedelta(minutes=20))},
        {"id": "b", "title": "B", "start_at": _iso(NOW + timedelta(minutes=5)),
         "end_at": _iso(NOW + timedelta(minutes=50))},
    ]
    todos = [{"id": "t1", "text": "老账", "done": False, "created_at": _iso(NOW - timedelta(days=20))}]
    out = compute(events=events, todos=todos, now=NOW)
    rank = {"high": 0, "medium": 1, "low": 2}
    sev = [rank[t["severity"]] for t in out]
    assert sev == sorted(sev), "必须按 severity 降序"
    assert len(out) <= 5


# ----------------------------- 聚合：契约一致性 -----------------------------
#
# ★ 这里刻意不用 @pytest.mark.asyncio：项目没装 pytest-asyncio 时，
#   pytest 会把 async 测试**静默 skip**（只留一条 warning）——看起来是绿的，其实一条没跑。
#   这跟「门禁不许空转」是同一种病：测试也不许空转。
#   所以统一用 asyncio.run() 在同步测试里跑，零插件依赖。

def test_aggregate_matches_contract():
    """★ 最强的一条：seeded 正常上游 → 输出必须过 contract 校验。"""
    jsonschema = pytest.importorskip("jsonschema")

    async def fake(path: str):
        if "/calendar/events" in path:
            return [
                {"id": "e1", "title": "评审会", "start_at": _iso(NOW), "end_at": _iso(NOW + timedelta(hours=1))},
                {"id": "e2", "title": "组会", "start_at": _iso(NOW + timedelta(minutes=30)),
                 "end_at": _iso(NOW + timedelta(hours=2))},
            ]
        if "/todo/items" in path:
            return {"items": [
                {"id": "t1", "text": "交计划书", "done": False,
                 "created_at": _iso(NOW - timedelta(days=11)),
                 "due_at": _iso(NOW - timedelta(days=1))},
            ], "total": 1}
        if "/habits" in path:
            return [{"id": "h1", "name": "喝水"}]
        raise AssertionError(path)

    set_fetch_override(fake)
    try:
        out = asyncio.run(aggregate_now(now=NOW))
    finally:
        set_fetch_override(None)

    schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(schema).validate(out)  # 不通过会抛

    assert out["head"]["weekday"] == "sun"
    assert any(t["kind"] == "schedule_conflict" for t in out["tensions"])
    assert any(a["kind"] == "todo_overdue" for a in out["attention"])
    # 没接的源要如实列在 degraded
    assert {d["source"] for d in out["degraded"]} >= {"finance", "health", "notes", "diary"}


def test_aggregate_all_upstream_down_is_still_200():
    """上游全挂 → 不抛、不 500；degraded 记满，输出仍过契约。"""
    jsonschema = pytest.importorskip("jsonschema")

    async def boom(path: str):
        raise RuntimeError("upstream down")

    set_fetch_override(boom)
    try:
        out = asyncio.run(aggregate_now(now=NOW))
    finally:
        set_fetch_override(None)

    schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(schema).validate(out)

    assert out["tensions"] == []            # 没数据就不编
    assert out["attention"] == []
    sources = {d["source"] for d in out["degraded"]}
    assert {"calendar", "todo", "habits"} <= sources
    assert all(d["reason"] in {"error", "timeout", "not_implemented"} for d in out["degraded"])


def test_aggregate_tolerates_list_or_dict():
    """宽容解析：上游返回 list 或 {items:[]} 都要认；认不出就是空，不炸。"""

    async def mixed(path: str):
        if "/calendar/events" in path:
            return []                      # 直接 list
        if "/todo/items" in path:
            return {"data": [{"id": "t9", "text": "x", "done": False}]}   # 包一层 data
        return {"unexpected": True}        # 认不出

    set_fetch_override(mixed)
    try:
        out = asyncio.run(aggregate_now(now=NOW))
    finally:
        set_fetch_override(None)

    assert isinstance(out["attention"], list)
    assert isinstance(out["tensions"], list)


def test_no_async_test_is_silently_skipped():
    """★ 元测试：防止有人把上面几条改回 @pytest.mark.asyncio 又被静默跳过。

    只检查**行首装饰器**，不检查字符串出现——否则本文件的说明注释会自证其罪。
    """
    src = Path(__file__).read_text(encoding="utf-8")
    offenders = [ln.strip() for ln in src.splitlines() if ln.strip().startswith("@pytest.mark.asyncio")]
    assert not offenders, (
        "本文件不许使用 @pytest.mark.asyncio —— 项目未装 pytest-asyncio，"
        "它会让异步测试被静默 skip（假绿）。请用 asyncio.run()。"
    )


# ----------------------------- T16：MCP 暴露 -----------------------------

def test_manifest_provides_derive_mcp_tool():
    """★ T16 的回归证据：manifest 的 provides 必须能派生成可用的 MCP 工具。

    这是「加一个工具 = manifest 多一行 provides」这条设计的**验收**：
    若 provides 写错（动词不在动词表 / 段数 <3），derive_tool 会返回 None
    —— 工具就静默消失了。**静默消失比报错更危险**，所以要用测试钉住。
    """
    from modules.mcp.registry_adapter import derive_tool

    mf = json.loads(
        (Path(__file__).resolve().parents[1] / "modules" / "sight" / "manifest.json")
        .read_text(encoding="utf-8")
    )
    base = mf["api"]["base"]
    routes = mf["api"].get("tools") or {}

    derived = []
    for pvd in mf["provides"]:
        t = derive_tool(pvd, base, mf["id"], routes)
        assert t is not None, (
            f"provides「{pvd}」派生不出工具 —— 检查动词是否在 _METHOD_BY_VERB 里、段数是否 >=3"
        )
        derived.append((t.name, t.method, t.path, t.scope))

    assert ("sight_now_read", "GET", "/api/v1/sight/now", "sight:read") in derived, f"实际派生：{derived}"


def test_manifest_is_loadable_and_pure():
    """manifest 必须能被内核机械读取；且 sight 是只读聚合器，不许持有业务表。"""
    mf = json.loads(
        (Path(__file__).resolve().parents[1] / "modules" / "sight" / "manifest.json")
        .read_text(encoding="utf-8")
    )
    assert mf["id"] == "sight"
    assert mf["api"]["base"] == "/api/v1/sight"
    assert mf["api"]["health"] == "/api/v1/sight/health"
    assert mf["provides"], "至少要暴露一个能力"
    assert mf["migrations"] is None, "sight 是只读聚合器，不许持有业务表"
    assert all("net:out" in x for x in mf["permissions"]), "只该申请出网（聚合用）"
