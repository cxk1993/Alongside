"""T25 calendar 提醒：collect/tick/持久化去重/日志（不触网）。"""
from __future__ import annotations
from core.db_scope import declare_scope  # 2026-11-09 作用域翻转：显式声明

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("DB_PATH", "./data/tmp_t25_cal.db")

import pytest  # noqa: E402
from sqlmodel import Session, create_engine  # noqa: E402

from db.base import SQLModel  # noqa: E402
from modules.calendar.models import CalendarEvent  # noqa: E402
from modules.calendar.reminder_scheduler import (  # noqa: E402
    _lock_state,
    collect_due_events,
    list_reminder_logs,
    run_reminder_tick,
    scheduler_status,
    start_scheduler,
    stop_scheduler,
)


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path}/t25.db")
    SQLModel.metadata.create_all(engine)
    session = declare_scope(Session(engine))
    _lock_state["notified"] = {}
    yield session
    session.close()


def _mk_event(db: Session, title: str, start: datetime, all_day: bool = False) -> CalendarEvent:
    ev = CalendarEvent(
        title=title,
        start_at=start,
        end_at=start + timedelta(hours=1),
        all_day=all_day,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def test_collect_due_window_and_skip_allday(db: Session) -> None:
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    _mk_event(db, "到点", now - timedelta(minutes=1))
    _mk_event(db, "提前", now + timedelta(minutes=5))
    _mk_event(db, "太远", now + timedelta(hours=3))
    _mk_event(db, "全天", now, all_day=True)
    due = collect_due_events(db, now=now, lead_minutes=10)
    titles = {e.title for e in due}
    assert "到点" in titles
    assert "提前" in titles
    assert "太远" not in titles
    assert "全天" not in titles


def test_tick_notifies_once_then_dedup_memory_and_db(db: Session) -> None:
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    _mk_event(db, "站会", now)
    calls: list[tuple[str, str]] = []

    def fake(title: str, body: str) -> dict:
        calls.append((title, body))
        return {"ok": True, "channel": "log"}

    r1 = run_reminder_tick(db, notify_fn=fake, now=now, lead_minutes=0)
    r2 = run_reminder_tick(db, notify_fn=fake, now=now, lead_minutes=0)
    assert len(r1) == 1 and r1[0]["ok"] is True
    assert len(calls) == 1
    assert len(r2) == 0

    logs = list_reminder_logs(db)
    assert len(logs) == 1
    assert logs[0]["title"] == "站会"
    assert logs[0]["ok"] is True

    # 模拟进程重启：清空内存去重，仅靠 DB 日志去重
    _lock_state["notified"] = {}
    r3 = run_reminder_tick(db, notify_fn=fake, now=now, lead_minutes=0)
    assert len(r3) == 0
    assert len(calls) == 1


def test_tick_failed_not_marked_db_dedup(db: Session) -> None:
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    _mk_event(db, "坏条目", now)

    def boom(title: str, body: str) -> dict:
        raise RuntimeError("bridge down")

    def ok(title: str, body: str) -> dict:
        return {"ok": True, "channel": "log"}

    r1 = run_reminder_tick(db, notify_fn=boom, now=now, lead_minutes=0)
    assert r1[0]["ok"] is False
    _lock_state["notified"] = {}
    r2 = run_reminder_tick(db, notify_fn=ok, now=now, lead_minutes=0)
    assert len(r2) == 1 and r2[0]["ok"] is True


def test_n2_quiet_hours_suppress_with_reason(db: Session) -> None:
    """N2：静默时段不发送，必须带 suppress_reason 落账，且不 mark_notified。"""
    from datetime import time as dtime

    from modules.calendar.notify_policy import NotifyPolicy, QuietHours

    # 23:30 UTC+0 对应上海 07:30 边界外——直接用上海 23:30 的 UTC 时刻
    # QuietHours 默认 23:00–07:30（按传入 now 的本地钟面判断；evaluate 用 now.time()）。
    now = datetime(2026, 9, 19, 15, 30, tzinfo=UTC)  # UTC 15:30 = 上海 23:30
    _mk_event(db, "夜深", now)
    calls: list[tuple[str, str]] = []

    def fake(title: str, body: str) -> dict:
        calls.append((title, body))
        return {"ok": True, "channel": "log"}

    policy = NotifyPolicy(quiet=QuietHours(start=dtime(23, 0), end=dtime(7, 30)))
    # ★ 2026-09-26 更新（astrbot）：静默判断已统一换算 Asia/Shanghai（原先用 now.time()
    #   直接比钟面，而调用方传 UTC → 白天误判深夜）。now = UTC 15:30 = 上海 23:30，
    #   故用**默认静默窗（23:00–07:30）**即可命中，无需再"盖住 UTC 钟面"。
    policy = NotifyPolicy(quiet=QuietHours(start=dtime(23, 0), end=dtime(7, 30)))
    r = run_reminder_tick(db, notify_fn=fake, now=now, lead_minutes=0, policy=policy)
    assert len(r) == 1
    assert r[0]["ok"] is False
    assert r[0]["suppress_reason"] == "quiet_hours"
    assert calls == []
    logs = list_reminder_logs(db)
    assert any(x["detail"] and "n2_suppress:quiet_hours" in x["detail"] for x in logs)
    # 未 mark → 策略放行后可重试
    from modules.calendar.reminder_scheduler import _lock_state

    assert str(r[0]["event_id"]) not in _lock_state["notified"]


def test_n2_rate_limit_suppress(db: Session) -> None:
    """N2：超过小时频控则 suppress_reason=rate_limited。"""
    from modules.calendar.notify_policy import NotifyPolicy, RateLimit

    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    _mk_event(db, "一", now)
    _mk_event(db, "二", now)
    calls: list[str] = []

    def fake(title: str, body: str) -> dict:
        calls.append(title)
        return {"ok": True, "channel": "log"}

    policy = NotifyPolicy(rate=RateLimit(max_per_hour=1, max_per_day=1))
    r = run_reminder_tick(db, notify_fn=fake, now=now, lead_minutes=0, policy=policy)
    assert len(r) == 2
    sent = [x for x in r if x.get("ok") is True]
    skipped = [x for x in r if x.get("suppress_reason") == "rate_limited"]
    assert len(sent) == 1
    assert len(skipped) == 1
    assert len(calls) == 1


def test_scheduler_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_scheduler()
    monkeypatch.delenv("CALENDAR_REMINDER_ENABLED", raising=False)
    assert start_scheduler() is False
    st = scheduler_status()
    assert st["enabled"] is False
    assert st["running"] is False
    assert st["log_table"] == "calendar_reminder_log"
