"""proposals 请求/响应 schema（bidiu 2026-11-21 · B1）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ProposalCreate(BaseModel):
    """POST /proposals —— 签发一条变更提案。

    ★ before 由**签发方**提供：MCP 拦截点在签发前调对应 read 工具取过快照；
    人类手动签发则按 action 决定（delete/update 强烈建议传，create 留空）。
    提案层不 import 业务模块，所以快照必须从外面带进来。
    """

    title: str = Field(min_length=1, max_length=300, description="要做什么（人话，签字时给人看的）")
    tool_name: str = Field(min_length=1, max_length=128, description="执行工具（MCP 工具名，如 todo_item_delete）")
    action: str = Field(description="create / update / delete")
    method: str = Field(default="", description="执行 HTTP 方法（空=服务端按工具机械推导）")
    path: str = Field(default="", description="执行路径（空=服务端按工具机械推导）")
    payload: dict[str, Any] = Field(default_factory=dict, description="执行参数")
    before: dict[str, Any] | None = Field(default=None, description="变更前快照（create 应为 null）")
    actor: str = Field(default="human", max_length=80, description="发起者（mcp:<pat name> / human）")

    @field_validator("action")
    @classmethod
    def _action_kind(cls, v: str) -> str:
        from .models import ACTION_KINDS

        if v not in ACTION_KINDS:
            raise ValueError(f"action 须为 {ACTION_KINDS} 之一，收到 {v!r}")
        return v


class ProposalOut(BaseModel):
    """列表/查询响应。"""

    id: str
    title: str
    tool_name: str
    action: str
    method: str
    path: str
    payload: dict[str, Any] | None
    before: dict[str, Any] | None
    actor: str
    status: str
    created_at: datetime
    executed_at: datetime | None
    executed_id: str | None
    executed_status: int | None
    undone_at: datetime | None
    undo_result: str | None


class ProposalOpOut(BaseModel):
    """approve / reject / undo 的响应。"""

    id: str
    status: str
    detail: str
