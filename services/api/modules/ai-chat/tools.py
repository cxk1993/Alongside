"""· E7 工具桥：MCP 白名单工具 → 内部 API（声明即授权）。

★ 2026-09-26 hermes 接线（生产实测根因驱动）：
  tools 此前恒为 []（gateway.complete(..., []) 与 loop(..., {})），
  LLM 拿不到工具 schema，只能凭 system 字面「只使用已注册工具」**幻觉**
  ——本机探针实锤：真实 2 条事件 → AI 答「无日程事件」（audit=['final']）。

桥 = 既有 InternalHttpClient（ISSUE-005 A 案）：
  capability 白名单与 manifest.requires 一一对应，get_plugin_client
  机器校验 ⊆ requires（越权 403）——零新鉴权流程，作用域受限。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from fastapi import Request

from core.deps import get_plugin_client

# 工具 → 能力（与 manifest.requires 严格一致）
# ★ 2026-11-22 比丢：加**一个**写能力，而且是 proposals 的、不是 todo 的。
#   理由（最小权限）：ai-chat 的写工具设计目标是「生成提案等人签字」，不是直接改数据。
#   只要 requires 里没有 todo.item.write/patch/delete，它**在能力层面就无法直写业务**，
#   代码里想绕也绕不过 get_plugin_client 的 ⊆ requires 校验 —— 比"约定不这么写"硬。
_CAPS = [
    "calendar.event.read",
    "todo.item.read",
    "health.record.read",
    "dashboard.today.read",
    "proposals.proposal.write",
]


def build_tool_defs() -> list[dict[str, Any]]:
    """OpenAI function-calling schema。参数名与 MCP 目录逐字一致（from/to 带连字符键）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "calendar_event_read",
                "description": (
                    "读取日程事件。不传 from/to 时自动限定为【今天】（CST 00:00–24:00）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "from": {
                            "type": "string",
                            "description": "ISO8601 起点（可选，默认今天 00:00）",
                        },
                        "to": {
                            "type": "string",
                            "description": "ISO8601 终点（可选，默认明天 00:00）",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "todo_item_read",
                "description": "读取待办列表（默认未完成项，≤20 条）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["done", "todo", "all"],
                            "description": "默认 todo",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "health_record_read",
                "description": "读取健康记录（症状/用药/预约/化验，≤20 条）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "description": "symptom|medication|appointment|lab（可选）",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dashboard_today_read",
                "description": "读取今日合并视图（日程数/待办/习惯完成度）。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        # ───────── ★ 2026-11-22 比丢：写工具（生成提案，不直接执行） ─────────
        # 设计前提（判据 5「声明与实现同一句话」）：
        #   ai-chat 的 requires 里**没有** todo.item.write/patch/delete，只有
        #   proposals.proposal.write ⇒ 模型即使想直写也拿不到客户端能力。
        #   所以这三个工具的语义只能是「替人起草一张待签字的提案」。
        #   描述里必须把这一点说透，否则模型会把「已提交」说成「已完成」（认知污染）。
        {
            "type": "function",
            "function": {
                "name": "todo_item_create",
                "description": (
                    "为用户起草一张「新建待办」的提案。"
                    "★ 不会立即创建——提案进入待签字区，用户点签字后才真正写入。"
                    "告诉用户「已起草，请在待签区确认」而不是「已创建」。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "待办正文（必填，一句话说清要做什么）",
                        },
                        "due_at": {
                            "type": "string",
                            "description": "截止时间 ISO8601（可选，如 2026-11-25T23:59:00+08:00）",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": "优先级（可选）",
                        },
                        "recur_rule": {
                            "type": "string",
                            "description": "周期规则（可选，如 daily / weekly）",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "标签（可选）",
                        },
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "todo_item_update",
                "description": (
                    "为用户起草一张「修改待办」的提案（改正文/完成状态/截止/优先级/标签）。"
                    "★ 不会立即修改——提案待签字。系统会自动读取当前值作为可撤销快照。"
                    "告诉用户「已起草，请确认」而不是「已修改」。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string", "description": "要修改的待办 id（必填）"},
                        "text": {"type": "string", "description": "新正文（可选）"},
                        "done": {"type": "boolean", "description": "完成状态（可选）"},
                        "due_at": {"type": "string", "description": "新截止时间 ISO8601（可选）"},
                        "priority": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": "新优先级（可选）",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "整组替换的标签（可选）",
                        },
                    },
                    "required": ["item_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "todo_item_delete",
                "description": (
                    "为用户起草一张「删除待办」的提案。"
                    "★ 不会立即删除——提案待签字，且系统会自动取当前值做快照（撤销时据此重建）。"
                    "告诉用户「已起草，请确认」而不是「已删除」。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string", "description": "要删除的待办 id（必填）"},
                    },
                    "required": ["item_id"],
                },
            },
        },
    ]


