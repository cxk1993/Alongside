"""插件模型：理财（流水账 + BeeCount 只读快照）。

★ 表名必须以插件 id 为前缀（`finance_xxx`），否则内核拒绝建表。
★ 时间列一律用 `db.base.TimestampTZ`（UTC 存储 + 往返保时区）。
★ Mixin 里的字段只能用 sa_type=（用 sa_column= 会在多表继承时抛
  "Column object ... already assigned to Table"）。

字段设计：
  amount_cents  金额，整数分（避免浮点；恒为正，方向由 direction 表达）
  direction     expense（支出） | income（收入）
  category      分类（自由文本，v0.1 不做独立分类表）
  account       账户（自由文本，如 现金 / 招行 / 支付宝）
  occurred_at   发生时间（UTC，带时区写入）
  note          备注（可空长文本）

finance_snapshot（T08B 增补，字典业务表一节早已登记）：
  date          日历日（Asia/Shanghai），全局唯一 —— 幂等 upsert 的归属键
  total_asset / cash / invest / debt  整数分；BeeCount MCP 读不到细分时保持 0，
                                      原始摘要完整落在 meta_json
  meta_json     MCP 原始摘要（get_ledger_stats + get_analytics_summary），可截断
"""
from __future__ import annotations

from datetime import date as DateType
from datetime import datetime

from sqlalchemy import Date
from sqlalchemy import Text as SAText
from sqlmodel import Field

from db.base import PkMixin, TimestampMixin, TimestampTZ

DIR_EXPENSE = "expense"
DIR_INCOME = "income"
DIRECTIONS = (DIR_EXPENSE, DIR_INCOME)


class FinanceEntry(PkMixin, TimestampMixin, table=True):
    """一条流水。表名前缀 finance_。"""

    __tablename__ = "finance_entry"
    __table_args__ = {"extend_existing": True}

    # 整数分，恒 >= 0；符号语义交给 direction
    amount_cents: int = Field(ge=0)
    direction: str = Field(max_length=8, index=True)  # expense | income
    category: str = Field(default="", max_length=64, index=True)
    account: str = Field(default="", max_length=64, index=True)
    occurred_at: datetime = Field(sa_type=TimestampTZ, index=True)
    note: str | None = Field(default=None, sa_type=SAText)


class FinanceSnapshot(PkMixin, TimestampMixin, table=True):
    """每日资产/收支快照（BeeCount MCP 只读同步的落点）。

    ★ date 唯一：同日重复 sync 覆盖同一行，不翻倍（Alongside 侧权威去重）。
    ★ 金额整数分；无法从 BeeCount 拆出的分项保持 0，原始摘要在 meta_json。
    """

    __tablename__ = "finance_snapshot"
    __table_args__ = {"extend_existing": True}

    # 日历日语义（与 habits_habit_log.date 同款：SQLAlchemy Date，不是 datetime）
    # 字段别名 DateType 避免与 datetime.date 类型注解撞名
    date: DateType = Field(sa_type=Date, unique=True, index=True)
    total_asset: int = Field(default=0)
    cash: int = Field(default=0)
    invest: int = Field(default=0)
    debt: int = Field(default=0)
    meta_json: str | None = Field(default=None, sa_type=SAText)
