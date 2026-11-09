"""· 待办到期提醒调度（复用 calendar 三段式范式，workbuddy 2026-09-25）。

★ 主人「做」：待办此前**从未接入推送**（`grep scheduler|push` 在 todo 模块 0 命中）。

链路（与 calendar 完全同构，ADR-0002 不跨模块 import 插件本体）：
    due_scheduler 扫到期
      → event_bus.publish("todo.item.due")
        → push.link 监听（push 模块）
          → web push（浏览器/PWA 通知）

设计要点：
- **env 开关**：`TODO_REMIND_ENABLED=true` 才起（与 calendar 同款纪律，默认关）。
  ★ 2026-09-26 修：三个开关改走 `read_setting`（原先裸 os.environ **读不到 .env** → 恒关）。
- **轮询**：默认 60s（env `TODO_REMIND_POLL_SECONDS`）。
- **提前量**：`TODO_REMIND_LEAD_MINUTES`（默认 30）—— 到期前 30 分钟即提醒（主人）。
- **窗口**：只提醒 **最近 DUE_WINDOW_SECONDS（默认 300s）内到期**的待办 ——
  这样**重启后不会补发一堆历史提醒**（内存幂等集合丢失也无害，天然规避重复）。
- **幂等**：进程内 `_notified` 集合（配合窗口 = 不会重复、不会轰炸）。
- **不打断**：任何异常只 warning，绝不影响主进程与其它调度。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from core.config import read_setting
from core.events import event_bus

from .models import TodoItem

log = logging.getLogger("todo.due")

EVENT_DUE = "todo.item.due"
DEFAULT_POLL_SECONDS = 60
DEFAULT_DUE_WINDOW_SECONDS = 300  # 只看 5 分钟内到期的（防重启补发历史）
DEFAULT_LEAD_MINUTES = 30  # ★ 2026-09-26：提前 30 分钟提醒（0 = 只在到期时提醒）
# ★ 2026-09-26（astrbot · 主人「学业页」）：**学业类待办用独立提前量**。
#   学业作业 deadline 通常需要提前一天准备，而日常待办提前 30 分钟足矣 ——
#   若统一改大，日常会被提前一天"吵醒"（故分开）。
DEFAULT_STUDY_LEAD_MINUTES = 1440  # 24 小时
STUDY_TAG_ROOT = "学业"  # 命中该标签（或其子标签 学业/高数 等）即视为学业项

_lock_state: dict[str, Any] = {"scheduler": None}
_notified: set[str] = set()


# ★ 2026-09-26 修（astrbot · 主人「全修」）：
#   原先三个开关都读裸 `os.environ` —— 而项目的约定是 `read_setting`（os.environ 优先，
#   缺失时回退解析项目根 .env）。生产把开关写在 .env 里，裸 os.environ **读不到** →
#   待办提醒恒为关闭。本处统一改为 read_setting（与 calendar/reminder_scheduler 同款）。
def _enabled() -> bool:
    raw = (read_setting("TODO_REMIND_ENABLED", "false") or "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _poll_seconds() -> int:
    try:
        raw = read_setting("TODO_REMIND_POLL_SECONDS", str(DEFAULT_POLL_SECONDS))
        return max(10, int(raw or DEFAULT_POLL_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_POLL_SECONDS


def _due_window_seconds() -> int:
    try:
        raw = read_setting("TODO_REMIND_WINDOW_SECONDS", str(DEFAULT_DUE_WINDOW_SECONDS))
        return max(60, int(raw or DEFAULT_DUE_WINDOW_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_DUE_WINDOW_SECONDS


# ★ 2026-09-26 新增（主人报障「触发前半小时不推送」）：
#   原语义只提醒「**已经到期**」的待办（due_at <= now），**不支持提前提醒**。
#   新增 TODO_REMIND_LEAD_MINUTES（默认 30）：提醒点 = due_at - lead，
#   于是「到期前 30 分钟」即进入提醒窗口 —— 与 calendar 的 lead_minutes 同款语义。
def _lead_minutes() -> int:
    try:
        raw = read_setting("TODO_REMIND_LEAD_MINUTES", str(DEFAULT_LEAD_MINUTES))
        return max(0, int(raw or DEFAULT_LEAD_MINUTES))
    except (TypeError, ValueError):
        return DEFAULT_LEAD_MINUTES


def _study_lead_minutes() -> int:
    """学业类提前量（env：TODO_REMIND_STUDY_LEAD_MINUTES，默认 1440 = 24h）。"""
    try:
        raw = read_setting("TODO_REMIND_STUDY_LEAD_MINUTES", str(DEFAULT_STUDY_LEAD_MINUTES))
        return max(0, int(raw or DEFAULT_STUDY_LEAD_MINUTES))
    except (TypeError, ValueError):
        return DEFAULT_STUDY_LEAD_MINUTES


def _is_study(item: TodoItem) -> bool:
    """是否学业项：tags 命中 `学业` 或其子标签（学业/高数 …）。"""
    from .models import tags_from_json

    tags = tags_from_json(item.tags)
    return any(t == STUDY_TAG_ROOT or t.startswith(STUDY_TAG_ROOT + "/") for t in tags)


def _lead_for(item: TodoItem) -> int:
    """该条目的提前量（分钟）：学业 → study lead；其它 → 常规 lead。"""
    return _study_lead_minutes() if _is_study(item) else _lead_minutes()


def collect_due_todos(db: Session, *, now: datetime | None = None) -> list[TodoItem]:
    """收集**刚进入提醒窗口**的待办。

    ★ 2026-09-26 改：提醒点 = `due_at - lead`（lead 默认 30 分钟）。
       窗口 = [now - window, now + lead]：
         - 上界 `now + lead`  → 覆盖「未来 lead 分钟内将到期」（提前提醒）
         - 下界 `now - window`→ 只兜「刚过期」的一小段，**防重启补发一堆历史**
       幂等：进程内 `_notified` 集合（配合窗口，既不重复也不轰炸）。
    """
    now = now or datetime.now(UTC)
    window_start = now - timedelta(seconds=_due_window_seconds())
    # ★ 用**两者中较大**的 lead 做 SQL 粗筛（学业 24h / 日常 30min），
    #   再逐条按"该条目的 lead"精判 —— 避免为两种提前量写两条查询。
    max_lead = max(_lead_minutes(), _study_lead_minutes())
    fire_until = now + timedelta(minutes=max_lead)
    rows = db.exec(
        select(TodoItem).where(
            TodoItem.done == False,  # noqa: E712 — SQLModel 表达式需 == False
            TodoItem.due_at.is_not(None),  # type: ignore[union-attr]
            TodoItem.due_at <= fire_until,  # type: ignore[operator]
            TodoItem.due_at >= window_start,  # type: ignore[operator]
        )
    ).all()
    due: list[TodoItem] = []
    for r in rows:
        if r.id in _notified:
            continue
        # ★ 精判：该条目的提醒点 = due_at - lead（学业用 24h，日常用 30min）
        fire_at = r.due_at - timedelta(minutes=_lead_for(r))
        if fire_at <= now:
            due.append(r)
    return due


def run_due_tick(db: Session) -> int:
    """一轮扫描：对到期待办发布 `todo.item.due` 事件。返回发布条数。"""
    due = collect_due_todos(db)
    sent = 0
    for item in due:
        try:
            # ★ 2026-09-26 修（astrbot · 主人「全修」）：
            #   原写法把 {"topic":…,"payload":…} 整个 dict 当作 topic 传入 ——
            #   而 EventBus.publish 的签名是 publish(topic, payload, source)，
            #   于是抛 TypeError("missing 1 required positional argument: 'payload'")，
            #   且被本处 except 吞成 warning → **静默失败**（开关开了也不推）。
            #   现按 calendar/reminder_scheduler 的同款姿势调用。
            event_bus.publish(
                EVENT_DUE,
                payload={
                    "item_id": item.id,
                    "title": item.text,
                    "due_at": item.due_at.isoformat() if item.due_at else "",
                    "priority": item.priority or "",
                },
                source="todo",
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001 — 单条失败不影响其它待办
            log.warning("publish todo.item.due failed: %s", exc)
        _notified.add(item.id)  # 无论发布成功与否都记账（防每轮重试轰炸）
    if sent:
        log.info("待办到期提醒已发布", extra={"count": sent})
    return sent


def start_scheduler() -> bool:
    """Interval 调度：默认关（`TODO_REMIND_ENABLED=true` 开启）。与 calendar 同模式。"""
    if not _enabled():
        log.info("待办提醒未启用（TODO_REMIND_ENABLED!=true）")
        return False
    if _lock_state.get("scheduler") is not None:
        return False

    from apscheduler.schedulers.background import (
        BackgroundScheduler,  # type: ignore[import-untyped]
    )

    def _job() -> None:
        # ★ 根因/纪律：非 Depends 场景必须用 kernel_session()（get_db 是生成器依赖）
        from core.deps import kernel_session

        try:
            with kernel_session() as db:
                run_due_tick(db)
        except Exception as exc:  # noqa: BLE001 — tick 失败不打断主进程
            log.warning("todo due tick failed: %s", exc)

    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    sched.add_job(_job, "interval", seconds=_poll_seconds(), id="todo_due_tick")
    sched.start()
    _lock_state["scheduler"] = sched
    log.info(
        "待办到期提醒调度已启动",
        extra={"poll_seconds": _poll_seconds(), "window_seconds": _due_window_seconds()},
    )
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
        "window_seconds": _due_window_seconds(),
        "lead_minutes": _lead_minutes(),   # ★ 2026-09-26：提前提醒分钟数
        "study_lead_minutes": _study_lead_minutes(),   # ★ 学业项独立提前量
        "notified_in_process": len(_notified),
    }