def _today_window() -> dict[str, str]:
    cst = timezone(timedelta(hours=8))
    now = datetime.now(cst)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return {
        "from": start.astimezone(UTC).isoformat(),
        "to": end.astimezone(UTC).isoformat(),
    }


def _cst_today_iso() -> str:
    """CST（主人时区）口径的今天 —— 与 _today_window 同一基准，防 UTC 跨日错杀。"""
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def _window_days(frm: str, to: str) -> int:
    """ISO 区间跨度（天），解析失败按超大窗（→ 强正）。"""
    try:
        a = datetime.fromisoformat(frm.replace("Z", "+00:00"))
        b = datetime.fromisoformat(to.replace("Z", "+00:00"))
        return (b - a).days
    except Exception:  # noqa: BLE001 — 解析不了就是坏窗
        return 9999


def _redact(msg: str) -> str:
    """错误摘要进对话上下文前的脱敏：token 一律打星（绝不让凭据流进 LLM/日志）。"""
    import re

    msg = re.sub(r"(Bearer\s+)[A-Za-z0-9_.\-]+", r"\1***", msg)
    msg = re.sub(r"(Authorization['\"]?[:=]\s*)[^,}\s]+", r"\1***", msg)
    msg = re.sub(r"(key=)[^&\s]+", r"\1***", msg, flags=re.I)
    return msg


