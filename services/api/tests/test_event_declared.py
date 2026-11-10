"""声明式订阅（`manifest.consumes` 声明即订阅）的执行点单测。

★ 守两条纪律：
  ① 处理器要的 topic 不在 `manifest.consumes` 里 ⇒ 注册当场被拒（fail-closed）；
  ② 投递只发生在声明覆盖的 topic 上 —— 未声明的一律不投。
"""
from __future__ import annotations

import pytest

from core.events import EventBus, UndeclaredSubscription


def _manifest(*consumes: str) -> dict[str, object]:
    return {"id": "demo", "consumes": list(consumes)}


def test_undeclared_topic_is_rejected() -> None:
    bus = EventBus()
    with pytest.raises(UndeclaredSubscription):
        bus.subscribe_declared(
            _manifest("todo.item.due"), lambda _e: None, topics={"finance.entry.created"}
        )


def test_declared_wildcard_covers_concrete_topic() -> None:
    bus = EventBus()
    bus.subscribe_declared(_manifest("todo.*"), lambda _e: None, topics={"todo.item.due"})
    assert bus.declared_topics("demo") == frozenset({"todo.item.due"})


def test_handler_cannot_invent_wildcard() -> None:
    """声明只给了一个精确 topic 时，处理器不能拿 `todo.*` 去套。"""
    bus = EventBus()
    with pytest.raises(UndeclaredSubscription):
        bus.subscribe_declared(_manifest("todo.item.due"), lambda _e: None, topics={"todo.*"})


def test_only_declared_topics_are_delivered() -> None:
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe_declared(
        _manifest("todo.item.due"), lambda e: seen.append(e["topic"]), topics={"todo.item.due"}
    )
    bus.publish("finance.entry.created", {})
    bus.publish("todo.item.due", {"id": 1})
    assert seen == ["todo.item.due"]


def test_push_declares_every_topic_it_handles() -> None:
    """push 的三路联动都必须在自己的 manifest.consumes 里（漏一条导入期就炸）。"""
    from core.events import event_bus
    from modules.push import push_link

    push_link.install_push_link()  # 幂等：导入期通常已装载
    assert event_bus.declared_topics("push") == frozenset(
        {push_link.EVENT_REMINDER, push_link.EVENT_TODO_DUE, push_link.EVENT_COURSE_DUE}
    )
