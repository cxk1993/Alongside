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
  早先订阅是插件直接往总线上挂回调（旧的 `add_listener`），
  声明完全不参与——于是出现过「插件实际监听了 3 个主题、manifest 只声明 1 个」。
  ⇒ **声明 ↔ 实际**可能不一致，而不一致**没有任何报错**。

⇒ 现在的规矩：
  ① 插件**不许**直接挂总线回调；那个挂点已改名为 `add_kernel_hook`
     （名字就是纪律：只给内核自己用，如激活器）；
  ② 插件必须走 `event_bus.subscribe_declared(info, handler, topics=...)`：
     内核从 `info`（PluginInfo / Manifest / 原始 manifest）读 `consumes`，
     处理器要的每个 topic 都必须落在声明内，**否则当场拒绝注册**；
     投递时再按声明过滤，**未声明的 topic 一律不投递**。
  ③ 于是「声明 ↔ 实际」的不一致在**结构上不可能发生**——
     声明少了会在启动时炸（fail-closed），不会拖到线上才表现为「没收到事件」。

★ 为什么 info 由调用方传入而不由内核去读 manifest：
  内核不该为了「读一份插件的声明」而认识插件的目录结构；
  而插件自己知道自己的 manifest。
  ⇒ 传进来是**单向依赖**（插件 → 内核），内核不反向依赖插件。

## 声明也是发布的唯一入口（2026-11-10）

★ `manifest.emits` 与 `manifest.consumes` 是**同一套契约的两端**：
  订阅端「未声明不许订阅」，发布端「未声明不许发布」。
  早先只有订阅端有约束，而 emits 完全没人读 —— 于是出现过
  「9 个模块声明了却从不发布、6 个发布了却没声明」，
  事件契约只成立一半（写着的没人执行，执行着的没人写）。

⇒ 现在的规矩：
  ① 模块**只能**用 `publish(topic, payload, source=<模块 id>)` 发布；
  ② 发布前内核按 `source` 查该模块登记过的 `emits` 声明，
     未登记 / 未覆盖该 topic ⇒ 抛 `UndeclaredPublication`（fail-closed，**不发出去**）；
  ③ 声明由发现层调用 `declare_publisher(info)` 登记（见 `create_app` 与 `mount_plugin`）；
  ④ 内核自己的系统事件（SSE 补发、激活器观测等）走 `publish_kernel_event()`：
     它固定 `source="kernel"`，**不**受模块声明体系约束 ——
     内核不是插件，没有 manifest，也不该为了发一条事件去编一份声明。

★ 为什么发布端要 fail-closed 而不是「记个警告继续发」：
  发布端是**契约的供给方**。没人声明的话题被发出去，订阅端要么收不到（契约漏了），
  要么靠通配符偷听（契约被绕过）——两种都让「事件契约」退化成注释。
  在发布点炸，问题会出现在**写错的那一行**；只警告，问题会出现在三个月后的排障现场。

★ 为什么要静态门禁再扫一遍：运行时只拦得住「跑到的代码」。
  `scripts/check_protocol_consumption.py` 会把「声明了不发布 / 发布了没声明」
  在**不开服务**的情况下判出来（双向），并强制模块发布时必须显式写 `source=<自身 id>`。

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
import inspect
import json
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from core.logging import get_logger, trace_id_var

log = get_logger("kernel.events")

_BUS_MAXLEN = 100

#: 内核自身的**事件源**标识。
#:
#: ★ 这是一个受信值：`publish(source="kernel")` 跳过模块 emits 校验。
#:   所以**模块代码不许用它**（静态门禁会拦：模块里的发布必须显式写 source=<自身 id>）。
#:   内核要发系统事件，请走 `publish_kernel_event()` —— 名字即纪律，
#:   与 `add_kernel_hook` 同款思路：让越界写在 review 里一眼可见。
KERNEL_SOURCE = "kernel"

