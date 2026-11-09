"""声明式激活器：manifest `activates_on` 语义的运行时实现。

★ 语义（对齐 VSCode activationEvents 哲学，收益是内存与稳定性，不是启动提速）：
  - 缺省 `[]` 或含 `startup:always` → 启动即激活（向后兼容：存量模块零漂移）；
  - `event:<topic>` → 注册但暂不激活，事件总线命中 topic 时延后 `load_router + mount`；
  - 未命中（事件从未发生）→ 保持「已注册未激活」，路由不存在（404）。
  - 本卡不做：请求触发型激活（硬约束 4）；cron/定时触发器（候卡）。

职责边界：
  - 注册表（app.state.modules 全量）由 create_app 主导，本器只管「激活」与「pending 索引」。
  - 挂载动作统一走 discover.mount_plugin（前置小步确立的单一入口）。
  - 激活失败：记入 last_error 表 + 暴露到 readyz（只报目击，不自动重启/禁用——卡档缓行纪律）。
    ★ ADR-0005（2026-10-03）：原 `_last_error` 只是进程内 dict，重启即失忆；
    「plugin_state 落库」原为候办项，现由 `persist_hook` 注入通道落地（见
    `set_persist_hook`），本模块仍不 import db，分层约定不破。
  - 线程安全：事件可能从任意线程 publish，activate() 全程持锁串行。
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from core.logging import get_logger
from core.plugins.discover import PluginInfo, mount_plugin

log = get_logger("kernel.plugins.activator")

STARTUP_ALWAYS = "startup:always"
EVENT_PREFIX = "event:"


def is_startup_always(manifest: dict[str, Any]) -> bool:
    """缺省（无 activates_on）或显式 startup:always → 启动即激活。"""
    acts = manifest.get("activates_on") or []
    return not acts or STARTUP_ALWAYS in acts


def event_triggers(manifest: dict[str, Any]) -> list[str]:
    """提取 event: 前缀的触发 topic（已剥前缀）。"""
    return [
        a[len(EVENT_PREFIX) :]
        for a in (manifest.get("activates_on") or [])
        if a.startswith(EVENT_PREFIX)
    ]


class PluginActivator:
    """注册/激活二分的运行时：startup 立即激活，事件命中延后激活。"""

    def __init__(self, registry: Any) -> None:
        self._registry = registry
        self._lock = threading.Lock()
        self._activated: set[str] = set()
        # topic -> 等待该事件的插件（激活时用）
        self._pending: dict[str, list[PluginInfo]] = {}
        # 激活失败目击表（id -> 错误摘要）；成功后移除。只报不自动处置。
        self._last_error: dict[str, str] = {}
        # ★ ADR-0005：落库通道（由 create_app 注入 manager 侧写函数）。
        #   本模块不 import db —— 分层约定不破；未注入时退化为纯内存（测试友好）。
        self._persist_hook: Callable[[str, str | None], None] | None = None

    def set_persist_hook(self, hook: Callable[[str, str | None], None] | None) -> None:
        """注入「把激活错误写进 plugin_state.last_error」的通道。

        传 None 可退回纯内存模式（单测用）。签名：hook(plugin_id, error_or_None)。
        """
        self._persist_hook = hook

    def _persist(self, plugin_id: str, error: str | None) -> None:
        """经注入通道落库；失败只记日志，绝不影响激活主流程。"""
        if self._persist_hook is None:
            return
        try:
            self._persist_hook(plugin_id, error)
        except Exception as exc:  # noqa: BLE001 —— 落库失败不该改变激活结果
            log.warning(
                "激活错误落库失败（已记录，不影响激活）",
                extra={"module": plugin_id, "error": f"{type(exc).__name__}: {exc}"},
            )

    # ─────────────────────── 查询 ───────────────────────
    @property
    def activated(self) -> list[str]:
        """已激活模块 id（排序稳定，供 readyz census）。"""
        return sorted(self._activated)

    @property
    def last_error(self) -> dict[str, str]:
        """激活失败目击表（只读快照）。"""
        return dict(self._last_error)

    def is_registered_pending(self, plugin_id: str) -> bool:
        with self._lock:
            return plugin_id not in self._activated and any(
                w.id == plugin_id
                for waiters in self._pending.values()
                for w in waiters
            )

    # ─────────────────────── 启动路径 ───────────────────────
    def activate_on_startup(self, info: PluginInfo) -> bool:
        """启动挂载判定：always → 立即激活返回 True；事件型 → 登记 pending 返回 False。
        ★ G2：degraded 插件跳过激活，不挂载路由，不打断其他插件。"""
        # ★ G2：不兼容插件降级处理，不激活
        if info.compat_status == "degraded":
            log.warning(
                "插件降级（不兼容，跳过激活）",
                extra={"module": info.id, "reason": info.manifest.get("_compat_reason", "unknown")},
            )
            return False
        if is_startup_always(info.manifest):
            self.activate(info)
            return True
        for topic in event_triggers(info.manifest):
            self._pending.setdefault(topic, []).append(info)
        log.info(
            "模块已注册待激活",
            extra={"module": info.id, "triggers": event_triggers(info.manifest)},
        )
        return False

    # ─────────────────────── 激活 ───────────────────────
    def activate(self, info: PluginInfo) -> None:
        """幂等激活（load_router + mount）。失败记录目击并原样上抛给调用方。"""
        with self._lock:
            if info.id in self._activated or info.id in self._registry.mounted():
                self._activated.add(info.id)
                return
            # 路径 a：container 型模块 = 纯前端编排容器，无 router.py
            # 也不该挂路由——登记激活态即返回，Dock/entry 由前端 manifest 驱动。
            if info.manifest.get("kind") == "container":
                self._activated.add(info.id)
                self._last_error.pop(info.id, None)
                self._persist(info.id, None)
                log.info("容器型模块跳过路由挂载", extra={"module": info.id})
                return
            try:
                mount_plugin(info, self._registry)
            except Exception as exc:  # noqa: BLE001 —— 缓行纪律：记目击，不自动处置
                err_text = f"{type(exc).__name__}: {exc}"
                self._last_error[info.id] = err_text
                # ★ ADR-0005：目击同时落库 —— 原为进程内 dict，重启即失忆，
                #   且 startup 恢复只读 enabled 不看错误，导致「永远不自愈」。
                self._persist(info.id, err_text)
                log.error(
                    "插件激活失败（已记录，不自动重试）",
                    extra={"module": info.id, "error": str(exc)},
                )
                raise
            self._activated.add(info.id)
            self._last_error.pop(info.id, None)
            self._persist(info.id, None)
            log.info("模块已激活", extra={"module": info.id})

    # ─────────────────────── 事件路径 ───────────────────────
    def on_event(self, event: dict[str, Any]) -> None:
        """事件总线回调：命中 pending topic 的插件逐个激活（幂等）。
        ★ G2：degraded 插件跳过激活。"""
        topic = str(event.get("topic", ""))
        waiters = self._pending.pop(topic, None)
        if not waiters:
            return
        for info in waiters:
            if info.compat_status == "degraded":
                continue
            try:
                self.activate(info)
            except Exception:  # noqa: BLE001 —— activate 内已记目击；事件回调绝不上抛
                continue

    # ─────────────────────── 与 manager 协同 ───────────────────────
    def clear_pending(self, plugin_id: str) -> bool:
        """移除某插件的全部 pending 登记（manager.disable 后由端点联动调用）。

        否则事件命中会把**已禁用**的插件重新挂回路由，击穿 disable 语义。
        返回是否真的移除了登记（幂等：无登记时返回 False）。
        """
        removed = False
        with self._lock:
            touched = [
                topic
                for topic, waiters in self._pending.items()
                if any(w.id == plugin_id for w in waiters)
            ]
            for topic in touched:
                kept = [w for w in self._pending[topic] if w.id != plugin_id]
                if kept:
                    self._pending[topic] = kept
                else:
                    del self._pending[topic]
                removed = True
        return removed
