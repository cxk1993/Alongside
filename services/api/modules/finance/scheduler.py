"""finance 定时只读同步（A2 / 终裁归 MiMo）。

★ 只包装既有 `sync_snapshot()`，不碰金额映射与净资产（BeeCount 冻结项）。
★ 默认关闭：需 `FINANCE_SYNC_ENABLED=true` 才在 API 进程内启动。
★ 调度：默认每日本地 03:10；可用 `FINANCE_SYNC_CRON="10 3 * * *"` 或
  `FINANCE_SYNC_INTERVAL_HOURS=6` 覆盖。测试环境保持关闭，避免后台线程搅库。
"""
from __future__ import annotations

import logging
import threading
from contextlib import suppress
from typing import Any

from core.config import read_setting

log = logging.getLogger("finance.scheduler")

_lock = threading.Lock()
_scheduler: Any | None = None
_last_run: dict[str, Any] | None = None


def _enabled() -> bool:
    raw = (read_setting("FINANCE_SYNC_ENABLED", "false") or "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _run_sync_job() -> None:
    """后台任务：建短会话 → 只读同步当日快照。异常只记日志，不打断进程。"""
    global _last_run
    import time

    from core.deps import kernel_session  # ★ 非 Depends 场景必须 kernel_session

    from .beecount_sync import sync_snapshot

    started = int(time.time())
    try:
        with kernel_session() as db:
            out = sync_snapshot(db)
        snap = getattr(out, "snapshot", None)
        date = getattr(snap, "date", None) if snap is not None else None
        _last_run = {
            "ts": started,
            "ok": True,
            "upstream": getattr(out, "upstream", None),
            "snapshot_date": str(date) if date is not None else None,
        }
        log.info("finance 定时同步完成", extra=_last_run)
    except Exception as exc:  # noqa: BLE001 — 后台任务不崩进程
        _last_run = {"ts": started, "ok": False, "error": str(exc)[:200]}
        log.warning("finance 定时同步失败: %s", exc)


def start_scheduler() -> bool:
    """按配置启动 BackgroundScheduler；已启动或未启用时返回 False。"""
    global _scheduler
    if not _enabled():
        log.info("finance 定时同步未启用（FINANCE_SYNC_ENABLED!=true）")
        return False
    with _lock:
        if _scheduler is not None:
            return False
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        sched = BackgroundScheduler(timezone="Asia/Shanghai")
        interval_h = read_setting("FINANCE_SYNC_INTERVAL_HOURS")
        cron = read_setting("FINANCE_SYNC_CRON")
        if interval_h:
            try:
                hours = max(1, int(interval_h))
            except ValueError:
                hours = 6
            sched.add_job(
                _run_sync_job,
                IntervalTrigger(hours=hours),
                id="finance_snapshot_sync",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            trigger_desc = f"interval={hours}h"
        else:
            expr = (cron or "10 3 * * *").strip()
            sched.add_job(
                _run_sync_job,
                CronTrigger.from_crontab(expr, timezone="Asia/Shanghai"),
                id="finance_snapshot_sync",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            trigger_desc = f"cron={expr}"
        sched.start()
        _scheduler = sched
        log.info("finance 定时同步已启动", extra={"trigger": trigger_desc})
        return True


def stop_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is None:
            return
        with suppress(Exception):
            _scheduler.shutdown(wait=False)
        _scheduler = None


def scheduler_status() -> dict[str, Any]:
    return {
        "enabled": _enabled(),
        "running": _scheduler is not None,
        "last_run": _last_run,
    }