#: 主题的当前版本。破坏性变更时 bump。
DEFAULT_TOPIC_VERSION = "1"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class EventBus:
    def __init__(self, maxlen: int = _BUS_MAXLEN) -> None:
        self._history: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        # ★ 声明式订阅：插件 id → [(该处理器被允许的 topic 集合, 处理器), ...]
        #   一个插件可以有多个处理器（push 就同时挂提醒/待办/上课三路），
        #   但每一路都必须落在 manifest.consumes 声明之内。
        self._declared: dict[str, list[tuple[frozenset[str], Callable[[dict[str, Any]], None]]]] = {}
        # ★ 发布端声明：插件 id → manifest.emits 声明的 topic 集合。
        #   与 `_declared`（订阅端）对称：登记即声明，publish 时按它 fail-closed。
        #   由发现层在**发现时**登记（不是激活时）：注册表回答的是
        #   「这个模块声明了什么」，而「代码在不在跑」由激活表回答，两者不该混。
        self._declared_emits: dict[str, frozenset[str]] = {}
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
        source: str = KERNEL_SOURCE,
        *,
        version: str = DEFAULT_TOPIC_VERSION,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """发布事件（同步、线程安全）。返回事件对象。

        ★ `source` 必须写**发布者自己的模块 id**（模块代码里不许省略）。
          省略 = 走内核源，静态门禁会把它当越界。内核自己请用 `publish_kernel_event()`。

        ★ 模块发布者 fail-closed（2026-11-10）：`source` 不是内核时，
          先按该模块登记过的 `manifest.emits` 校验 topic；
          没登记 / 没覆盖 ⇒ 抛 `UndeclaredPublication`，**事件不会发出去**。
          与 `subscribe_declared` 是同一套语义的两端：
          订阅端「未声明不许订阅」，发布端「未声明不许发布」。

        ★ `trace_id` 从 `trace_id_var` 取（中间件已设好），
          没有请求上下文时为空串——**不编造**。
          编一个假 trace_id 会让排障时去查一条不存在的链路。
        """
        if source != KERNEL_SOURCE:
            self._assert_publish_declared(source, topic)
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
        # ★ 声明式订阅：只投给**声明过**该 topic 的处理器。
        #   未声明的 topic 一律不投——这是「声明是唯一入口」的执行点。
        for plugin_id, entries in list(self._declared.items()):
            for topics, handler in list(entries):
                if not _topic_matches(topics, topic):
                    continue
                try:
                    handler(event)
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "声明式订阅者异常（已忽略，不影响广播）",
                        extra={"topic": topic, "plugin": plugin_id, "error": str(exc)},
                    )
                except BaseException as exc:  # noqa: BLE001
                    # ★ `except Exception` 兜不住 SystemExit / KeyboardInterrupt /
                    #   asyncio.CancelledError（3.8+ 属 BaseException）。它们会**打断
                    #   整条广播**，而原来的写法连一行日志都不留 —— 后面几个插件
                    #   静默漏事件，排障时什么都看不到。
                    #   这里不上抛也不吞：先留痕，再按原语义继续上抛（取消信号必须
                    #   抵达调用方，这是控制流，不是错误）。
                    log.warning(
                        "声明式订阅者抛 BaseException，广播中止",
                        extra={
                            "topic": topic,
                            "plugin": plugin_id,
                            "type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                    raise
        if self._subscribers and self._loop is not None:
            for q in list(self._subscribers):
                try:
                    self._loop.call_soon_threadsafe(q.put_nowait, event)
                except RuntimeError as exc:
                    # ★ loop 已关闭（`asyncio.run` 结束、uvicorn worker 重载、
                    #   优雅退出窗口）。此时**业务副作用已经发生**（上面各 handler
                    #   都跑完了），如果让 RuntimeError 冒给 publish 的调用方，
                    #   调用方会当成失败重试 ⇒ 同一条业务写两遍。
                    #   ⇒ 订阅者已死，摘掉它，降级为 warn。
                    log.warning(
                        "SSE 订阅者的 loop 已关闭，已摘除该订阅者",
                        extra={"topic": topic, "error": str(exc)},
                    )
                    with contextlib.suppress(ValueError):
                        self._subscribers.remove(q)
        return event

    def publish_kernel_event(
        self,
        topic: str,
        payload: Any,
        *,
        version: str = DEFAULT_TOPIC_VERSION,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """发布**内核自身**的系统事件（不受任何模块的 emits 声明约束）。

        ★ 为什么要有独立入口，而不是让内核也写 `publish(source="kernel")`：
          两者行为一样，但名字回答了一个问题——「这条事件是谁发的」。
          内核发的事件不该看起来像某个模块发的（`source` 必须是 `kernel`），
          也不该为了合规去编一份 manifest。名字即纪律（同 `add_kernel_hook`）。
        """
        return self.publish(
            topic,
            payload,
            source=KERNEL_SOURCE,
            version=version,
            correlation_id=correlation_id,
        )

    # ── 发布端声明（emits 契约的执行点）────────────────────────────

    def declare_publisher(self, info: Any) -> frozenset[str]:
        """登记某插件的 `manifest.emits` 声明（发布端的唯一登记入口，幂等）。

        `info` 与 `subscribe_declared` 同形：`PluginInfo` / `Manifest` 模型 / 原始 manifest dict。

        ★ 重复登记同一份声明是幂等的（发现层与挂载入口都会调一次）。
          两次声明**不一致**时不抛错、只告警：那说明同一个 id 有两份 manifest，
          根因在发现层（它已经保证 id == 目录名），不该由总线替它裁决；
          但必须留下痕迹，否则「两份真相」会静默并存。
        """
        plugin_id, declared = _declaration_of(info, field="emits")
        existing = self._declared_emits.get(plugin_id)
        if existing is not None and existing != declared:
            log.warning(
                "插件 %s 的 emits 声明被重复登记且内容不同（后一份生效）",
                plugin_id,
                extra={"before": sorted(existing), "after": sorted(declared)},
            )
        self._declared_emits[plugin_id] = declared
        return declared

    def withdraw_publisher(self, plugin_id: str) -> None:
        """撤销某插件的 emits 声明（幂等）。仅测试与诊断用。"""
        self._declared_emits.pop(plugin_id, None)

    def declared_emits(self, plugin_id: str) -> frozenset[str] | None:
        """某插件登记的 emits 声明；`None` = 从未登记（≠ 空声明）。"""
        return self._declared_emits.get(plugin_id)

    def publishers(self) -> dict[str, frozenset[str]]:
        """当前全部发布端声明快照（诊断用）。"""
        return dict(self._declared_emits)

    def _assert_publish_declared(self, source: str, topic: str) -> None:
        """`source` 是否有权发布 `topic`（fail-closed）。"""
        declared = self._declared_emits.get(source)
        if declared is None:
            raise UndeclaredPublication(
                f"发布者 {source!r} 没有登记 emits 声明：内核不知道它会发什么事件。"
                "模块必须在 manifest.emits 里声明，并由发现层调用 declare_publisher 登记；"
                "内核自身的事件请用 publish_kernel_event()。"
            )
        if not _declared_covers(declared, topic):
            raise UndeclaredPublication(
                f"模块 {source!r} 试图发布未声明的 topic {topic!r}；"
                f"manifest.emits = {sorted(declared)}。"
                "声明是发布的唯一入口——要发布，先把 topic 写进 manifest.emits。"
            )

    def add_kernel_hook(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """注册**内核自身**的回调挂点（激活器等；重复注册同一回调会被去重）。

        ★ 名字里的 `kernel` 是纪律，不是装饰：插件消费事件**只能**走
          `subscribe_declared`——插件的订阅面必须等于 manifest.consumes 声明面。
          这里挂的回调**不受 consumes 过滤**（内核不是插件，没有 manifest）。
          早先它叫 `add_listener`，名字看不出来「谁可以调」，于是 push 模块
          直接用它绕过了声明。改名的目的就是让越界写在 code review 里一眼可见。
        """
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_kernel_hook(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(callback)

    def subscribe_declared(
        self,
        info: Any,
        handler: Callable[[dict[str, Any]], None],
        topics: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> frozenset[str]:
        """★ 插件订阅事件的**唯一入口**：声明即订阅，未声明即拒绝。

        `info`：插件的身份 + 声明。接受三种形态（都只读 `id` 与 `consumes`）：
          · `PluginInfo`（发现层产出，`info.manifest` 是原始 manifest dict）
          · `Manifest` 模型（`core/manifest.py`，`consumes` 是属性）
          · 原始 manifest dict（插件自己从目录里读出来的那份）

        `topics`：这个**处理器**要订阅的 topic。缺省 = 订阅声明里的全部 topic。
        每一路都必须落在 `manifest.consumes` 之内：声明写 `todo.*` 可以覆盖
        `todo.item.due`；但处理器**不能发明**声明里没有的 topic，也不能拿通配
        去套一份只写了精确 topic 的声明。

        ★ 不满足就**拒绝注册并抛错**（fail-closed）——与「未声明 topic 不投递」
          是同一套语义的两端：投递端静默丢弃，注册端当场炸。区别在于后者能让人
          在启动时就发现自己漏写了声明，而不是上线后「怎么没收到事件」。

        ★ 为什么 `info` 由调用方传入而不由内核去读 manifest：
          内核不该为了「读一份插件的声明」而认识插件的目录结构；
          而插件自己知道自己的 manifest。
          ⇒ 传进来是**单向依赖**（插件 → 内核），内核不反向依赖插件。
        """
        plugin_id, declared = _declaration_of(info)
        if not callable(handler):
            raise TypeError(f"插件 {plugin_id!r} 的事件处理器必须可调用，收到 {type(handler)!r}")
        if inspect.iscoroutinefunction(handler):
            # ★ 注册期就拒，而不是运行期静默丢弃：总线是**同步投递**，
            #   `async def` 处理器只会产出一个没人 await 的 coroutine
            #   （实测：注册成功、publish 不报错、handler 一次没跑，
            #     只在日志里留一句 "coroutine ... was never awaited"）。
            #   这正是 fail-closed 要消灭的失败模式 —— 启动时炸，别上线后静默。
            raise TypeError(
                f"插件 {plugin_id!r} 的事件处理器不能是 async def："
                "事件总线是同步投递，async 处理器会被静默丢弃（永不执行）。"
                "请在 handler 内部自行调度到事件循环（如 asyncio.run_coroutine_threadsafe）。"
            )
        wanted = frozenset(declared if topics is None else _as_topics(topics))

        undeclared = sorted(t for t in wanted if not _declared_covers(declared, t))
        if undeclared:
            raise UndeclaredSubscription(
                f"插件 {plugin_id!r} 试图订阅未声明的 topic：{undeclared}；"
                f"manifest.consumes = {sorted(declared)}。"
                "声明是订阅的唯一入口——要订阅，先把 topic 写进 manifest.consumes。"
            )
        if not wanted:
            # 声明为空 = 不订阅任何事件。注册一个空处理器没有意义，
            # 但也不该报错（插件可能还没声明要消费什么）。
            log.debug("插件 %s 的 consumes 为空，不注册任何事件订阅。", plugin_id)
            return wanted

        entries = self._declared.setdefault(plugin_id, [])
        # ★ 同一个 handler 对象重复注册（比如先声明 `todo.*` 后来又补一条
        #   `todo.item.due`，或模块重载前的那份引用）**合并 topic**，
        #   而不是追加第二条 —— 追加会让同一条事件投递两次（实测 S2b）。
        #   插件只表达了一次「我要处理这个函数」，就不该被调用两次。
        for idx, (topics_i, handler_i) in enumerate(entries):
            if handler_i == handler:
                entries[idx] = (topics_i | wanted, handler_i)
                return wanted
        entries.append((wanted, handler))
        return wanted

    def unsubscribe_declared(self, plugin_id: str) -> None:
        """撤销某插件的**全部**声明式订阅（插件停用时用，幂等）。"""
        self._declared.pop(plugin_id, None)

    def declared_topics(self, plugin_id: str) -> frozenset[str]:
        """某插件当前所有处理器声明订阅的 topic 并集（供诊断与测试）。"""
        entries = self._declared.get(plugin_id, [])
        return frozenset().union(*(topics for topics, _h in entries)) if entries else frozenset()

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

    ★ 必须用 `fnmatchcase`（大小写敏感）而不是 `fnmatch`：
      `fnmatch` 在 Windows 上会先 `os.path.normcase`（两边转小写），
      与注册期 `_declared_covers`（用 `fnmatchcase`）语义不一致 ——
      实测声明写 `TODO.*`、topic 是 `todo.item.due` 时，
      Windows 投递、Linux 不投递。topic 是标识符，本就该大小写敏感。
    """
    if topic in topics:
        return True
    return any(fnmatch.fnmatchcase(topic, p) for p in topics)


class UndeclaredSubscription(ValueError):
    """插件试图订阅 `manifest.consumes` 之外的 topic（fail-closed）。"""


class UndeclaredPublication(ValueError):
    """插件试图发布 `manifest.emits` 之外的 topic（fail-closed，事件不发出去）。

    ★ 与 `UndeclaredSubscription` 是同一套语义的两端：
      订阅端在**注册时**炸，发布端在**发布时**炸；
      两边都是「声明是唯一入口」这句话的执行点，不是可选的 lint。
    """


def _as_topics(topics: Any) -> frozenset[str]:
    """把 set/list/tuple/单个字符串归一成 topic 集合。

    ★ 单个字符串要当成**一个** topic——`for t in "todo.item.due"` 会按字符拆。
    """
    if isinstance(topics, str):
        return frozenset({topics})
    return frozenset(str(t) for t in topics)


def _declared_covers(declared: frozenset[str], wanted: str) -> bool:
    """声明 `declared` 是否覆盖处理器想要的 `wanted`。

    · 声明里的通配（`todo.*`）可以覆盖具体 topic（`todo.item.due`）；
    · 处理器**不能**发明声明里没有的通配——`wanted` 带通配字符时必须
      与声明的某一条**原样相等**（否则「处理器要一整个域」而声明只给了一个 topic，
      两者的差集无法在注册期判定，只能在投递期静默漏掉——那正是要消灭的失败模式）。
    """
    if wanted in declared:
        return True
    if any(ch in wanted for ch in "*?["):
        return False
    return any(fnmatch.fnmatchcase(wanted, p) for p in declared)


#: 声明字段的读取器。★ 每个字段**各写一句**，刻意不写成 `manifest.get(field)`：
#: 协议字段的消费点必须能被 `check_protocol_consumption.py` 的 AST 扫描看见；
#: 用变量当键，扫描器看不到 —— 那会让 emits / consumes 被判成「没人读」，
#: 于是门禁的绿灯建立在「扫描器瞎了」上（正是那道门禁最怕的失效方式）。
_FIELD_READERS: dict[str, Callable[[Any], Any]] = {
    "consumes": lambda m: m.get("consumes") if isinstance(m, Mapping) else getattr(m, "consumes", None),
    "emits": lambda m: m.get("emits") if isinstance(m, Mapping) else getattr(m, "emits", None),
}


def _declaration_of(info: Any, field: str = "consumes") -> tuple[str, frozenset[str]]:
    """从 PluginInfo / Manifest 模型 / 原始 manifest dict 里取 `(插件 id, <field> 声明)`。

    三种形态都只读 `id` 与 `field`（默认 `consumes`；发布端传 `emits`）——
    内核不认识插件的目录结构，manifest 是调用方（插件自己或发现层）递进来的。
    """
    reader = _FIELD_READERS.get(field)
    if reader is None:
        raise ValueError(f"未知的声明字段：{field!r}（只认 {sorted(_FIELD_READERS)}）")
    manifest = getattr(info, "manifest", None)
    if manifest is None:
        manifest = info
    if isinstance(manifest, Mapping):
        plugin_id = getattr(info, "id", None) or manifest.get("id")
    else:
        plugin_id = getattr(info, "id", None) or getattr(manifest, "id", None)
    raw = reader(manifest)
    if not plugin_id:
        raise ValueError("声明式订阅/发布缺少插件 id：info 应带 id，或 manifest 里必须有 id")
    if raw is None:
        raw = ()
    if isinstance(raw, str):
        raise TypeError(f"插件 {plugin_id!r} 的 manifest.{field} 必须是 topic 列表，收到字符串")
    return str(plugin_id), frozenset(str(t) for t in raw)


def _match(topic: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    return any(fnmatch.fnmatch(topic, p) for p in patterns)


def _format_sse(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, default=str)
    return f"id: {event['id']}\nevent: {event['topic']}\ndata: {data}\n\n"


# 全局单例
event_bus = EventBus()
