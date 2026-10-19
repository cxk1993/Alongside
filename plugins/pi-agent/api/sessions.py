"""pi-agent · 会话池（★ 主人第③问的落地）。

════════════════════════════════════════════════════════════════════
主人原话：
  「我们的 alongside 里面，能不能有一个类似于 mcp 或者 api 的口子，让我们**外部的
    任何 ai agent** 都可以与我们的**内嵌的 pi 进行通话**？并且可以**锁定与切换会话
    session**？」

设计（一句话）：**一个 session = 一个 pi 子进程**。
  - 外部 agent A 用 `session="alpha"` → 进程 1
  - 外部 agent B 用 `session="beta"`  → 进程 2
  → **天然隔离**（互不干扰），**"锁定"就是"占着这个 session 的进程"**，
     **"切换"就是"换一个 session 名"**。

为什么不用单进程 + `switch_session`：
  `switch_session` 是**全局状态**——两个 agent 同时用会互相踩（A 切到 alpha、
  B 切到 beta，A 的下一句就跑进 beta 了）。**一会话一进程**从结构上消灭了这个
  竞争，代价是进程数（用上限 + LRU 淘汰兜住）。

★ 上限与淘汰：`max_sessions`（默认 4）· 超限时淘汰**最久未用**且**空闲**的会话。
★ 会话文件落在 `plugins/pi-agent/runtime/sessions/`（插件目录内，符合 permissions 收口）。

★ 第三方插件约束：本文件**不能用相对导入**。
════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import importlib.util
import os
import logging
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

log = logging.getLogger("plugin.pi-agent.sessions")


def _load_sibling(name: str):
    """按路径加载同目录模块（第三方插件不能用相对导入）。"""
    mod_name = f"pi_agent_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


DEFAULT_MAX_SESSIONS = 4
IDLE_EVICT_SECONDS = 1800.0  # 30 分钟未用 → 可被淘汰


class _SessionEntry:
    """一个会话槽位（= 一个 pi 子进程）。"""

    __slots__ = ("name", "client", "created_at", "last_used_at", "busy")

    def __init__(self, name: str, client: Any) -> None:
        self.name = name
        self.client = client
        self.created_at = time.time()
        self.last_used_at = self.created_at
        self.busy = False

    def is_alive(self) -> bool:
        return bool(self.client and self.client.is_alive())


class PiSessionPool:
    """按 session 名分进程的池（带上限 + LRU 淘汰 + 单会话串行）。"""

    def __init__(
        self,
        *,
        cwd: str,
        binary: str | None = None,
        provider: str = "alongside",
        model: str = "alongside:high",
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        # ★ 沙箱模式（pi 跑在 Docker 容器里）
        sandbox: bool = False,
        sandbox_image: str | None = None,
    ) -> None:
        self._cwd = cwd
        self._binary = binary
        self._provider = provider
        self._model = model
        self._sandbox = sandbox
        self._sandbox_image = sandbox_image
        self._max = max(1, max_sessions)
        self._sessions: OrderedDict[str, _SessionEntry] = OrderedDict()
        self._lock = threading.RLock()
        # ★ 2026-09-28（方案 A）：是否**尝试过**启动会话进程。
        #   与 process.PiProcessManager 同语义 —— 供 router._level() 区分
        #   「从未启动（L0 待命）」与「尝试过但失败（L2）」。
        self._ever_started = False
        self._last_error: str | None = None
        self._session_dir = str(Path(cwd) / "sessions")
        Path(self._session_dir).mkdir(parents=True, exist_ok=True)

    # ── 内部 ────────────────────────────────────────────────
    def _evict_if_needed(self) -> None:
        """超限时淘汰最久未用且**不忙**的会话（忙的不动，宁可临时超限）。"""
        if len(self._sessions) < self._max:
            return
        now = time.time()
        for name in list(self._sessions.keys()):
            if len(self._sessions) < self._max:
                break
            e = self._sessions[name]
            if e.busy:
                continue
            if now - e.last_used_at < IDLE_EVICT_SECONDS and len(self._sessions) <= self._max:
                # 未闲置够久：仅在**确实超限**时才淘汰
                if len(self._sessions) <= self._max:
                    continue
            log.info("[pi-agent] 淘汰会话 %s（LRU）", name)
            try:
                e.client.stop()
            except Exception:  # noqa: BLE001
                pass
            del self._sessions[name]

    def _ensure(self, name: str) -> _SessionEntry:
        """取（或创建）某会话。★ 调用方需持有 self._lock。"""
        e = self._sessions.get(name)
        if e is not None and e.is_alive():
            self._sessions.move_to_end(name)
            return e
        if e is not None:  # 死了：清掉重建
            try:
                e.client.stop()
            except Exception:  # noqa: BLE001
                pass
            del self._sessions[name]
        self._evict_if_needed()
        rpc = _load_sibling("rpc")
        # ★ 2026-09-28（方案 A）：走到这里 = 真尝试过建会话进程
        self._ever_started = True
        client = rpc.PiRpcClient(
            cwd=self._cwd, binary=self._binary,
            provider=self._provider, model=self._model,
            extra_args=["--session-dir", self._session_dir],
            # ★ 池用**持久会话** —— 会话落盘后，pi-web-ui（同 agent-dir + 同 cwd）
            #   与外部工具才能看到/续接同一个会话。
            persist=True,
            sandbox=self._sandbox,
            **({"sandbox_image": self._sandbox_image} if self._sandbox_image else {}),
        )
        try:
            client.start()
        except Exception as exc:  # noqa: BLE001
            # ★ 2026-09-28（方案 A）：记下真实失败原因（供 _level()/status 判"启动失败"）
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise
        e = _SessionEntry(name, client)
        self._sessions[name] = e
        self._last_error = None
        log.info("[pi-agent] 新会话 %s（当前 %d 个）", name, len(self._sessions))
        return e

    # ── 对外 ────────────────────────────────────────────────
    def prompt(self, session: str, message: str) -> Iterator[Any]:
        """在指定会话里发一条消息（**该会话串行**，不同会话并行）。"""
        rpc = _load_sibling("rpc")
        with self._lock:
            e = self._ensure(session)
            if e.busy:
                raise rpc.PiRpcError(
                    f"会话 {session!r} 正忙（同一会话同一时刻只处理一条消息）"
                )
            e.busy = True
            e.last_used_at = time.time()
        try:
            yield from e.client.prompt(message)
        finally:
            with self._lock:
                ent = self._sessions.get(session)
                if ent is not None:
                    ent.busy = False
                    ent.last_used_at = time.time()

    def new_session(self, session: str) -> dict[str, Any]:
        """在指定会话槽位里开一个**全新**会话（RPC `new_session`）。"""
        with self._lock:
            e = self._ensure(session)
            e.busy = True
        try:
            rid = "new1"
            e.client._send({"id": rid, "type": "new_session"})
            deadline = time.time() + 30
            while True:
                ev = e.client._read_event(deadline)
                if ev is None:
                    return {"session": session, "ok": False, "detail": "new_session 超时"}
                if ev.type == "response" and ev.raw.get("id") == rid:
                    return {
                        "session": session,
                        "ok": bool(ev.raw.get("success")),
                        "cancelled": bool((ev.raw.get("data") or {}).get("cancelled")),
                    }
        finally:
            with self._lock:
                ent = self._sessions.get(session)
                if ent is not None:
                    ent.busy = False

    def stats(self, session: str) -> dict[str, Any]:
        """某会话的用量/上下文统计（RPC `get_session_stats`）。"""
        with self._lock:
            e = self._sessions.get(session)
            if e is None or not e.is_alive():
                return {"session": session, "exists": False}
        rid = "st1"
        try:
            e.client._send({"id": rid, "type": "get_session_stats"})
            deadline = time.time() + 20
            while True:
                ev = e.client._read_event(deadline)
                if ev is None:
                    return {"session": session, "exists": True, "detail": "stats 超时"}
                if ev.type == "response" and ev.raw.get("id") == rid:
                    data = ev.raw.get("data") or {}
                    return {"session": session, "exists": True, **data}
        except Exception as exc:  # noqa: BLE001
            return {"session": session, "exists": True, "detail": str(exc)}

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出当前活跃会话（给外部 agent 发现"谁在用哪个会话"）。"""
        with self._lock:
            out = []
            for name, e in self._sessions.items():
                out.append({
                    "session": name,
                    "alive": e.is_alive(),
                    "busy": e.busy,
                    "age_seconds": int(time.time() - e.created_at),
                    "idle_seconds": int(time.time() - e.last_used_at),
                })
            return out

    def lock(self, session: str) -> dict[str, Any]:
        """★ "锁定"某会话：确保进程存在并标记占用（外部 agent 声明"这个会话归我"）。

        实现上"锁定"= 让该 session 的进程**保持存在**且**独占**；
        真正的互斥由 `busy` 标志保证（同会话串行）。
        """
        with self._lock:
            e = self._ensure(session)
            return {"session": session, "locked": True, "alive": e.is_alive()}

    def release(self, session: str) -> dict[str, Any]:
        """释放/关闭某会话（外部 agent 用完主动还）。"""
        with self._lock:
            e = self._sessions.pop(session, None)
            if e is None:
                return {"session": session, "released": False, "detail": "不存在"}
            try:
                e.client.stop()
            except Exception:  # noqa: BLE001
                pass
            return {"session": session, "released": True}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "sessions": len(self._sessions),
                "max_sessions": self._max,
                "busy": sum(1 for e in self._sessions.values() if e.busy),
                "alive": sum(1 for e in self._sessions.values() if e.is_alive()),
                "names": list(self._sessions.keys()),
                "ever_started": self._ever_started,   # ★ 09-28：供 _level() 区分 L0/L2
                "last_error": self._last_error,
                "sandbox": self._sandbox,          # ★ 是否容器隔离
                "sandbox_image": self._sandbox_image if self._sandbox else None,
            }

    def stop_all(self) -> None:
        with self._lock:
            for e in self._sessions.values():
                try:
                    e.client.stop()
                except Exception:  # noqa: BLE001
                    pass
            self._sessions.clear()


# ── 单例 ────────────────────────────────────────────────────
# ★ 同 process.py 的教训：单例是模块级的，务必通过 get_pool() 取，别重复加载本模块。
_POOL: PiSessionPool | None = None
_POOL_LOCK = threading.Lock()


def get_pool(
    *, cwd: str | None = None, binary: str | None = None,
    provider: str = "alongside", model: str = "alongside:high",
    max_sessions: int = DEFAULT_MAX_SESSIONS,
    sandbox: bool | None = None,
    sandbox_image: str | None = None,
) -> PiSessionPool:
    """取全局单例。★ 沙箱开关默认读环境变量 `PI_AGENT_SANDBOX`（1/true 开）。"""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            if cwd is None:
                cwd = str(Path(__file__).resolve().parent.parent / "runtime")
            if sandbox is None:
                sandbox = (os.environ.get("PI_AGENT_SANDBOX", "") or "").strip().lower() in (
                    "1", "true", "yes", "on",
                )
            _POOL = PiSessionPool(
                cwd=cwd, binary=binary, provider=provider, model=model,
                max_sessions=max_sessions,
                sandbox=sandbox, sandbox_image=sandbox_image,
            )
        return _POOL


def shutdown_pool() -> None:
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.stop_all()
            _POOL = None
