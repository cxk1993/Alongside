"""proposals 请求/响应 schema（bidiu 2026-11-21 · B1）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


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

    @field_validator("method")
    @classmethod
    def _method_allowed(cls, v: str) -> str:
        allowed = {"GET", "POST", "PUT", "PATCH", "DELETE"}
        up = v.strip().upper()
        if up not in allowed:
            raise ValueError(f"method 仅允许 {sorted(allowed)}，收到 {v!r}")
        return up

    @field_validator("path")
    @classmethod
    def _path_local_only(cls, v: str) -> str:
        """★ SSRF 防护（bidiu 2026-11-22 自查）：approve 时服务端会真的请求 path。

        httpx 的行为实测：`Client(base_url=X).get("http://evil/…")` **会忽略 base_url**
        直接打绝对 URL ⇒ 若签发者（AI/人）把 path 写成 `http://169.254.169.254/…`，
        人一签字平台就去打云元数据。**必须只允许站内相对路径**。
        """
        p = v.strip()
        if not p.startswith("/"):
            raise ValueError("path 必须以 / 开头（服务端内部相对路径）")
        if p.startswith("//") or "://" in p:
            raise ValueError("path 不允许绝对 URL / 协议相对 URL（SSRF 防护）")
        if any(seg == ".." for seg in p.split("/")):
            raise ValueError("path 不允许包含 .. 段（路径穿越防护）")
        return p

    @model_validator(mode="after")
    def _before_required_for_destructive(self) -> "ProposalCreate":
        """★ 破坏性动作必须带 before 快照（bidiu 2026-11-22 边界自查）。

        提案层的核心承诺是「可撤销」；但 delete 的 undo 靠 before 整行重建、
        update 的 undo 靠 before 回写。**没有快照的 delete/update 签了就是永久删除**——
        那与「事后悔可撤」直接矛盾（判据 5：声明与实现同一句话）。

        实测：before=null 的 delete 提案 approve 能执行（数据真删），undo 才报 422
        ——用户已经丢了数据，补偿性报错来不及了。⇒ 在**签发时**就拒绝。
        """
        if self.action in ("delete", "update") and self.before is None:
            raise ValueError(
                f"action={self.action} 需要 before 快照——否则签字执行后无法撤销"
                "（提案层承诺「可撤销」）。删/改前先取当前值再签发。"
            )
        return self
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
