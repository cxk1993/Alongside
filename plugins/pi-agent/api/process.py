"""pi-agent · 进程托管（★ 采纳 workbuddy 拍砖口径）。

一句话立场（workbuddy 原话，全盘采纳）：
    **「内核要做的不是让它不崩，而是让它崩了也不影响主人用别的。」**

四件事：
  1. **指数退避重启**（1/2/4…≤60s）+ **熔断**（10 分钟内 ≥5 次 → 停止自动重启）
  2. **心跳辨「卡死 vs 崩溃」**：进程在但 RPC 无响应 = 卡死 → SIGTERM→SIGKILL→重启
  3. **三层降级**：L1 正常 / L2 重启中（**快速失败，不排队**）/ L3 熔断（保底轻量对话）
  4. **fail-loud**：每次重启/熔断都记日志 + 事件（别静默）

★ 第三方插件约束：本文件**不能用相对导入**。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Iterator
from typing import Any

# ★ 同目录模块按路径导入（第三方插件不能用相对导入）
import importlib.util
import sys
from pathlib import Path


def _load_sibling(name: str):
    """按路径加载同目录模块（与内核加载第三方插件的方式一致）。"""
    path = Path(__file__).resolve().parent / f"{name}.py"
    mod_name = f"pi_agent_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


_rpc = _load_sibling("rpc")
PiRpcClient = _rpc.PiRpcClient
PiRpcError = _rpc.PiRpcError
PiEvent = _rpc.PiEvent
LEVEL_L0, LEVEL_L1, LEVEL_L2, LEVEL_L3 = (
    _rpc.LEVEL_L0, _rpc.LEVEL_L1, _rpc.LEVEL_L2, _rpc.LEVEL_L3,
)

log = logging.getLogger("plugin.pi-agent.process")

BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 60.0
CIRCUIT_WINDOW_S = 600.0
CIRCUIT_FAILS = 5


class PiProcessManager:
    """pi 子进程的单例托管者（带退避 / 熔断 / 心跳 / 降级）。

    ★ 全局唯一实例由 module-level `get_manager()` 提供；插件启停时 start/stop。
    """

    def __init__(
        self,
        *,
        cwd: str,
        binary: str | None = None,
        provider: str = "alongside",
        model: str = "alongside",
    ) -> None:
        self._cwd = cwd
        self._binary = binary
        self._provider = provider
        self._model = model
        self._client: Any = None
        self._lock = threading.RLock()
        self._fail_ts: deque[float] = deque(maxlen=32)  # 失败时间戳（熔断统计）
        self._consecutive_fails = 0
        self._circuit_open = False
        self._circuit_opened_at = 0.0
        self._last_error: str | None = None
        # ★ 2026-09-28（方案 A）：是否**尝试过**启动。
        #   False = 从未被叫醒（懒启动正常态 → L0「待命」，不是故障）
        #   True  = 尝试过但没进程（启动失败/重启中 → L2，真·异常）
        self._ever_started = False

    # ── 状态 ────────────────────────────────────────────────
    @property
    def level(self) -> str:
        """当前降级层级（给 UI 顶栏状态点用）。

        ★ 2026-09-28 方案 A 语义修正（主人候办③「状态点恒显启动中」）：
            L3 = 熔断
            L1 = 有活进程（正常）
            L0 = **从未启动过**（懒启动待命 —— 正常态，别报"启动中"吓人）
            L2 = 尝试过启动但没进程（真·启动失败/重启中）
        """
        if self._circuit_open:
            return LEVEL_L3
        if self._client is not None and self._client.is_alive():
            return LEVEL_L1
        if not self._ever_started:
            return LEVEL_L0
        return LEVEL_L2

    def status(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "circuit_open": self._circuit_open,
            "consecutive_fails": self._consecutive_fails,
            "last_error": self._last_error,
            "alive": bool(self._client and self._client.is_alive()),
            "ever_started": self._ever_started,
            "model": self._model,
            "provider": self._provider,
        }

    # ── 熔断 ────────────────────────────────────────────────
    def _note_failure(self) -> None:
        now = time.time()
        self._fail_ts.append(now)
        self._consecutive_fails += 1
        recent = [t for t in self._fail_ts if now - t <= CIRCUIT_WINDOW_S]
        if len(recent) >= CIRCUIT_FAILS:
            self._circuit_open = True
            self._circuit_opened_at = now
            log.error(
                "[pi-agent] ★ 熔断开启：%ds 内 %d 次失败 → 停止自动重启（转 L3 降级）",
                int(CIRCUIT_WINDOW_S), len(recent),
            )

    def _note_success(self) -> None:
        self._consecutive_fails = 0
        if self._circuit_open:
            self._circuit_open = False
            log.info("[pi-agent] ★ 熔断恢复：pi 已重新可用")

    def reset_circuit(self) -> None:
        """人工/长周期重试入口（L3 → 允许再试）。"""
        with self._lock:
            self._circuit_open = False
            self._fail_ts.clear()
            self._consecutive_fails = 0
            log.info("[pi-agent] 熔断已被人工重置")

    # ── 启动 / 保活 ─────────────────────────────────────────
    def _backoff_seconds(self) -> float:
        return min(BACKOFF_BASE_S * (2 ** max(0, self._consecutive_fails - 1)), BACKOFF_MAX_S)

    def ensure_started(self) -> bool:
        """确保子进程可用。失败则按退避等待后重试**一次**，仍失败返回 False。

        ★ 熔断开启时**直接返回 False**（快速失败，不排队 —— 避免"重启后涌进一堆过期请求"）。
        """
        with self._lock:
            if self._circuit_open:
                return False
            if self._client is not None and self._client.is_alive():
                return True
            # ★ 2026-09-28（方案 A）：走到这里 = 真尝试过启动（此后 level 不再报 L0 待命）
            self._ever_started = True
            # 崩溃 or 未起：按退避重启
            if self._consecutive_fails > 0:
                wait = self._backoff_seconds()
                log.warning("[pi-agent] 退避 %.0fs 后重启 pi（连续失败 %d 次）", wait, self._consecutive_fails)
                time.sleep(min(wait, 5.0))  # 请求路径上不真等满 60s（避免拖住用户）
            try:
                if self._client is not None:
                    self._client.stop()
                self._client = PiRpcClient(
                    cwd=self._cwd, binary=self._binary,
                    provider=self._provider, model=self._model,
                )
                self._client.start()
                self._note_success()
                return True
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._note_failure()
                log.error("[pi-agent] pi 启动失败：%s", self._last_error)
                return False

    def check_alive(self) -> bool:
        """★ 心跳：辨「卡死 vs 崩溃」。

        进程已退出 → 判崩溃；进程在但心跳连续失败 → 判卡死 → 温和 kill。
        """
        with self._lock:
            if self._client is None or not self._client.is_alive():
                return False
            for _ in range(_rpc.HEARTBEAT_FAILS_TO_KILL):
                if self._client.get_state() is not None:
                    return True
            log.warning("[pi-agent] ★ 心跳连续失败 → 判卡死，重启子进程")
            try:
                self._client.stop()
            except Exception:  # noqa: BLE001
                pass
            self._note_failure()
            return False

    # ── 对话 ────────────────────────────────────────────────
    def prompt(self, message: str) -> Iterator[PiEvent]:
        """流式对话。**L2/L3 一律快速失败**（不排队）。

        Raises:
            PiRpcError: 熔断中 / 起不来 / 超时 —— 调用方据此转 L3 轻量对话。
        """
        if self.level == LEVEL_L3:
            raise PiRpcError("pi 处于熔断态（L3）：AI 工具能力暂不可用，已降级为轻量对话")
        if not self.ensure_started():
            raise PiRpcError(
                f"pi 不可用（{self.level}）：{self._last_error or '启动失败'}"
            )
        try:
            yielded = False
            for ev in self._client.prompt(message):
                yielded = True
                yield ev
            self._note_success()
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._note_failure()
            raise PiRpcError(self._last_error) from exc

    def stop(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._client = None


# ── 单例 ────────────────────────────────────────────────────
# ★ 踩坑记录（2026-09-25）：单例是**模块级**的 ——
#   若同一份 process.py 被以**不同模块名**加载两次（内核加载叫 pi_agent_process，
#   测试里另起名会得到**另一个** _MANAGER），就会出现"两个进程管理器"：
#   测试改了 A 的 binary，router 用的是 B → 断言全错。
#   **正确做法：永远通过 get_manager() 拿，且保证模块名一致。**
_MANAGER: PiProcessManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_manager(
    *, cwd: str | None = None, binary: str | None = None,
    provider: str = "alongside", model: str = "alongside:high",
) -> PiProcessManager:
    """取全局单例（首次调用时按参数创建）。"""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            if cwd is None:
                # 默认工作目录：插件的 runtime 目录（★ 避免读到 Alongside 之外的 AGENTS.md）
                cwd = str(Path(__file__).resolve().parent.parent / "runtime")
            _MANAGER = PiProcessManager(cwd=cwd, binary=binary, provider=provider, model=model)
        return _MANAGER


def shutdown_manager() -> None:
    """插件禁用/卸载时调用。"""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is not None:
            _MANAGER.stop()
            _MANAGER = None
