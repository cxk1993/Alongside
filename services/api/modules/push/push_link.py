"""事件联动：calendar.reminder.fired → Web Push 广播（浏览器侧推送通道）。

★ ISSUE-007/health_link 同款模式：包装 event_bus.publish，不改 core/events.py；
  不 import calendar 插件本体，只消费事件 payload（ADR-0002 硬规则）。
★ 桌面通知走桥（T25 已投递 Windows toast），本联动补的是「浏览器/PWA 窗口」通道，
  两者并行——手机 Chrome、主人没开桌面时也能收到。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.deps import kernel_session
from core.events import event_bus

from .sender import send_broadcast

log = logging.getLogger("push.link")

EVENT_REMINDER = "calendar.reminder.fired"
# ★ 主人 2026-09-25「做」：待办到期 → web push（与日程提醒同构）
EVENT_TODO_DUE = "todo.item.due"
# ★ 主人 2026-09-27「课程表保留推送能力」：上课前提醒 → web push（同构第三路）
EVENT_COURSE_DUE = "course.session.due"
_installed = False

#: 本插件自己的声明。订阅面**只**从这份 manifest 来——那是「声明即订阅」的唯一真相源。
_MANIFEST_PATH = Path(__file__).resolve().with_name("manifest.json")


def handle_course_due_event(event: dict[str, Any]) -> None:
    """即将上课 → web push（由 course/remind_scheduler 发布 `course.session.due`）。"""
    if not isinstance(event, dict) or event.get("topic") != EVENT_COURSE_DUE:
        return
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return
    title = f"上课提醒：{payload.get('title') or ''}".strip()
    parts: list[str] = []
    if payload.get("start_time"):
        parts.append(f"{payload['start_time']} 上课")
    if payload.get("location"):
        parts.append(str(payload["location"]))
    body = " · ".join(parts)
    with kernel_session() as db:
        try:
            out = send_broadcast(
                db,
                title=title,
                body=body,
                url="/",
                tag=f"course-{payload.get('item_id') or ''}",
                topic=EVENT_COURSE_DUE,
            )
            log.info("course due → web push", extra={"sent": out.get("sent")})
        except Exception as exc:  # noqa: BLE001 — 联动失败不打断事件广播
            log.warning("course due push 联动失败: %s", exc)


def handle_todo_due_event(event: dict[str, Any]) -> None:
    """待办到期 → web push（由 todo/due_scheduler 发布 `todo.item.due`）。"""
    if not isinstance(event, dict) or event.get("topic") != EVENT_TODO_DUE:
        return
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return
    title = f"待办提醒：{payload.get('title') or ''}".strip()
    due_s = str(payload.get("due_at") or "")
    prio = str(payload.get("priority") or "")
    body = f"截止：{due_s}" if due_s else ""
    if prio == "high":
        body = ("【高优先】" + body).strip()
    with kernel_session() as db:
        try:
            out = send_broadcast(
                db,
                title=title,
                body=body,
                url="/",
                tag=f"todo-{payload.get('item_id') or ''}",
                topic=EVENT_TODO_DUE,
            )
            log.info("todo due → web push", extra={"sent": out.get("sent")})
        except Exception as exc:  # noqa: BLE001 — 联动失败不打断事件广播
            log.warning("todo due push 联动失败: %s", exc)


def handle_reminder_event(event: dict[str, Any]) -> None:
    if not isinstance(event, dict) or event.get("topic") != EVENT_REMINDER:
        return
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return
    title = f"日程提醒：{payload.get('title') or ''}".strip()
    start_s = str(payload.get("start_at") or "")
    # ★ 根因补刀（hermes 亲修）：get_db 已生成器化（仅供 Depends），
    #   非依赖场景直调它拿到的是 generator —— 必须用 kernel_session()。
    with kernel_session() as db:
        try:
            out = send_broadcast(
                db, title=title, body=f"开始：{start_s}" if start_s else "",
                url="/", tag=f"reminder-{payload.get('event_id') or ''}", topic=EVENT_REMINDER,
            )
            log.info(
                "reminder → web push",
                extra={"sent": out.get("sent"), "pruned": out.get("pruned")},
            )
        except Exception as exc:  # noqa: BLE001 — 联动失败不打断事件广播
            log.warning("reminder push 联动失败: %s", exc)


def _own_manifest() -> dict[str, Any]:
    """读本插件自己的 manifest（订阅声明的唯一来源）。

    ★ 插件自己知道自己的 manifest，内核不替它读（单向依赖：插件 → 内核）。
      这份 `consumes` 会被 `subscribe_declared` 用来做覆盖校验：
      某一个处理器要的 topic 不在声明里，注册当场被拒。
    """
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def install_push_link() -> bool:
    """按 manifest 声明注册事件联动（幂等）。

    ★ 三条订阅都必须出现在 `modules/push/manifest.json` 的 `consumes` 里。
      少一条，`subscribe_declared` 会在导入期抛 `UndeclaredSubscription`
      （fail-closed）——这是「声明即订阅」的执行点，不允许绕过。
    """
    global _installed
    if _installed:
        return False
    manifest = _own_manifest()
    event_bus.subscribe_declared(manifest, handle_reminder_event, topics={EVENT_REMINDER})
    # ★ 主人 09-25：待办到期通道
    event_bus.subscribe_declared(manifest, handle_todo_due_event, topics={EVENT_TODO_DUE})
    # ★ 主人 09-27：上课提醒通道
    event_bus.subscribe_declared(manifest, handle_course_due_event, topics={EVENT_COURSE_DUE})
    _installed = True
    log.info(
        "push 事件联动已装载（%s / %s / %s → web push，均经 manifest.consumes 授权）",
        EVENT_REMINDER,
        EVENT_TODO_DUE,
        EVENT_COURSE_DUE,
    )
    return True


# router.py 被内核挂载时即装载（health_link 由 todo/router import 触发，同款时序）。
install_push_link()
