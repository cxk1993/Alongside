"""插件模型：资产层（可沉淀的经验 · contracts/asset.schema.json 的落地）。

★ 表名必须以插件 id 为前缀（assets_），否则内核拒绝建表。
★ 时间列一律 db.base.TimestampTZ（UTC 存储 + 往返保时区）。
★ 本文件只 import db.base（内核），不 import 其它插件 —— 保持数据库隔离。

三条硬规则（契约）：
1) append-only + 显式 supersede（不许整块重写；旧条标 superseded 并反向指 superseded_by）
2) 必须留 provenance（agent + at；可选 inputs / session_id）
3) 必须可单条硬删（用户有权真正删除）
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field

from db.base import TimestampMixin

# 契约枚举（asset.schema.json）
ASSET_TYPES = ("note", "skill", "checklist", "decision", "procedure")
ASSET_STATUSES = ("active", "superseded", "draft")
ASSET_TIERS = ("public", "work", "private", "sealed")
ASSET_SCOPES = ("private", "shared")


def new_asset_id() -> str:
    """稳定 id：前缀 `as_`（契约 pattern ^as_[A-Za-z0-9_-]{6,}$）。永不复用。"""
    return "as_" + uuid4().hex[:12]


class Asset(TimestampMixin, table=True):
    """一条资产。append-only：更新=新建一条 + supersede 旧的。"""

    __tablename__ = "assets_asset"

    # 自定义主键（不用 PkMixin：契约要求 as_ 前缀，不是 32 位裸 hex）
    id: str = Field(default_factory=new_asset_id, primary_key=True, max_length=40)
    schema_version: int = Field(default=1)

    type: str = Field(max_length=20, index=True)
    title: str = Field(max_length=120)
    body: str = Field(sa_column=Column(Text, nullable=False))
    summary: str | None = Field(default=None, max_length=200)

    # provenance: {agent, at, inputs?, session_id?}（必填，缺了不可信）
    provenance: Any = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))

    # supersedes: ["as_xxx", ...]；superseded_by: "as_yyy"（与 supersedes 互为镜像）
    supersedes: Any = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    superseded_by: str | None = Field(default=None, max_length=40)

    status: str = Field(default="active", max_length=16, index=True)
    tier: str = Field(default="private", max_length=16)   # 默认 private（柜子模型）
    scope: str = Field(default="private", max_length=16)  # v1 一律 private
    exportable: bool = Field(default=True)

    tags: Any = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    refs: Any = Field(default_factory=list, sa_column=Column(JSON, nullable=False))