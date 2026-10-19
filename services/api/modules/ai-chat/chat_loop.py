"""· 内置 AI 对话窗后端骨架（tool_call 循环）。

安全：token 不进日志全文；工具白名单；max_hops 防环。
LLM 网关可插拔（默认 stub，接 model-gateway 另配）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# ★ 2026-09-26 与 tools.py 实现严格对齐（hermes 接线）：
#   只放行**有实现且只读**的四个工具。写工具（todo_item_write/calendar_event_write）
#   暂不进对话窗——「查东西」是 V1 场景，误写代价不对称；notes_search 依赖本机桥
#（V1 不配）。删掉无实现的条目 = 第二道闸：LLM 即使幻觉出写调用也被 denied 挡下。
ALLOWED_TOOLS: set[str] = {
    "calendar_event_read",
    "todo_item_read",
    "health_record_read",
    "dashboard_today_read",
}


@dataclass
class ChatTurn:
    role: str
    text: str = ""
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None


@dataclass
class ChatSession:
    turns: list[ChatTurn] = field(default_factory=list)
    audit: list[str] = field(default_factory=list)


def build_system(today: date | None = None) -> str:
    # ★ CST 口径的「今天」（主人时区）：date.today() 在 UTC 机上凌晨会错一天，
    #   且 LLM 报日期必须与工具强窗（_today_window 同为 CST）同一基准。
    from datetime import datetime, timedelta, timezone

    d = (today or datetime.now(timezone(timedelta(hours=8))).date()).isoformat()
    return (
        f"你是 Alongside 助手。今天是 {d}（CST）。"
        "回答涉及日程/待办/健康/今日概况时，必须先调用对应工具取真实数据，"
        "严禁凭记忆或想象编造；工具返回为空就如实说没有。"
    )


def run_tool_call_loop(
    user_text: str,
    llm: Callable[[list[ChatTurn]], ChatTurn],
    tools: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    *,
    max_hops: int = 4,
) -> ChatSession:
    s = ChatSession()
    s.turns.append(ChatTurn("user", text=user_text))
    for _ in range(max_hops):
        reply = llm(s.turns)
        if reply.tool_name is None:
            s.turns.append(ChatTurn("assistant", text=reply.text))
            s.audit.append("final")
            return s
        name = reply.tool_name
        if name not in ALLOWED_TOOLS:
            s.turns.append(ChatTurn("assistant", text=f"工具 {name} 不在白名单"))
            s.audit.append(f"denied:{name}")
            return s
        fn = tools.get(name)
        if not fn:
            s.turns.append(ChatTurn("assistant", text="工具未实现"))
            s.audit.append(f"missing:{name}")
            return s
        args = reply.tool_args or {}
        result = fn(args)
        s.audit.append(f"call:{name}")
        s.turns.append(ChatTurn("tool", tool_name=name, tool_args=args, tool_result=result))
    s.turns.append(ChatTurn("assistant", text="步骤过多，请拆成更小的问题。"))
    s.audit.append("max_hops")
    return s
