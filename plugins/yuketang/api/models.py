"""雨课堂表模型。

★ 表名前缀 = 插件 id 的连字符换下划线：`yuketang` → `yuketang_*`
  （见 core/db_scope.py 的 table_prefix()；改前缀会直接丢掉数据边界）。
★ 时间列一律 TimestampTZ（UTC 存、本地读），禁止 naive datetime。
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field

from db.base import PkMixin, TimestampMixin, TimestampTZ


class YuketangSession(PkMixin, TimestampMixin, table=True):
    """用户自己账号的网页会话（一个部署只存一行）。"""

    __tablename__ = "yuketang_session"

    #: 浏览器 Cookie 里的 sessionid 值。★ 明文存是本插件当前最大的安全债：
    #:   runner 的凭据链路（secretRef）还没落地，先按「用户可一键删除」处理。
    session_id: str = Field(max_length=500)
    #: 标准版 / 学校私有云（如 changjiang.yuketang.cn）的根地址
    base_url: str = Field(default="https://www.yuketang.cn", max_length=300)
    #: 校验通过后拿到的显示名（只用于给用户确认「登录的是谁」）
    user_name: str = Field(default="", max_length=120)
    last_verified_at: datetime | None = Field(default=None, sa_type=TimestampTZ)
    last_error: str = Field(default="", max_length=500)


class YuketangSynced(PkMixin, TimestampMixin, table=True):
    """一条活动 → 待办的幂等映射（重复同步不会建第二条）。"""

    __tablename__ = "yuketang_synced"

    #: `{classroom_id}:{activity_id}` —— 跨课程唯一
    activity_key: str = Field(max_length=200, unique=True, index=True)
    classroom_id: str = Field(default="", max_length=64)
    title: str = Field(default="", max_length=300)
    todo_id: str = Field(default="", max_length=64)
    synced_at: datetime | None = Field(default=None, sa_type=TimestampTZ)
