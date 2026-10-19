"""复盘出入参。

★ 出参直接返回资源本身，不要包 {code,data}。
★ 日期字段用 date（YYYY-MM-DD）；非法日期由 FastAPI → 422。
"""
from __future__ import annotations

from datetime import date as DateType
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    """Alongside 侧探活：是否连通上游 + 版本。失败也返回结构化信息，不甩 500。"""

    ok: bool
    upstream_mode: str = "mock"  # mock | api
    path: str = "mock"  # mock | direct | bridge
    bridge: bool = False
    version: str | None = None
    status: str | None = None
    recording: bool | None = None
    paused: bool | None = None
    message: str = ""


class SourceOut(BaseModel):
    """取数来源状态（前端「数据源小字」用）。"""

    mode: str  # mock | api
    path: str  # mock | direct | bridge
    bridge: bool = False
    bridge_online: bool | None = None
    upstream_online: bool = False
    upstream_version: str | None = None
    last_sync_at: datetime | None = None
    message: str = ""


class NamedRow(BaseModel):
    name: str
    seconds: int = 0
    duration_text: str = ""


class HourlyRow(BaseModel):
    hour: int
    seconds: int = 0
    duration_text: str = ""


class ReviewNoteOut(BaseModel):
    id: str
    date: DateType
    content_md: str
    created_at: datetime


class ReviewNoteCreate(BaseModel):
    date: DateType = Field(description="批注归属的日期（YYYY-MM-DD 日历日）")
    content_md: str = Field(min_length=1, max_length=20000, description="批注正文（Markdown）")


class DayListItem(BaseModel):
    date: DateType
    is_empty: bool = False
    total_seconds: int = 0
    category_count: int = 0
    app_count: int = 0
    has_ai: bool = False
    has_raw: bool = False
    raw_path: str = ""
    top_categories: list[NamedRow] = []
    synced_at: datetime | None = None


class DaysOut(BaseModel):
    items: list[DayListItem] = []
    total: int = 0
    page: int = 1
    size: int = 20
    has_more: bool = False


class DayDetail(BaseModel):
    date: DateType
    is_empty: bool = False
    empty_hint: str = ""
    categories: list[NamedRow] = []
    apps: list[NamedRow] = []
    domains: list[NamedRow] = []
    hourly: list[HourlyRow] = []
    ai_analysis_md: str = ""
    total_seconds: int = 0
    raw_path: str = ""
    has_raw: bool = False
    synced_at: datetime | None = None
    notes: list[ReviewNoteOut] = []
    source: SourceOut | None = None


class TrendPoint(BaseModel):
    date: DateType
    total_seconds: int = 0
    values: dict[str, int] = Field(default_factory=dict)


class TrendOut(BaseModel):
    metric: str = "total"
    days: int = 7
    points: list[TrendPoint] = []
    labels: list[str] = []
    conclusion: str = ""


class MetricDelta(BaseModel):
    key: str
    date_seconds: int = 0
    against_seconds: int = 0
    delta_seconds: int = 0
    delta_text: str = ""


class CompareOut(BaseModel):
    date: DateType
    against: DateType
    metrics: list[MetricDelta] = []
    conclusion: str = ""


class WeeklyOut(BaseModel):
    date: DateType
    available: bool = True
    offline_hint: str = ""
    week_start: str | None = None
    week_end: str | None = None
    total_seconds: int = 0
    days: list[dict[str, Any]] = []
    summary: str = ""
    source: str = "mock"
    cached: bool = False


class RawOut(BaseModel):
    date: DateType
    markdown: str = ""
    raw_path: str = ""
    found: bool = False


class IngestOut(BaseModel):
    date: DateType
    id: str
    raw_path: str
    is_empty: bool = False
    category_count: int = 0
    app_count: int = 0
    has_ai: bool = False
    has_raw: bool = False
    synced_at: datetime | None = None
    mode: str = "mock"
    path: str = "mock"
    event: str = "review.day.ingested"


class NotesListOut(BaseModel):
    date: DateType | None = None
    items: list[ReviewNoteOut] = []


MetricName = Literal["total", "category", "app"]
