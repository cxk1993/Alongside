"""事件总线加固回归（P1/P2）：loop 关闭、BaseException、async 处理器、大小写、重复注册。

这五条都是「跑起来看起来正常，出事时无声」的类型，所以各配一条可复跑的判据。

★ 判据一律用真实 `EventBus`，不 mock：
  总线是进程内单例语义最简单的组件，mock 它等于测 mock 自己。
"""
from __future__ import annotations

import asyncio
import gc
import weakref

import pytest

from core.events import EventBus, UndeclaredSubscription


def _manifest(*consumes: str) -> dict[str, object]:
    return {"id": "demo", "consumes": list(consumes)}


# ── P1 · SSE loop 已关闭 ────────────────────────────────────────────
def test_publish_does_not_raise_when_subscriber_loop_is_closed() -> None:
    """loop 关闭后 publish 不得把 RuntimeError 冒给业务调用方（副作用已发生）。

    真实形态：`asyncio.run(bus.subscribe())` 结束 → loop 关闭，
    而 `_subscribers` 还留着那个死 loop。此后任何一次业务 publish 都会抛
    `RuntimeError: Event loop is closed` —— 调用方拿到 500 会重试，
    业务就写了两遍（这是最坏的组合：非幂等 + 报错）。
    """
    bus = EventBus()

    async def _subscribe_once():
        return bus.subscribe()

    asyncio.run(_subscribe_once())  # 记下 loop，随后关闭
    assert bus._subscribers, "前置判据：订阅者应当在列表里"

    bus.publish_kernel_event("x.y", {})  # 不应抛
    assert bus._subscribers == [], "死订阅者应当被摘掉，而不是每次 publish 都炸一遍"


# ── P2 · BaseException 打断广播要留痕 ───────────────────────────────
def test_base_exception_aborts_broadcast_but_is_traced(caplog: pytest.LogCaptureFixture) -> None:
    bus = EventBus()
    seen: list[str] = []

    def killer(_e: dict) -> None:
        raise asyncio.CancelledError("任务被取消")

    def after(_e: dict) -> None:
        seen.append("after")

    bus.subscribe_declared(_manifest("x.y"), killer, topics={"x.y"})
    bus.subscribe_declared(_manifest("x.y"), after, topics={"x.y"})

    with pytest.raises(asyncio.CancelledError):
        bus.publish_kernel_event("x.y", {})
    assert seen == [], "取消信号保持原语义：后面的 handler 不再收到（不像普通异常那样继续）"
    assert any("BaseException" in r.message for r in caplog.records), (
        "必须留一条日志 —— 改造前这里一行痕迹都没有，排障时不知道谁吞了事件"
    )


# ── P2 · async def 处理器注册期就拒 ────────────────────────────────
def test_async_handler_is_rejected_at_registration() -> None:
    bus = EventBus()

    async def handler(_e: dict) -> None:  # pragma: no cover —— 不该被执行
        raise AssertionError("async 处理器永远不该被注册成功")

    with pytest.raises(TypeError, match="async def"):
        bus.subscribe_declared(_manifest("x.y"), handler, topics={"x.y"})


# ── P2 · 注册期与投递期必须是同一个匹配器（大小写敏感）──────────────
def test_topic_matching_is_case_sensitive_on_every_platform() -> None:
    """声明 `TODO.*` 不该命中 `todo.item.due`（Windows 上 fnmatch 会转小写）。

    这是跨平台漂移的典型：同一份插件包在 Windows 投递、在 Linux 不投递。
    """
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe_declared(_manifest("TODO.*"), lambda e: seen.append(e["topic"]), topics={"TODO.*"})
    bus.publish_kernel_event("todo.item.due", {})
    assert seen == [], "topic 是标识符，大小写必须敏感（与注册期 fnmatchcase 一致）"


# ── P1 · 同一个 handler 重复注册不重复投递 ──────────────────────────
def test_same_handler_with_extra_topics_is_merged_not_duplicated() -> None:
    bus = EventBus()
    calls: list[str] = []

    def handler(event: dict) -> None:
        calls.append(event["topic"])

    bus.subscribe_declared(_manifest("todo.*", "todo.item.due"), handler, topics={"todo.*"})
    bus.subscribe_declared(_manifest("todo.*", "todo.item.due"), handler, topics={"todo.item.due"})

    assert len(bus._declared["demo"]) == 1, "同一 handler 应合并成一条，而不是追加第二条"
    bus.publish_kernel_event("todo.item.due", {})
    assert calls == ["todo.item.due"], f"一条事件只能投递一次，实际 {calls}"


# ── P0-1 的补充判据：退订后对象可回收（总线不再强引用）────────────
def test_unsubscribe_releases_handler_reference() -> None:
    bus = EventBus()

    class Holder:
        def on_event(self, _e: dict) -> None:
            return None

    holder = Holder()
    ref = weakref.ref(holder)
    bus.subscribe_declared(_manifest("todo.*"), holder.on_event, topics={"todo.item.due"})
    bus.publish_kernel_event("todo.item.due", {})
    bus.unsubscribe_declared("demo")
    del holder
    gc.collect()
    assert ref() is None, "退订后总线不得再强引用插件对象（否则卸载的插件永远回收不掉）"
