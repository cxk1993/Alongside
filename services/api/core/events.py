"""进程内事件总线 + SSE 订阅。

## 事件信封（Event Envelope）

★ 事件格式固定：
  `{ id, eid, topic, version, at, source, trace_id, correlation_id, payload }`

| 字段 | 含义 | 为什么有它 |
|---|---|---|
| `id` | **进程内递增序号**（int） | SSE 断线续传（`Last-Event-ID`）用它——续传语义要求单调可比较 |
| `eid` | 事件 UUID（str） | **跨进程唯一**的标识。`id` 只在本进程内有效，重启归零 |
| `topic` | 主题，`<域>.<实体>.<过去式>` | 路由键 |
| `version` | 主题版本（str） | 破坏性变更时 bump；minor 只加字段 |
| `at` | 发生时间（ISO8601 UTC） | |
| `source` | 发布者插件 id | 「谁发的」 |
| `trace_id` | 链路 id | 排障：一次请求引发的多个事件串成一条链 |
| `correlation_id` | 关联 id | 把「一件事的多个产物」串起来（如「装了包 → 成员 A 激活 → 成员 B 激活」） |
| `payload` | 业务数据 | |

★ **与 CloudEvents 1.0 的关系**（不是照抄，是**对齐**）：
  CloudEvents 的 REQUIRED 属性是 `id` / `source` / `specversion` / `type`。
  我们的映射：`topic`↔`type`、`source`↔`source`、`payload`↔`data`、
  `id`↔`id`，另加两个扩展（`eid` / `correlation_id`）。
  之所以不把 `id` 直接换成 UUID：SSE 的 `Last-Event-ID` 走的是**整数比较**，
  换成字符串会破坏断线续传。⇒ 两个 id 各司其职，不合并。

★ 为什么信封要**在总线这一层**统一，而不是让每个发布者自己拼：
  统一后，订阅方拿到的每条事件形状都相同；
  不统一的话，「字段增删不会在启动期暴露」这个问题会一直存在。

## 声明是订阅的唯一入口

★ `manifest.consumes` **必须**是订阅的唯一入口。
  早先订阅是插件直接 `event_bus.add_listener(handler)`，
  声明完全不参与——于是出现过「插件实际监听了 3 个主题、manifest 只声明 1 个」。
  ⇒ **声明 ↔ 实际**可能不一致，而不一致**没有任何报错**。

⇒ 现在的规矩：
  ① 插件**不许**直接 `add_listener` 消费别人的事件；
  ② 必须走 `event_bus.subscribe_declared(plugin_id, handler, topics=manifest.consumes)`：
     内核按该插件 manifest 的 `consumes` 过滤，**未声明的 topic 一律不投递**。
  ③ 于是「声明 ↔ 实际」的不一致在**结构上不可能发生**——
     这比写一道事后对账门禁更彻底。

★ 为什么 topics 由调用方传入而不由内核去读 manifest：
  内核不该为了「读一份插件的声明」而认识插件的目录结构；
  而插件自己知道自己的 manifest。
  ⇒ 传进来是**单向依赖**（插件 → 内核），内核不反向依赖插件。

## 投递保证

★ 进程内总线是 **at-most-once**：发布即投，不重试、不持久化。
  订阅者崩了，事件就没了。
  ⇒ 这是**已知边界**，写在这里而不是假装它可靠。
  需要可靠投递的场景（跨进程、崩溃恢复）留给「outbox + dispatcher」，
  本期不实现——但边界必须先写清，免得有人以为它可靠。

## 其它

- publish：同步、线程安全（跨线程用 call_soon_threadsafe 通知订阅者）。
- subscribe：返回 (queue, 历史快照)，供 SSE 端点做断线重连（Last-Event-ID）。
- 历史保留最近 100 条（断线续传上限）。
- ★ 单进程内总线，不引入 Redis/Celery（总纲 §1.1 单用户场景足够）。
"""
from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from core.logging import get_logger, trace_id_var

log = get_logger("kernel.events")

_BUS_MAXLEN = 100

