"""声明式发布（`manifest.emits` 声明即许可）的执行点单测。

★ 与 `test_event_declared.py`（订阅端）对称，守同样两条纪律：
  ① 发布者没登记 / topic 不在声明里 ⇒ 当场被拒（fail-closed），**事件不发出去**；
  ② 内核自己的系统事件不受模块声明体系约束（`publish_kernel_event`）。

★ 为什么「发了没声明」必须炸而不是警告：
  发布端是契约的供给方。没声明的话题一旦发出去，订阅方要么收不到（契约漏了），
  要么靠通配偷听（契约被绕过）—— 两种都让事件契约退化成注释。
"""
from __future__ import annotations

import pytest

from core.events import (
    KERNEL_SOURCE,
    EventBus,
    UndeclaredPublication,
)


def _manifest(*emits: str, pid: str = "demo") -> dict[str, object]:
    return {"id": pid, "emits": list(emits)}


def test_unregistered_publisher_is_rejected() -> None:
    """从未登记 emits 的 source：内核不知道它会发什么 ⇒ 拒绝。"""
    bus = EventBus()
    with pytest.raises(UndeclaredPublication):
        bus.publish("todo.item.due", {}, source="ghost")


def test_registered_but_undeclared_topic_is_rejected() -> None:
    bus = EventBus()
    bus.declare_publisher(_manifest("demo.ping"))
    with pytest.raises(UndeclaredPublication):
        bus.publish("demo.pong", {}, source="demo")


def test_declared_topic_is_published() -> None:
    bus = EventBus()
    bus.declare_publisher(_manifest("demo.ping"))
    event = bus.publish("demo.ping", {"n": 1}, source="demo")
    assert event["topic"] == "demo.ping"
    assert event["source"] == "demo"


def test_declared_wildcard_covers_concrete_topic() -> None:
    bus = EventBus()
    bus.declare_publisher(_manifest("demo.*"))
    assert bus.publish("demo.ping", {}, source="demo")["topic"] == "demo.ping"


def test_rejected_publish_does_not_enter_history() -> None:
    """fail-closed 的语义是「不发出去」，不是「发了再报错」。"""
    bus = EventBus()
    bus.declare_publisher(_manifest("demo.ping"))
    with pytest.raises(UndeclaredPublication):
        bus.publish("demo.pong", {}, source="demo")
    assert [e["topic"] for e in bus._history] == []


def test_kernel_events_are_not_blocked() -> None:
    """内核不是插件：它发的事件不该被任何模块的 emits 声明拦住。"""
    bus = EventBus()
    bus.declare_publisher(_manifest("demo.ping"))
    assert bus.publish_kernel_event("kernel.ready", {})["source"] == KERNEL_SOURCE
    # 老调用形态（source 缺省）同样走内核源，保持向后兼容
    assert bus.publish("kernel.ready", {})["source"] == KERNEL_SOURCE


def test_empty_declaration_means_no_publication() -> None:
    bus = EventBus()
    bus.declare_publisher(_manifest())
    with pytest.raises(UndeclaredPublication):
        bus.publish("demo.ping", {}, source="demo")


def test_declared_emits_reads_all_three_info_shapes() -> None:
    """PluginInfo / Manifest 模型 / 原始 dict 三种形态都要能登记。"""
    from core.manifest import Manifest

    bus = EventBus()
    model = Manifest(
        id="demo",
        name="Demo",
        version="0.1.0",
        kind="builtin",
        api={"base": "/api/v1/demo"},
        emits=["demo.ping"],
    )
    assert bus.declare_publisher(model) == frozenset({"demo.ping"})
    assert bus.declared_emits("demo") == frozenset({"demo.ping"})
    # 未登记 ≠ 空声明
    assert bus.declared_emits("never-registered") is None


def test_real_modules_register_their_emits_on_startup(tmp_path) -> None:  # noqa: ANN001
    """create_app 之后，每个模块的 emits 必须都能从总线查到（发现即登记）。"""
    from core.app import create_app

    create_app()
    from core.events import event_bus

    for pid in ("agents", "todo", "calendar", "web", "auth"):
        declared = event_bus.declared_emits(pid)
        assert declared, f"模块 {pid} 的 emits 没进总线注册表"

    # web 的事件源必须是模块 id（不是 catalog 展示标签 web_entry）
    mentioned = event_bus.declared_emits("web")
    assert mentioned is not None and "web.entry.created" in mentioned
