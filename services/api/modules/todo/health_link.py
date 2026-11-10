"""ISSUE-007 · todo 消费 health.care.requested（ADR-0002：只订阅事件，不 import health）。

★ 内核 event_bus 的事件消费入口是 `subscribe_declared`（声明即订阅）。本模块在
  **todo 侧**按自己的 `manifest.consumes` 声明订阅 `health.care.requested`，
  命中后建跟进待办。不 import health 插件本体，也不替整条总线改行为。
★ 幂等：`TodoItem.source_path = "health:{idempotency_key}"`，同 key 不重复创建。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
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

#: 本模块自己的声明（订阅面**只**从这份 manifest 来 —— 声明即订阅的唯一真相源）。
_MANIFEST_PATH = Path(__file__).resolve().with_name("manifest.json")


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


def _own_manifest() -> dict[str, Any]:
    """读本模块自己的 manifest（订阅声明的唯一来源）。"""
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def install_health_link() -> bool:
    """按 manifest 声明订阅 `health.care.requested`（幂等）。

    ★ 2026-11-10 改造：原先这里是**猴补丁 `event_bus.publish`**（改实例方法），
      它有两处硬伤，且都是「静默失效」型：
      ① 包装函数的 `source` 缺省值是 `"kernel"` —— 任何忘了写 `source=` 的发布方
         都会被当成内核事件，**绕过 emits 声明校验**（fail-closed 形同虚设）；
      ② 包装函数不透传 `version` / `correlation_id` —— 内核的
         `publish_kernel_event()` 一调就 `TypeError`（实测已炸，见报告）。
      现在改走 `subscribe_declared`：订阅面来自 `manifest.consumes`，
      与其它事件消费者同一条路，不再替整条总线改行为。
    """
    global _installed
    if _installed:
        return False
    event_bus.subscribe_declared(_own_manifest(), handle_care_event, topics={EVENT_CARE})
    _installed = True
    log.info("todo health_link 已装配（经 manifest.consumes 订阅 %s）", EVENT_CARE)
    return True


def uninstall_health_link_for_tests() -> None:
    """仅测试用：摘掉本模块的订阅，恢复「未安装」状态。

    ★ 目前 todo 的声明式订阅只有这一条（consume `health.care.requested`），
      所以整插件退订是精确的。若以后 todo 增加其它事件订阅，这里需要
      `unsubscribe_declared(plugin_id, handler=...)` 的按处理器粒度。
    """
    global _installed
    event_bus.unsubscribe_declared("todo")
    _installed = False


install_health_link()
