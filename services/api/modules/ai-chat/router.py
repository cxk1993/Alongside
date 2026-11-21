"""ai_chat 路由：health + messages（tool_call 循环）。"""
import json
import threading
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from core.deps import get_current_user

from .chat_loop import ChatSession, ChatTurn, build_system, run_tool_call_loop
from .gateway import get_gateway
from .tools import build_tool_defs, make_tools

router = APIRouter()

_MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"
with _MANIFEST_PATH.open(encoding="utf-8") as _f:
    _MANIFEST = json.load(_f)

# 内存会话（V1 零新表）
_SESSIONS: dict[str, ChatSession] = {}
#: ★ M2：并发同 session 会撕裂 turns（原实现裸 dict）。
_SESSIONS_LOCK = threading.Lock()
#: ★ M2：每会话只回灌最近 N turn —— 无上限的 history 会一路把 token 撑爆。
_MAX_HISTORY_TURNS = 40


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000, description="要发给 AI 的这句话（≤4000 字）")
    session_id: str = Field(default="default", max_length=64, description="会话名（不同会话互不串上下文）")


@router.get("/health")
def health() -> dict[str, bool]:
    """插件健康探针（恒 200）。"""
    return {"ok": True}


@router.get("/manifest")
def manifest() -> dict:
    """模块清单（前端 / AI 发现能力用）。"""
    return _MANIFEST


@router.post("/messages")
def post_message(
    body: MessageIn,
    request: Request,
    _user: Annotated[Any, Depends(get_current_user)] = None,
) -> dict[str, Any]:
    """一轮对话：LLM 带工具 schema → 需要时发起 tool_call → 真数据回填 → 综答。

    ★ 2026-09-26 工具接线（hermes）：此前 tools 恒为 []/{}，LLM 无工具可用只能幻觉；
      现经 ISSUE-005 A 案受限 client 桥接四个白名单读工具（声明即授权）。
    ★ 2026-11-21 M2：把会话已有 turns 作为 history 回灌 —— 此前 `_SESSIONS` 只写不读，
      第二轮对话 LLM 完全看不到第一轮。
    """
    # get_plugin_client 校验 ai-chat.requires ⊇ 工具能力（越权即 403）
    tools = make_tools(request)
    defs = build_tool_defs()
    gw = get_gateway()

    def llm(turns: list[ChatTurn]) -> ChatTurn:
        return gw.complete(build_system(), turns, defs)

    with _SESSIONS_LOCK:
        s = _SESSIONS.setdefault(body.session_id, ChatSession())
        history = list(s.turns[-_MAX_HISTORY_TURNS:])

    out = run_tool_call_loop(
        body.text,
        llm,
        tools,
        history=history,
        session_id=body.session_id,
        correlation_id=uuid4().hex[:16],
    )

    with _SESSIONS_LOCK:
        # 并发同 session 时后到者覆盖前者的历史 —— 宁可丢一轮上下文，
        # 也不让两条 turn 序列交错（交错会让 assistant/tool 配对被拆散）。
        s.turns.extend(out.turns[len(history) :])
        s.audit.extend(out.audit)

    last = next((t.text for t in reversed(out.turns) if t.role == "assistant"), "")
    return {
        "session_id": body.session_id,
        "reply": last,
        "audit": list(out.audit),
        "system": build_system(),
    }
