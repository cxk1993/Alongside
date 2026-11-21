"""· 内置 AI 对话窗后端骨架（tool_call 循环）。

安全：token 不进日志全文；工具白名单；max_hops 防环。
LLM 网关可插拔（默认 stub，接 model-gateway 另配）。

★ 2026-11-21 修三处断链（审计 audit-ai-chat.md 的 M1/M2/M4）：
  ① **M1 tool_call 回填格式**：此前只 append `tool` turn，没有「assistant 发起调用」
     那一条，且 `tool_call_id` 用工具名而非模型返回的 `tc.id`。OpenAI 兼容规范要求
     `role:"tool"` 必须紧跟带 `tool_calls` 的 assistant 消息，否则严格网关直接 400。
     现在 assistant 侧与 tool 侧成对写入、共用同一个 `call_id`。
  ② **M2 多轮**：`history` 形参把会话已有的 turns 接回来（此前 `_SESSIONS` 只写不读，
     每轮对话 LLM 都看不到上一轮）。缺省 None = 老行为，向后兼容。
  ③ **M4 过程可见**：每轮开始 / 每次工具调用 / 每轮结束各发一条事件。
     topic 必须与 `manifest.emits` 逐字一致（内核发布端 fail-closed，未声明即抛）。

★ 为什么 publish 调用点写三份 try/except 而不是抽一个 `_emit(topic, payload)` 包装：
  静态门禁 `check_protocol_consumption.py` 靠 AST 读**字面量 topic** 做双向对账
  （声明↔发布）。包一层 `_emit` 后，包装体里的 `publish(topic, ...)` 成了动态 topic，
  扫描判不了，门禁会降级成警告——契约就又回到「写着的没人执行」。
  ⇒ 三处直写，是给门禁留的可见性，不是懒得重构。
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid4

from core.events import event_bus
from core.logging import get_logger

log = get_logger("ai-chat.chat_loop")

#: 模块 id（= manifest.id = 事件 source）。发布时按它查 emits 声明。
MODULE_ID = "ai-chat"

# ── 过程事件的 topic（必须与 manifest.emits 逐字一致）──
#: 一轮对话开始（人说了话，AI 还没动手）
TOPIC_TURN_STARTED = "ai-chat.turn.started"
#: 一次工具调用（含入参摘要与结果摘要，**不含**数据行全文）
TOPIC_TOOL_CALLED = "ai-chat.tool.called"
#: 一轮对话结束（正常综答 / 被拒 / 无实现 / 步数用尽）
TOPIC_TURN_COMPLETED = "ai-chat.turn.completed"

#: 事件里摘要字段的长度上限。过程可见 ≠ 把用户数据整包推给前端。
SUMMARY_LIMIT = 200

#: 结果摘要里**允许**出现的键（数量/状态类）。数据行（items/events/records）一律不发。
_RESULT_META_KEYS = ("ok", "count", "error", "window")

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
    #: ★ M1：模型返回的 `tool_calls[0].id`（形如 `call_abc123`）。
    #: assistant 侧（发起调用）与 tool 侧（回填结果）必须是同一个值，
    #: 否则严格网关报 `messages with role 'tool' must be a response to a
    #: preceding message with 'tool_calls'`。
    tool_call_id: str | None = None
    #: ★ 网关一次返回多个 tool_calls 时，V1 只执行第一个；剩下的数量记在这里，
    #: 由循环写进 audit（`dropped:<n>`）——「静默丢弃」是审计 P3-2 点出的缺陷。
    dropped_tool_calls: int = 0


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


def _summarize(value: Any, limit: int = SUMMARY_LIMIT) -> str:
    """把任意结构压成 ≤limit 字的单行摘要（事件里只放摘要，不放全文）。"""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 —— 序列化不了就退回 repr，摘要不许拖垮对话
        text = str(value)
    return text[:limit]


def _result_digest(result: Any) -> dict[str, Any]:
    """工具结果的**元信息**摘要：状态/数量/错误 + 字段名清单。

    ★ 刻意不发 `items` / `events` / `records` 的**值**——那是用户数据（健康记录、
      待办正文）。前端要「AI 拿到了什么」这件事，不需要拿到内容本身；
      内容已经在 LLM 的综答里了。字段名清单足够让前端画「读了 3 条待办」。
    """
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    out: dict[str, Any] = {}
    for key in _RESULT_META_KEYS:
        if key in result:
            out[key] = result[key] if key != "error" else _summarize(result[key])
    out["fields"] = sorted(str(k) for k in result)
    return out


def run_tool_call_loop(
    user_text: str,
    llm: Callable[[list[ChatTurn]], ChatTurn],
    tools: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    *,
    history: list[ChatTurn] | None = None,
    max_hops: int = 4,
    session_id: str = "default",
    correlation_id: str | None = None,
) -> ChatSession:
    """跑一轮工具循环。

    `history`：本会话此前的 turns（★ M2）。传进来后 LLM 就能看到上一轮，
    这是多轮对话的前提；缺省 None 保持老行为。
    `session_id` / `correlation_id`：只用于事件（过程可见性），不参与决策。
    同一次对话的三个事件共用一个 correlation_id，前端可据此串成一条时间线。
    """
    corr = correlation_id or uuid4().hex[:16]
    s = ChatSession()
    if history:
        s.turns.extend(history)
    s.turns.append(ChatTurn("user", text=user_text))

    try:
        event_bus.publish(
            TOPIC_TURN_STARTED,
            {
                "session_id": session_id,
                "text_len": len(user_text),
                "history_turns": len(history) if history else 0,
            },
            source="ai-chat",
            correlation_id=corr,
        )
    except Exception as exc:  # noqa: BLE001 —— 观测失败绝不拖垮对话（与工具跳同口径）
        log.warning("事件发布失败（已忽略）", extra={"topic": TOPIC_TURN_STARTED, "error": str(exc)})

    hops_used = 0
    finished = False
    for hops_used in range(1, max_hops + 1):
        reply = llm(s.turns)
        name = reply.tool_name
        if name is None:
            s.turns.append(ChatTurn("assistant", text=reply.text))
            s.audit.append("final")
            finished = True
            break
        if name not in ALLOWED_TOOLS:
            s.turns.append(ChatTurn("assistant", text=f"工具 {name} 不在白名单"))
            s.audit.append(f"denied:{name}")
            finished = True
            break
        fn = tools.get(name)
        if not fn:
            s.turns.append(ChatTurn("assistant", text="工具未实现"))
            s.audit.append(f"missing:{name}")
            finished = True
            break
        # ★ M1：模型没给 id 时补一个确定性 id（同一 hop 内 assistant/tool 两处共用），
        #   保证「发起」与「回填」永远成对——即使网关是个不给 id 的宽松实现。
        call_id = reply.tool_call_id or f"call_{hops_used}_{name}"
        args = reply.tool_args or {}
        if getattr(reply, "dropped_tool_calls", 0):
            s.audit.append(f"dropped:{reply.dropped_tool_calls}")
        s.turns.append(
            ChatTurn("assistant", tool_name=name, tool_args=args, tool_call_id=call_id)
        )
        started = time.perf_counter()
        result = fn(args)
        duration_ms = int((time.perf_counter() - started) * 1000)
        s.audit.append(f"call:{name}")
        s.turns.append(
            ChatTurn(
                "tool",
                tool_name=name,
                tool_args=args,
                tool_result=result,
                tool_call_id=call_id,
            )
        )
        # 一次调用一条事件：入参摘要 + 结果摘要（都在 SUMMARY_LIMIT 内，不含数据行）
        try:
            event_bus.publish(
                TOPIC_TOOL_CALLED,
                {
                    "session_id": session_id,
                    "hop": hops_used,
                    "tool": name,
                    "args_summary": _summarize(args),
                    "result_summary": _result_digest(result),
                    "ok": bool(result.get("ok")) if isinstance(result, dict) else None,
                    "duration_ms": duration_ms,
                },
                source="ai-chat",
                correlation_id=corr,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "事件发布失败（已忽略）", extra={"topic": TOPIC_TOOL_CALLED, "error": str(exc)}
            )

    if not finished:
        s.turns.append(ChatTurn("assistant", text="步骤过多，请拆成更小的问题。"))
        s.audit.append("max_hops")

    last_text = next((t.text for t in reversed(s.turns) if t.role == "assistant"), "")
    try:
        event_bus.publish(
            TOPIC_TURN_COMPLETED,
            {
                "session_id": session_id,
                "hops": hops_used,
                "audit": list(s.audit),
                "reply_len": len(last_text),
            },
            source="ai-chat",
            correlation_id=corr,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "事件发布失败（已忽略）", extra={"topic": TOPIC_TURN_COMPLETED, "error": str(exc)}
        )
    return s