def make_tools(request: Request) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    client = get_plugin_client(request, _CAPS)

    def _jump(fn: Callable[[dict[str, Any]], dict[str, Any]], name: str):
        """fail-soft：一跳网络/解析挂了 → 把错误作为 tool 数据回给 LLM 综答，
        绝不让整个对话端点 500（工具失败 ≠ 聊天失败；错误摘要不带 token）。"""

        def wrapped(args: dict[str, Any]) -> dict[str, Any]:
            try:
                return fn(args)
            except Exception as e:  # noqa: BLE001 — 工具边界就是要吞一切并上报
                return {"ok": False, "tool": name, "error": _redact(str(e))[:200]}

        return wrapped

    def calendar_event_read(args: dict[str, Any]) -> dict[str, Any]:
        frm = str(args.get("from") or "")
        to = str(args.get("to") or "")
        want = _today_window()
        today_iso = _cst_today_iso()
        # 只在窗明显离谱时强正为今日（缺参 / 起点在过去 / 窗宽 >31 天）；
        # 「查明天/下周五」等合理未来窗放行，不错杀。
        bad = not frm or not to or frm[:10] < today_iso or _window_days(frm, to) > 31
        if bad:
            frm, to = want["from"], want["to"]
        data = client.get(
            "/api/v1/calendar/events",
            # ★ 路由声明 Query(..., alias="from") —— 必须用别名 from，
            #   传 frm 会被当缺参 → 422（生产 500 根因之一，实测钉出）。
            params={"from": frm, "to": to, "include_children": False, "flat": True},
        )
        items = data if isinstance(data, list) else []
        slim = [
            {
                "title": e.get("title"),
                "start_at": e.get("start_at") or e.get("startAt"),
                "end_at": e.get("end_at") or e.get("endAt"),
            }
            for e in items[:20]
        ]
        return {"ok": True, "count": len(slim), "events": slim, "window": {"from": frm, "to": to}}

    def todo_item_read(args: dict[str, Any]) -> dict[str, Any]:
        status = args.get("status") or "todo"
        data = client.get("/api/v1/todo/items", params={"status": status, "limit": 20})
        items = (data or {}).get("items", [])
        slim = [
            {
                "text": i.get("text"),
                "due_at": i.get("due_at") or i.get("dueAt"),
                "done": i.get("done"),
            }
            for i in items[:20]
        ]
        return {"ok": True, "count": len(slim), "items": slim}

    def health_record_read(args: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if args.get("kind"):
            params["kind"] = args["kind"]
        data = client.get("/api/v1/health/records", params=params)
        items = data if isinstance(data, list) else (data or {}).get("items", [])
        slim = [
            {
                "kind": r.get("kind"),
                "title": r.get("title"),
                "occurred_at": r.get("occurred_at") or r.get("occurredAt"),
            }
            for r in items[:20]
        ]
        return {"ok": True, "count": len(slim), "records": slim}

    def dashboard_today_read(args: dict[str, Any]) -> dict[str, Any]:
        data = client.get("/api/v1/dashboard/today")
        return {"ok": True, "today": data}

    # ───────── ★ 2026-11-22 比丢：写工具 = 起草提案（不直接执行） ─────────
    # 为什么必须这样：ai-chat 的能力清单里没有 todo.item.write/patch/delete，
    # 只有 proposals.proposal.write ⇒ 物理上只能建提案。这正是"AI 提议、人执行"。
    def _draft(
        title: str,
        tool_name: str,
        action: str,
        method: str,
        path: str,
        payload: dict[str, Any],
        before: dict[str, Any] | None,
    ) -> dict[str, Any]:
        body = {
            "title": title[:300],
            "tool_name": tool_name,
            "action": action,
            "method": method,
            "path": path,
            "payload": payload,
            "before": before,
            "actor": "ai-chat",
        }
        data = client.post("/api/v1/proposals", json=body) or {}
        pid = data.get("id")
        return {
            "ok": True,
            "proposal_id": pid,
            "status": data.get("status"),
            # ★ 措辞是功能的一部分：模型会照抄这句话回答用户。
            #   说成"已创建"就是认知污染（判据 5/16）。
            "message": (
                f"已起草提案 {pid}，**尚未执行**；"
                "请在「它想说的」待签区点「签字」后才会真正生效。"
            ),
        }

    def _read_item(item_id: str) -> dict[str, Any]:
        """取当前行做 before 快照。delete/update 没它会被 proposals 的
        `_before_required_for_destructive` 直接 422（夜班 79fc86f 钉的规矩）。"""
        data = client.get(f"/api/v1/todo/items/{item_id}")
        if not isinstance(data, dict) or not data:
            raise ValueError(f"待办 {item_id} 读不到（可能已删除或 id 不对）")
        return data

    def todo_item_create(args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text") or "").strip()
        if not text:
            raise ValueError("text 不能为空")
        payload: dict[str, Any] = {"text": text}
        for k in ("due_at", "priority", "recur_rule"):
            if args.get(k):
                payload[k] = args[k]
        if args.get("tags"):
            payload["tags"] = args["tags"]
        return _draft(
            f"AI 想新建待办：{text[:40]}",
            "todo_item_create",
            "create",
            "POST",
            "/api/v1/todo/items",
            payload,
            None,
        )

    def todo_item_update(args: dict[str, Any]) -> dict[str, Any]:
        item_id = str(args.get("item_id") or "").strip()
        if not item_id:
            raise ValueError("item_id 不能为空")
        payload: dict[str, Any] = {}
        for k in ("text", "done", "due_at", "priority"):
            if args.get(k) is not None:
                payload[k] = args[k]
        if args.get("tags"):
            payload["tags"] = args["tags"]
        if not payload:
            raise ValueError("没有要改的字段（text/done/due_at/priority/tags 至少传一个）")
        before = _read_item(item_id)
        label = payload.get("text") or before.get("text") or item_id[:8]
        return _draft(
            f"AI 想修改待办：{str(label)[:40]}",
            "todo_item_update",
            "update",
            "PATCH",
            f"/api/v1/todo/items/{item_id}",
            payload,
            before,
        )

    def todo_item_delete(args: dict[str, Any]) -> dict[str, Any]:
        item_id = str(args.get("item_id") or "").strip()
        if not item_id:
            raise ValueError("item_id 不能为空")
        before = _read_item(item_id)  # 先取快照，撤销时据此重建
        label = before.get("text") or item_id[:8]
        return _draft(
            f"AI 想删除待办：{str(label)[:40]}",
            "todo_item_delete",
            "delete",
            "DELETE",
            f"/api/v1/todo/items/{item_id}",
            {},
            before,
        )

    return {
        "calendar_event_read": _jump(calendar_event_read, "calendar_event_read"),
        "todo_item_read": _jump(todo_item_read, "todo_item_read"),
        "health_record_read": _jump(health_record_read, "health_record_read"),
        "dashboard_today_read": _jump(dashboard_today_read, "dashboard_today_read"),
        "todo_item_create": _jump(todo_item_create, "todo_item_create"),
        "todo_item_update": _jump(todo_item_update, "todo_item_update"),
        "todo_item_delete": _jump(todo_item_delete, "todo_item_delete"),
    }
