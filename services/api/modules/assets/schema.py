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
    session_id: str | None = Field(default=None, max_length=64, description="来源会话 id（便于回溯是哪次对话里沉淀的）")


class RefIn(BaseModel):
    kind: Literal["asset", "module", "url"] = Field(description="引用类型：asset=另一条资产 / module=模块 id / url=外部链接")
    id: str = Field(min_length=1, max_length=300, description="被引用对象的 id 或 URL")
    label: str | None = Field(default=None, max_length=120, description="展示用短标签（缺省用 id）")


class AssetCreate(BaseModel):
    """POST /assets —— 沉淀一条资产。

    带 supersedes 时服务端在同一事务里：建新条 + 把旧条标 superseded 并回指 superseded_by
    （append-only：旧条永不整块改写内容，只改状态与反向指针）。
    """

    type: Literal["note", "skill", "checklist", "decision", "procedure"] = Field(
        description="资产类型：note 笔记 / skill 可复用技能 / checklist 核对清单 / decision 决策记录 / procedure 操作步骤"
    )
    title: str = Field(min_length=1, max_length=120, description="标题（1~120 字，检索主要靠它）")
    body: str = Field(default="", description="正文（Markdown），无长度上限")
    summary: str | None = Field(default=None, max_length=200, description="一句话摘要（≤200 字），列表页展示用")
    provenance: ProvenanceIn = Field(description="来源凭据：谁写的、什么时候、基于什么。缺 agent 视为不可信")
    supersedes: list[str] = Field(default_factory=list, description="被本条取代的旧 asset id")
    status: Literal["active", "draft"] = Field(default="active", description="active 生效 / draft 草稿（草稿不参与检索）")
    tier: Literal["public", "work", "private", "sealed"] = Field(
        default="private", description="可见层级：public 公开 / work 工作 / private 私人 / sealed 封存"
    )
    scope: Literal["private", "shared"] = Field(default="private", description="作用域：private 仅本人 / shared 可共享")
    exportable: bool = Field(default=True, description="是否允许导出（false = 只在本平台内可见）")
    tags: list[str] = Field(default_factory=list, max_length=12, description="标签（最多 12 个，每个 ≤24 字）")
    refs: list[RefIn] = Field(default_factory=list, description="引用关系：关联的资产 / 模块 / 外部链接")

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