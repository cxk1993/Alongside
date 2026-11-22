"""E7 工具接线测试（2026-09-26 hermes）。

覆盖三层：
  ① run_tool_call_loop 机制（假 LLM，无网络）：call → 回填 → 综答 / 拒绝
  ② 白名单与 defs 一致性（第二道闸对表 + V1 只读立场）
  ③ 声明即授权：ai-chat manifest requires ⊇ 工具能力

注：模块目录名带连字符（ai-chat），必须 importlib 动态加载。
"""
from __future__ import annotations

import importlib.util
import json
from datetime import timedelta
from pathlib import Path

_MOD_DIR = Path(__file__).resolve().parents[1] / "modules" / "ai-chat"


def _load(mod_name: str, file_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, _MOD_DIR / file_name)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # ★ 必须先进 sys.modules 再 exec：@dataclass 解析 __module__ 时要能查到本模块
    import sys

    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


chat_loop = _load("e7_chat_loop_under_test", "chat_loop.py")

ALLOWED_TOOLS = chat_loop.ALLOWED_TOOLS
ChatTurn = chat_loop.ChatTurn
run_tool_call_loop = chat_loop.run_tool_call_loop

# tools.py 依赖 core.deps / fastapi Request —— 仅取纯函数与 defs，导入整包需 app 上下文，
# 故对 defs/window 用同样的动态加载（tools 顶层只 import core.deps，测试环境可加载）。
tools = _load("e7_tools_under_test", "tools.py")
_today_window = tools._today_window
build_tool_defs = tools.build_tool_defs


# ─────────────────────────── ① 循环机制 ───────────────────────────


def test_loop_calls_tool_then_final():
    """假 LLM 第一次要工具、第二次综答 → audit=[call, final]，tool 结果进 turns。"""
    n = {"calls": 0}

    def llm(turns):
        n["calls"] += 1
        if n["calls"] == 1:
            return ChatTurn("assistant", tool_name="dashboard_today_read", tool_args={})
        tool_turn = next(t for t in turns if t.role == "tool")
        assert tool_turn.tool_result["ok"] is True
        return ChatTurn("assistant", text="今天有 2 项日程")

    tools_map = {"dashboard_today_read": lambda a: {"ok": True, "count": 2}}
    s = run_tool_call_loop("今天有什么安排", llm, tools_map)
    assert s.audit == ["call:dashboard_today_read", "final"]
    assert s.turns[-1].text == "今天有 2 项日程"


def test_loop_denies_tool_outside_allowlist():
    """write 类工具不在白名单 → denied，绝不执行。"""
    executed = {"hit": False}

    def w(a):
        executed["hit"] = True
        return {}

    def llm(turns):
        return ChatTurn("assistant", tool_name="todo_item_write", tool_args={"text": "hack"})

    s = run_tool_call_loop("帮我写个待办", llm, {"todo_item_write": w})
    assert s.audit == ["denied:todo_item_write"]
    assert executed["hit"] is False


def test_allowlist_and_defs_consistent():
    """第二道闸对表：build_tool_defs 的名字 ⊆ ALLOWED_TOOLS。"""
    names = {d["function"]["name"] for d in build_tool_defs()}
    assert names <= ALLOWED_TOOLS, f"defs 里有白名单外的工具: {names - ALLOWED_TOOLS}"


def test_no_direct_write_tools_in_allowlist():
    """V1 只读立场（★ 2026-11-22 比丢改精确版）。

    旧断言是 `not any("write" in t for t in ALLOWED_TOOLS)` —— 只挡得住名字带
    `write` 的，挡不住 `todo_item_delete` 这种。真正的立场是：
    **白名单里不许出现「直接写业务」的工具**，写操作必须经提案起草器。

    所以这条测试改成双向：
      ① 直写类工具名一个都不许有；
      ② 三个起草器必须在（它们只 POST /api/v1/proposals）。
    更硬的约束在 `test_ai_chat_has_no_todo_write_capability`（能力层）。
    """
    direct_write = {
        "todo_item_write",
        "todo_item_patch",
        "todo_write",
        "calendar_event_write",
        "calendar_event_delete",
        "health_record_write",
    }
    leaked = ALLOWED_TOOLS & direct_write
    assert not leaked, f"白名单里出现直写工具: {leaked}"

    drafters = {"todo_item_create", "todo_item_update", "todo_item_delete"}
    assert drafters <= ALLOWED_TOOLS, "提案起草器缺失——AI 就无法替人起草变更"


def test_ai_chat_has_no_todo_write_capability():
    """★ 最小权限硬约束（2026-11-22 比丢）。

    ai-chat 的能力清单里只允许有 `proposals.proposal.write`，
    **不许**有 todo 的任何写能力 —— 这样即使有人往 tools.py 里塞一句
    `client.post("/api/v1/todo/items", ...)`，`get_plugin_client` 的
    ⊆ requires 校验也会 403。安全靠能力表，不靠代码自觉。
    """
    caps = set(tools._CAPS)
    assert "proposals.proposal.write" in caps, "起草提案需要 proposals.proposal.write"

    forbidden = {"todo.item.write", "todo.item.patch", "todo.item.delete"}
    leaked = caps & forbidden
    assert not leaked, f"ai-chat 不应持有 todo 直写能力: {leaked}"


def test_manifest_requires_covers_new_caps():
    """声明即授权：manifest.requires 必须覆盖 _CAPS（否则运行期 403）。"""
    manifest = json.loads((_MOD_DIR / "manifest.json").read_text(encoding="utf-8"))
    missing = set(tools._CAPS) - set(manifest["requires"])
    assert not missing, f"manifest.requires 缺能力: {missing}"


def test_write_tools_are_proposal_drafters():
    """★ 措辞是功能的一部分（2026-11-22 比丢）。

    写工具的 description 会原样进模型的 prompt；模型会照抄它回答用户。
    若描述不说「不会立即执行」，模型就会说「已创建」——那就是认知污染
    （判据 5：声明与实现必须说同一句话；判据 16：不装深度）。
    """
    defs = {d["function"]["name"]: d["function"] for d in build_tool_defs()}
    for name in ("todo_item_create", "todo_item_update", "todo_item_delete"):
        assert name in defs, f"{name} 不在 tool defs 里"
        desc = defs[name]["description"]
        assert "不会立即" in desc, f"{name} 的 description 必须写明「不会立即执行」"
        assert "提案" in desc, f"{name} 的 description 必须说明这是「提案」"


def test_max_hops_guard():
    """LLM 永远要工具（死循环模拟）→ max_hops 切断，不无限调。"""

    def llm(turns):
        return ChatTurn("assistant", tool_name="dashboard_today_read", tool_args={})

    tools_map = {"dashboard_today_read": lambda a: {"ok": True}}
    s = run_tool_call_loop("循环吧", llm, tools_map, max_hops=2)
    assert s.audit == ["call:dashboard_today_read", "call:dashboard_today_read", "max_hops"]


# ─────────────────────────── ② 参数窗口 ───────────────────────────


def test_today_window_today_covered():
    """今日窗必须覆盖『现在』：CST 今晨 00:00 = UTC 昨 16:00，换算回来才算对。"""
    from datetime import datetime, timezone

    cst = timezone(timedelta(hours=8))
    w = _today_window()
    start_cst = datetime.fromisoformat(w["from"]).astimezone(cst)
    end_cst = datetime.fromisoformat(w["to"]).astimezone(cst)
    now_cst = datetime.now(cst)
    assert start_cst.hour == 0 and start_cst.minute == 0
    assert start_cst.date() == now_cst.date()          # 窗起点 = CST 今天 00:00
    assert (end_cst - start_cst) == timedelta(days=1)  # 全天窗
    assert start_cst <= now_cst < end_cst              # 现在落在窗内


# ─────────────────────────── ③ 声明即授权 ───────────────────────────


def test_manifest_requires_covers_tool_caps():
    """工具能力必须 ⊆ manifest.requires（get_plugin_client 机器校验的前提）。"""
    manifest = json.loads((_MOD_DIR / "manifest.json").read_text(encoding="utf-8"))
    requires = set(manifest["requires"])
    need = {"calendar.event.read", "todo.item.read", "health.record.read", "dashboard.today.read"}
    assert need <= requires, f"缺声明: {need - requires}"


def test_calendar_jump_uses_alias_not_python_name():
    """★ 别名钉（生产 500 教训）：路由声明 Query(..., alias="from")，
    内部调用必须传 from= 而不是 frm=——传错=422=对话窗整链 500。"""
    src = (_MOD_DIR / "tools.py").read_text(encoding="utf-8")
    i = src.find('"/api/v1/calendar/events"')
    assert i > 0, "calendar 调用点被挪走了？"
    # 取调用点后 400 字符窗口（params 是多行字典，不能用第一个 ) 截断）
    win = src[i : i + 400]
    assert '"from":' in win, "calendar 内部调用必须用别名 from"
    assert '"frm"' not in win, "frm 是 Python 形参名，不是查询键——会 422"
