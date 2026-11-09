"""课程表 · 上课前提醒调度（复用 calendar / todo 同款三段式）。

链路（ADR-0002 不跨模块 import 插件本体）：
    remind_scheduler 扫「即将上课」
      → event_bus.publish("course.session.due")
        → push.link 监听（push 模块）
          → web push（浏览器 / PWA 通知）

设计要点：
- **env 开关**：`COURSE_REMIND_ENABLED=true` 才起（与 calendar/todo 同款纪律，默认关）。
  非依赖场景必须走 `read_setting`（裸 os.environ 读不到 .env —— todo 那次的教训）。
- **轮询**：默认 60s（env `COURSE_REMIND_POLL_SECONDS`）。
- **提前量**：`COURSE_REMIND_LEAD_MINUTES`（默认 15）—— 上课前 15 分钟提醒。
- **窗口**：只提醒「未来 lead 分钟内将开始」的课（防重启补发一堆历史）。
- **幂等**：进程内 `_notified` 集合，键 = `课程id@日期`（同一天同一节课只提醒一次）。
- **不打断**：任何异常只 warning，绝不影响主进程与其它调度。
"""
from __future__ import annotations

import logging
from datetime import UTC, date as DateType, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from core.config import read_setting
from core.events import event_bus

from .models import CourseItem
from .service import _parse_weeks, _term_week, local_today

log = logging.getLogger("course.remind")

EVENT_DUE = "course.session.due"
DEFAULT_POLL_SECONDS = 60
# ★ 2026-09-27（主人）：提前量由 15 改 **30** 分钟 —— 出门/换楼来得及。
DEFAULT_LEAD_MINUTES = 30

_SH_TZ = ZoneInfo("Asia/Shanghai")
_lock_state: dict[str, Any] = {"scheduler": None}
_notified: set[str] = set()


def _enabled() -> bool:
    raw = (read_setting("COURSE_REMIND_ENABLED", "false") or "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _poll_seconds() -> int:
    try:
        raw = read_setting("COURSE_REMIND_POLL_SECONDS", str(DEFAULT_POLL_SECONDS))
        return max(10, int(raw or DEFAULT_POLL_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_POLL_SECONDS


def _lead_minutes() -> int:
    try:
        raw = read_setting("COURSE_REMIND_LEAD_MINUTES", str(DEFAULT_LEAD_MINUTES))
        return max(0, int(raw or DEFAULT_LEAD_MINUTES))
    except (TypeError, ValueError):
        return DEFAULT_LEAD_MINUTES


def _todays_sessions(db: Session, *, now: datetime) -> list[tuple[CourseItem, datetime]]:
    """今天要上的课 → [(课, 开课时刻)]。只算有 start_time 且在今天的。"""
    today = now.astimezone(_SH_TZ).date()
    wd = today.weekday()
    rows = list(
        db.exec(
            select(CourseItem).where(
                CourseItem.enabled == True,  # noqa: E712
                CourseItem.weekday == wd,
            )
        ).all()
    )
    out: list[tuple[CourseItem, datetime]] = []
    for r in rows:
        if not r.start_time or ":" not in r.start_time:
            continue
        # 周次过滤（空表达式 = 每周都上）
        weeks = _parse_weeks(r.weeks)
        if weeks is not None:
            w = _term_week(r.term_start, today)
            if w is None or w not in weeks:
                continue
        try:
            hh, mm = (int(x) for x in r.start_time.split(":", 1))
        except (TypeError, ValueError):
            continue
        start_dt = datetime(today.year, today.month, today.day, hh, mm, tzinfo=_SH_TZ).astimezone(UTC)
        out.append((r, start_dt))
    return out


def collect_due_courses(db: Session, *, now: datetime | None = None) -> list[tuple[CourseItem, datetime]]:
    """收集**刚进入提醒窗口**的课：提醒点 = 开课时刻 - lead。

    窗口上界 = now + lead（未来 lead 分钟内将开课）；
    下界 = now（只提醒还没开始的，开课后不再补推）。
    """
    now = now or datetime.now(UTC)
    lead = _lead_minutes()
    due: list[tuple[CourseItem, datetime]] = []
    for row, start_dt in _todays_sessions(db, now=now):
        key = f"{row.id}@{start_dt.astimezone(_SH_TZ).date().isoformat()}"
        if key in _notified:
            continue
        fire_at = start_dt - timedelta(minutes=lead)
        if fire_at <= now < start_dt:
            due.append((row, start_dt))
    return due


def run_course_tick(db: Session) -> int:
    """一轮扫描：对即将上课的课发布 `course.session.due` 事件。返回发布条数。"""
    due = collect_due_courses(db)
    sent = 0
    for row, start_dt in due:
        day = start_dt.astimezone(_SH_TZ).date()
        try:
            event_bus.publish(
                EVENT_DUE,
                payload={
                    "item_id": row.id,
                    "title": row.name,
                    "location": row.location or "",
                    "teacher": row.teacher or "",
                    "start_at": start_dt.isoformat(),
                    "start_time": row.start_time or "",
                },
                source="course",
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001 — 单条失败不影响其它课
            log.warning("publish course.session.due failed: %s", exc)
        _notified.add(f"{row.id}@{day.isoformat()}")  # 无论成败都记账（防每轮重试轰炸）
    if sent:
        log.info("上课提醒已发布", extra={"count": sent})
    return sent


def start_scheduler() -> bool:
    """Interval 调度：默认关（`COURSE_REMIND_ENABLED=true` 开启）。"""
    if not _enabled():
        log.info("上课提醒未启用（COURSE_REMIND_ENABLED!=true）")
        return False
    if _lock_state.get("scheduler") is not None:
        return False

    from apscheduler.schedulers.background import (
        BackgroundScheduler,  # type: ignore[import-untyped]
    )

    def _job() -> None:
        from core.deps import kernel_session

        try:
            with kernel_session() as db:
                run_course_tick(db)
        except Exception as exc:  # noqa: BLE001 — tick 失败不打断主进程
            log.warning("course remind tick failed: %s", exc)

    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    sched.add_job(_job, "interval", seconds=_poll_seconds(), id="course_remind_tick")
    sched.start()
    _lock_state["scheduler"] = sched
    log.info("上课提醒调度已启动", extra={"poll_seconds": _poll_seconds(), "lead_minutes": _lead_minutes()})
    return True


def stop_scheduler() -> None:
    sched = _lock_state.get("scheduler")
    if sched is not None:
        sched.shutdown(wait=False)
        _lock_state["scheduler"] = None


def scheduler_status() -> dict[str, Any]:
    return {
        "enabled": _enabled(),
        "running": _lock_state.get("scheduler") is not None,
        "poll_seconds": _poll_seconds(),
        "lead_minutes": _lead_minutes(),
        "notified_in_process": len(_notified),
    }
