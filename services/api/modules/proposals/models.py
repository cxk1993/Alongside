"""插件模型：变更提案（签字/撤销地基）。

★ 表名必须以插件 id 为前缀（proposals_），否则内核拒绝建表。
★ 时间列一律用 db.base.TimestampTZ（UTC 存储 + 往返保时区）。
★ Mixin 里的字段只能用 sa_type=（多表继承时 sa_column 会撞 "already assigned"）。
★ 本文件只 import db.base（内核），不 import 其它插件——保持数据库隔离。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field

from db.base import PkMixin, TimestampMixin, TimestampTZ

# 提案状态机（bidiu 2026-11-21 · B1）：
#   pending → approved（签字执行）→ undone（撤销回放）
#   pending → rejected（作废）
# rejected / undone 是终态。
PROPOSAL_STATUS = ("pending", "approved", "rejected", "undone")

# 动作三分：create（新增资源）/ update（改已有）/ delete（删已有）。
# 决定 before 快照形态与撤销策略：
#   create → before 为 null，**不可撤销**（V1 边界，显式 409 拒绝，不做假按钮）
#   update → before = 原值快照，撤销 = 同工具回放 before
#   delete → before = 删除前整行，撤销 = create 工具回放 before
ACTION_KINDS = ("create", "update", "delete")


class ChangeProposal(PkMixin, TimestampMixin, table=True):
    """一条变更提案（proposals_change_proposal）。

    谁发起（actor）→ 要干什么（title/tool/payload）→ 变更前长什么样（before）
    → 人签没签（status/executed_at）→ 撤没撤（undone_at/undo_result）。
    审计的「行动可记录可补救」就是这张表：记录在 audit_log，补救在本表。
    """

    __tablename__ = "proposals_change_proposal"

    # ── 发起 ──
    title: str = Field(max_length=300, description="要做什么（人话）")
    tool_name: str = Field(max_length=128, description="执行工具（如 todo_item_delete）")
    action: str = Field(max_length=16, description="create / update / delete")
    method: str = Field(max_length=8, description="执行 HTTP 方法（签发时快照）")
    path: str = Field(max_length=300, description="执行路径（签发时快照，可含 {占位}）")
    payload: Any = Field(default_factory=dict, sa_column=Column(JSON, nullable=True))
    before: Any = Field(default=None, sa_column=Column(JSON, nullable=True))
    #: 发起者：`mcp:<pat name>`（经 MCP 拦截）或 `human`（登录会话直接签发）
    actor: str = Field(default="human", max_length=80)

    # ── 状态机 ──
    status: str = Field(default="pending", max_length=16, index=True)
    executed_at: datetime | None = Field(default=None, sa_type=TimestampTZ)
    #: 执行成功且响应带资源 id 时记下（create 类撤销定位用；V1 create 不可撤销故仅留证）
    executed_id: str | None = Field(default=None, max_length=64)
    executed_status: int | None = Field(default=None, description="执行时的 HTTP 状态码")
    undone_at: datetime | None = Field(default=None, sa_type=TimestampTZ)
    undo_result: str | None = Field(default=None, sa_column=Column(Text))
