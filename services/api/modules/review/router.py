"""复盘路由（薄：参数校验 + 调 service）。

HTTP 契约：成功直接返回资源 JSON；失败由内核转 RFC7807。
★ 前缀由内核按 manifest.api.base 自动加，这里不要写 prefix=。
★ 路径参数用 Annotated[str, FPath(...)]；本文件不要加 future import。
★ 204 端点写 `-> None`。
★ 对 Work-Review 只读：本文件的 POST 只写 Alongside 自己的库。
"""
import json
from typing import Any
from datetime import date as DateType
from pathlib import Path

from fastapi import APIRouter, Depends, Query, status
from fastapi import Path as FPath
from typing import Annotated
from sqlmodel import Session

from core.deps import get_current_user, get_db
from core.security import User

from .schema import (
    CompareOut,
    DayDetail,
    DaysOut,
    HealthOut,
    IngestOut,
    NotesListOut,
    RawOut,
    ReviewNoteCreate,
    ReviewNoteOut,
    SourceOut,
    TrendOut,
    WeeklyOut,
)
from .service import ReviewService, local_today

router = APIRouter()

_MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"
with _MANIFEST_PATH.open(encoding="utf-8") as _f:
    _MANIFEST = json.load(_f)

DbDep = Session
UserDep = User


@router.get("/health", response_model=HealthOut)
def health(
    db: DbDep = Depends(get_db),
) -> HealthOut:
    """探活：透传 Work-Review GET /health（mock 返回内置版本）。不强制鉴权，便于运维。"""
    return ReviewService(db).health()


@router.get("/manifest")
def manifest() -> dict:
    """模块清单（前端 / AI 发现能力用）。"""
    return _MANIFEST


@router.get("/source", response_model=SourceOut)
def source(
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> SourceOut:
    """上游数据源状态：Work-Review 接入模式（mock/api）、走直连还是桥、**桥是否在线**。"""
    return ReviewService(db).source()


@router.get("/days", response_model=DaysOut)
def list_days(
    frm: DateType | None = Query(None, alias="from", description="起始日（YYYY-MM-DD 日历日）"),
    to: DateType | None = Query(None, description="结束日（YYYY-MM-DD 日历日）"),
    page: int = Query(1, ge=1, description="页码（从 1 起）"),
    size: int = Query(20, ge=1, le=500, description="每页条数（1–500）"),
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> DaysOut:
    """按日期区间分页列日报（`frm`/`to` 是 YYYY-MM-DD 的**日历日**）。"""
    return ReviewService(db).list_days(frm=frm, to=to, page=page, size=size)


@router.get("/day", response_model=DayDetail)
def get_day(
    date: DateType = Query(..., description="YYYY-MM-DD"),
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> DayDetail:
    """读某一天复盘明细（分类 / 应用耗时等）。"""
    return ReviewService(db).get_day(date)


@router.get("/today-summary")
def today_summary(
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> dict:
    """U2 规范 v1（MiMo）：{title, items:[{text,state}], link}。

    空结果返回空 items，不抛错；形状对齐 workbuddy 《today-summary 数据源规范 v1》。
    """
    svc = ReviewService(db)
    day = local_today()
    detail = svc.get_day(day)
    notes_out = svc.list_notes(day)
    has_review = not detail.is_empty
    items: list[dict] = []
    if has_review:
        summary = (getattr(detail, "ai_analysis_md", "") or "").strip()
        items.append({"text": (summary[:40] if summary else "今日复盘"), "state": "info"})
    for n in getattr(notes_out, "items", [])[:5]:
        content = (getattr(n, "content_md", "") or "").strip().replace("\n", " ")
        items.append({"text": content[:40] or "批注", "state": "info"})
    return {
        "title": f"今日复盘 {len(items)} 条" if items else "今日复盘",
        "items": items,
        "link": "/review",
    }


@router.get("/trend", response_model=TrendOut)
def get_trend(
    metric: str = Query("total", description="total | category | app"),
    days: int = Query(7, ge=1, le=90, description="回看多少天（1–90，默认 7）"),
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> TrendOut:
    """按指标看趋势：最近 `days` 天的变化（前端画曲线用）。"""
    return ReviewService(db).trend(metric=metric, days=days)


@router.get("/compare", response_model=CompareOut)
def get_compare(
    date: DateType = Query(..., description="要看的那一天（YYYY-MM-DD 日历日）"),
    against: DateType = Query(..., description="对比日（YYYY-MM-DD）—— 拿它与 date 比"),
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> CompareOut:
    """**两天对比**：`date`（基准日）vs `against`（对比日）。"""
    return ReviewService(db).compare(date, against)


@router.get("/weekly", response_model=WeeklyOut)
def get_weekly(
    date: DateType | None = Query(None, description="周内任意一天；缺省今天"),
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> WeeklyOut:
    """某一周（含 `date` 的那一周）的汇总。"""
    return ReviewService(db).weekly(date or local_today())


@router.get("/raw", response_model=RawOut)
def get_raw(
    date: DateType = Query(..., description="要看的那一天（YYYY-MM-DD 日历日）"),
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> RawOut:
    """回看某一天的**原始记录**（未经归类的明细）。"""
    return ReviewService(db).get_raw(date)


@router.get("/notes", response_model=NotesListOut)
def list_notes(
    date: DateType | None = Query(None, description="批注归属日（YYYY-MM-DD；不传=今天）"),
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> NotesListOut:
    """列某一天的**批注**（自己写的复盘备注）。"""
    return ReviewService(db).list_notes(date)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: Annotated[str, FPath(description="批注 id —— 从 GET /notes 的结果里取")],
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> None:
    """删除一条主人批注（主人⑤「增减笔记」的"减"半边 · astrbot 下场）。"""
    ReviewService(db).delete_note(note_id)


@router.post("/ingest-all")
def ingest_all(
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """★ 全量同步历史日报（astrbot 下场 · 主人「同步理应同步历史所有日报」）。

    对 Work-Review **只读**；幂等；返回 {total, ok, skipped, failed, errors}。
    """
    return ReviewService(db).ingest_all()


@router.post("/ingest", response_model=IngestOut)
def ingest(
    date: DateType | None = Query(None, description="缺省为今天（Asia/Shanghai）"),
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> IngestOut:
    """从 Work-Review 拉取某日并写入 Alongside 库（幂等）。对上游只读。"""
    return ReviewService(db).ingest(date)


@router.post("/notes", response_model=ReviewNoteOut)
def create_note(
    body: ReviewNoteCreate,
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> ReviewNoteOut:
    """写 Alongside 自己的批注，不写回 Work-Review / 不污染 raw。"""
    return ReviewService(db).add_note(body.date, body.content_md)
