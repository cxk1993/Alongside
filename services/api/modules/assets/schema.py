"""assets 请求/响应 schema（契约 contracts/asset.schema.json 的 pydantic 投射）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ProvenanceIn(BaseModel):
    """provenance：谁写的、什么时候、基于什么。缺 agent 不可信。"""

    agent: str = Field(min_length=1, max_length=80, description="human | agent:<name> | mcp:<pat_name>")
    at: datetime | None = Field(default=None, description="缺省=服务端 now（UTC）")
    inputs: list[str] = Field(default_factory=list, description="基于哪些材料（路径 / asset id / 会话 id）")
    session_id: str | None = Field(default=None, max_length=64)


class RefIn(BaseModel):
    kind: Literal["asset", "module", "url"]
    id: str = Field(min_length=1, max_length=300)
    label: str | None = Field(default=None, max_length=120)


class AssetCreate(BaseModel):
    """POST /assets —— 沉淀一条资产。

    带 supersedes 时服务端在同一事务里：建新条 + 把旧条标 superseded 并回指 superseded_by
    （append-only：旧条永不整块改写内容，只改状态与反向指针）。
    """

    type: Literal["note", "skill", "checklist", "decision", "procedure"]
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", description="正文（Markdown），无长度上限")
    summary: str | None = Field(default=None, max_length=200)
    provenance: ProvenanceIn
    supersedes: list[str] = Field(default_factory=list, description="被本条取代的旧 asset id")
    status: Literal["active", "draft"] = "active"
    tier: Literal["public", "work", "private", "sealed"] = "private"
    scope: Literal["private", "shared"] = "private"
    exportable: bool = True
    tags: list[str] = Field(default_factory=list, max_length=12)
    refs: list[RefIn] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def _tag_len(cls, v: list[str]) -> list[str]:
        for t in v:
            if len(t) > 24:
                raise ValueError(f"tag 最长 24 字符：{t!r}")
        return v


class AssetOut(BaseModel):
    """资产查询响应（契约里的全部字段）。"""

    schema_version: int
    id: str
    type: str
    title: str
    body: str
    summary: str | None
    provenance: dict[str, Any]
    supersedes: list[str]
    status: str
    superseded_by: str | None
    tier: str
    scope: str
    exportable: bool
    tags: list[str]
    refs: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime