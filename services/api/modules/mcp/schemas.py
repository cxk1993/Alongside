"""mcp 模块自己的请求/响应 schema（T18）。

★ HTTP 契约：成功直接返回资源 JSON；失败由内核统一转 RFC7807。
★ PAT 明文（token 字段）只存在于 PatCreatedOut —— 创建响应，一次性的。
"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# scope 形如 "域:动词"（如 某域:read），与 provides 推导口径一致。
# ★ 错误消息里不得出现具体业务词举例 —— mcp 模块零业务词（自检测试守着）。
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9-]*:[a-z][a-z0-9-]*$")
_SCOPE_HINT = "scope 形如 域:动词（首段为能力域、末段为动词，如 某域:read）"


class PatCreateIn(BaseModel):
    """POST /pats 请求体。"""

    name: str = Field(
        min_length=1, max_length=64, description="给这枚 PAT 起个人话名字，便于日后在列表里认出它（如「云昔 (astrbot)」）"
    )
    # 空列表 = 不给任何 scope（创建了也调不了工具，安全默认）。
    # ★ 2026-09-27（云昔）：32 → 1024。原上限是"防灌爆"的形态守卫，但平台现有
    #   30 个活 scope，再上一个模块就顶格 ——「发一枚全权限令牌」会变成不可能
    #（实测 32+1 直接 422）。放宽到 1024；真正该收紧的是 scope 语义，不是数字。
    scopes: list[str] = Field(
        default_factory=list,
        max_length=1024,
        description="能力清单，每条形如「域:动词」（首段为能力域、末段为动词）。具体有哪些，看 tools/list 里每个工具名的前两段。**空列表 = 这枚令牌什么都调不了**（安全默认）",
    )
    # ★ S2（bidiu 2026-11-21）：可选过期时刻（ISO8601，建议带时区）。
    #   此前 expires_at 列「有校验、无写入路径」→ 所有 PAT 事实永不过期。
    #   缺省 None = 不设期限（与存量行为一致，不破坏已发的 PAT）。
    expires_at: datetime | None = Field(
        default=None,
        description="可选：到期时刻（ISO8601，如 2026-12-31T00:00:00+08:00）。到点该 PAT 自动 401。不传 = 永不过期",
    )

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name 不能为空白")
        return v

    @field_validator("scopes")
    @classmethod
    def _scopes_shape_create(cls, v: list[str]) -> list[str]:
        for s in v:
            if not _SCOPE_RE.match(s):
                raise ValueError(f"{_SCOPE_HINT}，收到 {s!r}")
        # 去重保序
        return list(dict.fromkeys(v))


class PatScopesPatch(BaseModel):
    """PATCH /pats/{id} 请求体：只允许改 scopes（收窄/扩大范围）。"""

    scopes: list[str] = Field(
        max_length=1024,
        description="**整组替换**该 PAT 的能力清单（不是追加）——要保留原 scope 请连原 scope 一起传",
    )  # ★ 2026-09-27：32 → 1024（同上）

    @field_validator("scopes")
    @classmethod
    def _scopes_shape_patch(cls, v: list[str]) -> list[str]:
        for s in v:
            if not _SCOPE_RE.match(s):
                raise ValueError(f"{_SCOPE_HINT}，收到 {s!r}")
        return list(dict.fromkeys(v))


class PatCreatedOut(BaseModel):
    """创建成功响应 —— token 明文只在这里出现这一次。"""

    id: str
    name: str
    token: str
    token_prefix: str
    scopes: list[str]
    created_at: datetime


class PatOut(BaseModel):
    """列表 / 查询响应：绝不含 token 明文。"""

    id: str
    name: str
    token_prefix: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class ToolOut(BaseModel):
    """GET /tools：当前暴露给 AI 的全部工具（调试用，纯 JSON 版 tools/list）。"""

    name: str
    description: str
    method: str
    path: str
    scope: str
    plugin_id: str


class AuditOut(BaseModel):
    """GET /audit-logs：经 MCP 的操作流水（读内核 audit_log）。"""

    id: str
    actor: str
    action: str
    target: str
    at: datetime
