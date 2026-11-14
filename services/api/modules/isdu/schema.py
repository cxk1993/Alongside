"""i山大 插件入参/出参。

★ 出参不包 {"code":0,"data":...}：直接返回资源本身（全项目统一口径）。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NoticeOut(BaseModel):
    """一条公告（与上游字段一一对应，避免前端二次映射）。"""

    notice_id: int
    title: str = ""
    content: str = ""
    image: str = ""
    route: str = ""
    url: str = ""
    popup: int = 0


class SyncOut(BaseModel):
    synced: int = Field(description="本次从上游落库的公告条数")
    last_sync_at: datetime | None = None


class StatusOut(BaseModel):
    adapter: str = Field(description="ready / partial / not_ready")
    notice_count: int = 0
    last_sync_at: datetime | None = None
    supports: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class CastgcIn(BaseModel):
    """浏览器 CAS 登录后的 CASTGC（TGT-...）。只用于换 token，不落库。"""

    castgc: str = Field(min_length=8, max_length=400)
    #: CAS「信任此设备」写下的设备指纹 cookie；部分部署把 TGT 绑在设备上，兑换要带上它
    device: str | None = Field(default=None, max_length=400)


class LoginOut(BaseModel):
    logged_in: bool
    token_preview: str = ""
