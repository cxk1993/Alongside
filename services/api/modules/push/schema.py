"""push 插件出入参。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SubscribeKeys(BaseModel):
    """浏览器生成的订阅密钥对（前端 `subscription.toJSON().keys` 原样回传即可）。"""

    p256dh: str = Field(min_length=10, max_length=200, description="浏览器公钥（base64url）")
    auth: str = Field(min_length=5, max_length=200, description="认证密钥（base64url）")


class SubscribeIn(BaseModel):
    """登记一个推送订阅（内容就是浏览器 `PushSubscription.toJSON()`）。"""

    endpoint: str = Field(
        min_length=20,
        max_length=500,
        description="推送服务商给的订阅地址（如 Windows 的 WNS、Chrome 的 FCM）—— 它本身就是设备凭证，故本接口不鉴权",
    )
    keys: SubscribeKeys
    user_agent: str | None = Field(
        default=None, max_length=200, description="浏览器 UA（可选，便于在订阅列表里认出是哪台设备）"
    )


class SubscriptionOut(BaseModel):
    id: str
    endpoint_prefix: str
    user_agent: str | None
    active: bool
    last_sent_at: datetime | None
    last_status: int | None
    fail_count: int
    created_at: datetime


class SendIn(BaseModel):
    """手动广播内容。"""

    title: str = Field(
        min_length=1, max_length=200, description="通知标题（通知栏第一行，尽量短，如「【云昔】推送通道已开通」）"
    )
    body: str = Field(
        default="", max_length=2000, description="通知正文（第二行起的详细内容）"
    )
    url: str = Field(
        default="/", max_length=500, description="点击通知后打开的站内路径（如 `/` 或 `/pi/`）"
    )
    tag: str = Field(
        default="alongside",
        max_length=64,
        description="通知分组标签：**同 tag 的新通知会覆盖旧的**（不刷屏）。想让多条独立并存就传不同 tag",
    )


class SendResultOut(BaseModel):
    ok: bool
    sent: int
    pruned: int = 0
    detail: str | None = None


class PushLogOut(BaseModel):
    id: str
    topic: str
    title: str
    body: str | None
    ok: bool
    detail: str | None
    sent_at: datetime


class PushHealthOut(BaseModel):
    ok: bool = True
    enabled: bool = False
    vapid_ready: bool = False
    pywebpush_installed: bool = False
    subscriptions_active: int = 0
