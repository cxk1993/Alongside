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
    # ★ 2026-11-22（汐瑶 P1 实测）：此前注释写「空=服务端按工具机械推导」，但推导
    #   **未实现**（执行时 422）——按「声明与实现必须说同一句话」（团队判据 5）改为必填：
    #   MCP 拦截点会带工具方法/路径模板；人工签发由前端填。提案层不 import 业务模块，
    #   无法在服务端反查工具映射。
    method: str = Field(min_length=1, max_length=10, description="执行 HTTP 方法（必填；MCP 拦截点自动带）")
    path: str = Field(
        min_length=1,
        max_length=500,
        description="执行路径（必填；推荐用 {占位符} 模板，真实 id 亦可，撤销时按结构降为集合路径）",
    )
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
    executed_by: str | None = None
    undone_at: datetime | None
    undone_by: str | None = None
    undo_result: str | None


class ProposalOpOut(BaseModel):
    """approve / reject / undo 的响应。"""

    id: str
    status: str
    detail: str
