"""ISSUE-007 · todo 消费 health.care.requested（ADR-0002：只订阅事件，不 import health）。

★ 内核 event_bus 只有 SSE subscribe，无主题回调 —— 本模块在 **todo 侧**包装
  `event_bus.publish`（不改 core/events.py 源文件），命中目标 topic 后建跟进待办。
★ 幂等：`TodoItem.source_path = "health:{idempotency_key}"`，同 key 不重复创建。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from core.deps import kernel_session  # ★ 非 Depends 场景必须 kernel_session（get_db 已生成器化）
from core.events import event_bus

from .models import TodoItem, tags_to_json
from .service import date_to_utc

log = logging.getLogger("todo.health_link")

EVENT_CARE = "health.care.requested"
SH_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_DUE_DAYS = 3
_installed = False


def source_path_for(idempotency_key: str) -> str:
    return f"health:{idempotency_key}"


def _parse_due(suggest_due: str | None) -> datetime:
    if suggest_due:
        try:
            d = date.fromisoformat(str(suggest_due)[:10])
            return date_to_utc(d)
        except ValueError:
            pass
    local = datetime.now(SH_TZ).date() + timedelta(days=DEFAULT_DUE_DAYS)
    return date_to_utc(local)


def find_existing(db: Session, idempotency_key: str) -> TodoItem | None:
    sp = source_path_for(idempotency_key)
    return db.exec(select(TodoItem).where(col(TodoItem.source_path) == sp)).first()


def create_followup_todo(db: Session, payload: dict[str, Any]) -> dict[str, Any] | None:
    """根据 health 事件 payload 创建跟进待办；幂等则跳过返回 None。"""
    key = str(payload.get("idempotency_key") or "").strip()
    if not key:
        log.warning("health.care.requested 缺 idempotency_key，忽略 payload=%s", payload)
        return None
    if find_existing(db, key):
        log.info("健康跟进已存在，幂等跳过 key=%s", key)
        return None
    title_src = str(payload.get("title") or "健康跟进").strip()[:200]
    text = f"跟进：{title_src}" if not title_src.startswith("跟进：") else title_src
    due = _parse_due(payload.get("suggest_due"))
    item = TodoItem(
        text=text,
        done=False,
        due_at=due,
        tags=tags_to_json(["health"]),
        source_path=source_path_for(key),
    )
    item.series_id = item.id
    db.add(item)
    db.commit()
    db.refresh(item)
    event_bus.publish("todo.item.created", {"id": str(item.id), "text": item.text}, source="todo")
    log.info("健康跟进待办已创建 id=%s key=%s", item.id, key)
    return {
        "id": str(item.id),
        "text": item.text,
        "source_path": item.source_path,
        "idempotency_key": key,
    }


def handle_care_event(event: dict[str, Any]) -> None:
    if not isinstance(event, dict):
        return
    if event.get("topic") != EVENT_CARE:
        return
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return
    # ★ 根因补刀：get_db 生成器化后，非依赖场景必须 kernel_session()。
    with kernel_session() as db:
        try:
            create_followup_todo(db, payload)
        except Exception as exc:  # noqa: BLE001 — 消费失败不打断 publish
            log.warning("todo 处理 health.care.requested 失败: %s", exc)


def install_health_link() -> bool:
    """包装 event_bus.publish；幂等安装。不修改 core/events.py 文件。"""
    global _installed
    if _installed or getattr(event_bus, "_todo_health_link", False):
        _installed = True
        return False
    original = getattr(event_bus, "_todo_health_link_orig", None) or event_bus.publish

    def publish_hook(topic: str, payload: Any, source: str = "kernel") -> dict[str, Any]:
        event = original(topic, payload, source=source)
        if topic == EVENT_CARE:
            try:
                handle_care_event(event)
            except Exception as exc:  # noqa: BLE001
                log.warning("health_link hook 异常: %s", exc)
        return event

    event_bus._todo_health_link_orig = original  # type: ignore[attr-defined]
    event_bus.publish = publish_hook  # type: ignore[method-assign]
    event_bus._todo_health_link = True  # type: ignore[attr-defined]
    _installed = True
    log.info("todo health_link 已安装（订阅 %s）", EVENT_CARE)
    return True


def uninstall_health_link_for_tests() -> None:
    """仅测试用：恢复 publish（若曾安装）。"""
    global _installed
    original = getattr(event_bus, "_todo_health_link_orig", None)
    if original is not None:
        event_bus.publish = original  # type: ignore[method-assign]
    if hasattr(event_bus, "_todo_health_link"):
        delattr(event_bus, "_todo_health_link")
    _installed = False


install_health_link()