#: 主题的当前版本。破坏性变更时 bump。
DEFAULT_TOPIC_VERSION = "1"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class EventBus:
    def __init__(self, maxlen: int = _BUS_MAXLEN) -> None:
        self._history: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        # ★ 声明式订阅：插件 id → (允许的 topic 集合, 处理器)
        self._declared: dict[str, tuple[frozenset[str], Callable[[dict[str, Any]], None]]] = {}
        self._seq = 0
        self._seq_lock = __import__("threading").Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def _next_id(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def publish(
        self,
        topic: str,
        payload: Any,
        source: str = "kernel",
        *,
        version: str = DEFAULT_TOPIC_VERSION,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """发布事件（同步、线程安全）。返回事件对象。

        ★ `trace_id` 从 `trace_id_var` 取（中间件已设好），
          没有请求上下文时为空串——**不编造**。
          编一个假 trace_id 会让排障时去查一条不存在的链路。
        """
        event = {
            "id": self._next_id(),
            "eid": uuid.uuid4().hex,
            "topic": topic,
            "version": version,
            "at": _now_iso(),
            "source": source,
            "trace_id": trace_id_var.get() or "",
            "correlation_id": correlation_id or "",
            "payload": payload,
        }
        self._history.append(event)
        # 进程内回调监听（激活器挂点）：同步调用，单回调异常不影响广播。
        # publish 可能来自任意线程，回调必须自行保证线程安全。
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "事件回调监听器异常（已忽略，不影响广播）",
                    extra={"topic": topic, "error": str(exc)},
                )
        # ★ 声明式订阅：只投给**声明过**该 topic 的插件。
        #   未声明的 topic 一律不投——这是「声明是唯一入口」的执行点。
        for plugin_id, (topics, handler) in list(self._declared.items()):
            if not _topic_matches(topics, topic):
                continue
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "声明式订阅者异常（已忽略，不影响广播）",
                    extra={"topic": topic, "plugin": plugin_id, "error": str(exc)},
                )
        if self._subscribers and self._loop is not None:
            for q in list(self._subscribers):
                self._loop.call_soon_threadsafe(q.put_nowait, event)
        return event

    def add_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """注册回调监听器（重复注册同一回调无副作用地被去重）。

        ★ 这是**内核自己**用的挂点（激活器等），**不是**插件消费事件的方式。
          插件消费事件必须走 `subscribe_declared`。
        """
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(callback)

    def subscribe_declared(
        self,
        plugin_id: str,
        handler: Callable[[dict[str, Any]], None],
        topics: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> None:
        """★ 插件订阅事件的**唯一入口**。

        `topics`：该插件 manifest 的 `consumes`（由调用方从 manifest 读出传入）。
        为 None 时**拒绝注册**——「没声明就不许订阅」是这条纪律的关键。

        ★ 为什么 topics 由调用方传入而不由内核去读 manifest：
          内核不该为了「读一份插件的声明」而认识插件的目录结构；
          而插件自己知道自己的 manifest。
          ⇒ 传进来是**单向依赖**（插件 → 内核），内核不反向依赖插件。
        """
        if topics is None:
            raise ValueError(
                f"插件 {plugin_id!r} 订阅事件时必须给出 manifest.consumes 声明——"
                "没声明就不许订阅（否则「声明 ↔ 实际」会不一致，而不一致没有报错）。"
            )
        allowed = frozenset(str(t) for t in topics)
        if not allowed:
            # 声明为空 = 不订阅任何事件。注册一个空集合的处理器是合法的
            # （插件可能后来才填声明），但**不投任何东西**。
            log.debug("插件 %s 声明的 consumes 为空，不订阅任何事件。", plugin_id)
        self._declared[plugin_id] = (allowed, handler)

    def unsubscribe_declared(self, plugin_id: str) -> None:
        """撤销某插件的声明式订阅（插件停用时用）。"""
        self._declared.pop(plugin_id, None)

    def declared_topics(self, plugin_id: str) -> frozenset[str]:
        """某插件当前声明订阅的 topic（供诊断与测试）。"""
        entry = self._declared.get(plugin_id)
        return entry[0] if entry else frozenset()

    def subscribe(self) -> tuple[asyncio.Queue[dict[str, Any]], list[dict[str, Any]]]:
        """注册订阅者（必须在事件循环内调用）。"""
        self._loop = asyncio.get_running_loop()
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(q)
        return q, list(self._history)

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        with contextlib.suppress(ValueError):
            self._subscribers.remove(q)

    def sse_response(
        self, request: Request, patterns: list[str] | None = None
    ) -> StreamingResponse:
        """构造 SSE 响应：先看历史补发（Last-Event-ID 续传），再实时推送。"""
        q, history = self.subscribe()
        last = int(request.headers.get("Last-Event-ID") or 0)

        async def generator() -> AsyncIterator[str]:
            try:
                for ev in history:
                    if ev["id"] > last and _match(ev["topic"], patterns or []):
                        yield _format_sse(ev)
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=30)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if _match(ev["topic"], patterns or []):
                        yield _format_sse(ev)
            finally:
                self.unsubscribe(q)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )


def _topic_matches(topics: frozenset[str], topic: str) -> bool:
    """topic 是否落在声明集合内。

    ★ 支持 `fnmatch` 通配（`todo.*`）——声明可以写「我要所有 todo 事件」。
      精确匹配优先，但通配让「一个插件关心一个域的所有事件」不必逐条列。
    """
    if topic in topics:
        return True
    return any(fnmatch.fnmatch(topic, p) for p in topics)


def _match(topic: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    return any(fnmatch.fnmatch(topic, p) for p in patterns)


def _format_sse(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, default=str)
    return f"id: {event['id']}\nevent: {event['topic']}\ndata: {data}\n\n"


# 全局单例
event_bus = EventBus()
