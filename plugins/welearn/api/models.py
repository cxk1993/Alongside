"""WE Learn 表模型。

★ 表名前缀 = 插件 id：welearn_*（见 core/db_scope.py 的 table_prefix()）。
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field

from db.base import PkMixin, TimestampMixin, TimestampTZ


class WelearnSession(PkMixin, TimestampMixin, table=True):
    """整条 Cookie 串（含 .AspNet.Cookies）。"""

    __tablename__ = "welearn_session"

    cookie: str = Field(max_length=4000)
    base_url: str = Field(default="https://welearn.sflep.com", max_length=300)
    user_name: str = Field(default="", max_length=120)
    last_verified_at: datetime | None = Field(default=None, sa_type=TimestampTZ)
    last_error: str = Field(default="", max_length=500)


class WelearnSynced(PkMixin, TimestampMixin, table=True):
    """单元 → 待办的幂等映射。"""

    __tablename__ = "welearn_synced"

    unit_key: str = Field(max_length=200, unique=True, index=True)
    course_id: str = Field(default="", max_length=64)
    title: str = Field(default="", max_length=300)
    todo_id: str = Field(default="", max_length=64)
    synced_at: datetime | None = Field(default=None, sa_type=TimestampTZ)
