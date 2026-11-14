"""i山大 插件模型：公告 / 通知缓存。

★ 表名必须带插件 id 前缀（`isdu_`），否则内核拒绝建表。
★ 时间列一律 `db.base.TimestampTZ`（UTC 存储 + 往返保时区）。
★ 本文件按包路径加载（modules.isdu.models），同源导入用绝对包路径。

v0.1 只落「公开可见」的数据：通知/公告。
课表 / 成绩 / 一卡通都要登录态，等鉴权链路打通再加表（见 README 的「下一步」）。
"""
from __future__ import annotations

from sqlalchemy import Text as SAText
from sqlmodel import Field, SQLModel

from db.base import PkMixin, TimestampMixin


class IsduNotice(PkMixin, TimestampMixin, SQLModel, table=True):
    """一条 i山大 公告（服务端 noticeId 唯一）。"""

    __tablename__ = "isdu_notice"

    notice_id: int = Field(index=True, unique=True, description="服务端 noticeId")
    title: str = Field(default="", max_length=200)
    content: str = Field(default="", sa_type=SAText)
    image: str = Field(default="", max_length=500)
    route: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=500)
    popup: int = Field(default=0, description="服务端 popup 标记，0/1")
