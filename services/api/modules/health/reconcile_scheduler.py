"""E3 · health 期望态 reconcile 调度（默认关）。

★ HEAL_RECONCILE_ENABLED=true 时进程内周期广播期望态。
★ 默认间隔 30 分钟；可用 HEALTH_RECONCILE_INTERVAL_MINUTES 覆盖。
★ 测试环境保持关闭。
"""
from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from core.config import read_setting
from core.deps import kernel_session  # ★ 非 Depends 场景必须 kernel_session（get_db 已生成器化）

log = logging.getLogger("health.reconcile_scheduler")

_scheduler: Any | None = None


def _enabled() -> bool:
    return (read_setting("HEALTH_RECONCILE_ENABLED", "false") or "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _interval_minutes() -> int:
    raw = read_setting("HEALTH_RECONCILE_INTERVAL_MINUTES", "") or ""
    try:
        n = int(str(raw).strip())
        return n if n >= 1 else 30
    except ValueError:
        return 30


def _run_once() -> None:
    from .reconcile import reconcile_followups

    # ★ 补刀：get_db 已生成器化，非 Depends 场景必须 kernel_session()（内置模块属系统的一部分；第三方插件才用 db_session(id)）。
    try:
        with kernel_session() as db:
            reconcile_followups(db)
    except Exception as exc:  # noqa: BLE001
        log.warning("health reconcile tick 失败: %s", exc)


def start_scheduler() -> bool:
    global _scheduler
    if not _enabled():
        log.info("health reconcile 未启用（HEALTH_RECONCILE_ENABLED!=true）")
        return False
    if _scheduler is not None:
        return True
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        log.warning("apscheduler 不可用，health reconcile 调度未启动")
        return False
    minutes = _interval_minutes()
    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    sched.add_job(
        _run_once,
        trigger=IntervalTrigger(minutes=minutes),
        id="health_reconcile",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    _scheduler = sched
    log.info("health reconcile 调度已启动 interval=%smin", minutes)
    return True


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    with suppress(Exception):
        _scheduler.shutdown(wait=False)
    _scheduler = None


def scheduler_status() -> dict[str, Any]:
    return {
        "enabled": _enabled(),
        "running": _scheduler is not None,
        "interval_minutes": _interval_minutes(),
        "env": "HEALTH_RECONCILE_ENABLED",
    }
