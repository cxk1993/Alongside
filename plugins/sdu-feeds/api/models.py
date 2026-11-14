"""sdu-feeds 表模型。

★ 表名前缀 = 插件 id 的连字符换下划线：`sdu-feeds` → `sdu_feeds_*`
  （见 core/db_scope.py:182 的 table_prefix()；改前缀会直接丢掉数据边界）。
★ 时间列一律 TimestampTZ（UTC 存、本地读），禁止 naive datetime。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from db.base import PkMixin, TimestampMixin, TimestampTZ


class SduFeedSource(PkMixin, TimestampMixin, table=True):
    """一个抓取源（一个列表页 = 一个源）。"""

    __tablename__ = "sdu_feeds_source"

    #: 稳定短名（如 cs-announcements）—— 用户改 list_url 不会丢掉已抓条目
    key: str = Field(max_length=64, unique=True, index=True)
    name: str = Field(max_length=120)
    #: 相对链接的基准，一般给站点根
    base_url: str = Field(max_length=300)
    #: 列表页绝对地址
    list_url: str = Field(max_length=600)
    #: 正文链接正则（用来把导航栏排除掉）
    link_pattern: str = Field(max_length=300)
    #: 日期形态：auto | ymd | ym+d | md+y（见 api/parse.py）
    date_mode: str = Field(default="auto", max_length=16)
    enabled: bool = Field(default=True, index=True)
    last_synced_at: datetime | None = Field(default=None, sa_type=TimestampTZ)
    last_error: str = Field(default="", max_length=500)
    last_new_count: int = Field(default=0)


class SduFeedItem(PkMixin, TimestampMixin, table=True):
    """一条通知/讲座/活动。"""

    __tablename__ = "sdu_feeds_item"
    __table_args__ = (
        # ★ 唯一性按 (源, URL)：不同源可能指向同一篇（如本科生院转发他院通知），
        #   按全局 URL 唯一会把后到的源整条吞掉。
        UniqueConstraint("source_id", "url", name="uq_sdu_feeds_item_source_url"),
    )

    source_id: str = Field(max_length=64, index=True)
    url: str = Field(max_length=600, index=True)
    title: str = Field(max_length=300)
    published_on: date | None = Field(default=None, index=True)
    summary: str = Field(default="", max_length=2000)
    #: unread | read | archived
    state: str = Field(default="unread", max_length=16, index=True)
    #: 转成待办后回填的 todo id（幂等：重复 promote 不再建第二条）
    todo_id: str | None = Field(default=None, max_length=64)
