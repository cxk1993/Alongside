"""pi-agent 插件路由 · ★ RPC 适配层接通（对话可用）。

════════════════════════════════════════════════════════════════════
★ 第三方插件结构约束（照 plugins/countdown 的先例，务必遵守）：

  内核按【文件路径】加载第三方路由（core/plugins/discover.py →
  load_python_module_from_file(router_path, f"plugin_thirdparty_{id}_router")），
  模块不属于任何包 → **不能用相对导入**（`from .schema import X` 会抛
  ImportError: attempted relative import with no known parent package）。
  故本文件：出入参模型与服务逻辑**就近写在本文件内**，同目录模块走 `_load_sibling`。

HTTP 契约（全项目统一，不许自创）：
  成功 → 直接返回资源 JSON（200/201），**不包 {code,data}**
  失败 → 抛 core.errors 的 AppError 子类，由内核统一转 RFC7807

★ 降级口径（采纳 workbuddy 拍砖）：
  L1 正常 → 走 pi（有工具能力）
  L2 重启中 → **快速失败**（不排队）
  L3 熔断 → 明确告知「AI 工具能力暂不可用」，**由前端保底轻量对话**

★（会话池）：**一个 session = 一个 pi 子进程**。
  - 外部 agent 用 `session="alpha"` / `"beta"` → **各自独立进程**（互不干扰）
  - `provides` 里三条能力经 **ADR-0003 MCP 桥**自动变成外部可调工具：
      pi_chat_write / pi_session_read / pi_session_write
════════════════════════════════════════════════════════════════════
"""

import importlib.util
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

log = logging.getLogger("plugin.pi-agent.router")

router = APIRouter()

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_MANIFEST_PATH = _PLUGIN_DIR / "manifest.json"
with _MANIFEST_PATH.open(encoding="utf-8") as _f:
    _MANIFEST: dict[str, Any] = json.load(_f)


def _load_sibling(name: str):
    """按路径加载同目录模块（第三方插件不能用相对导入）。"""
    mod_name = f"pi_agent_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, Path(__file__).resolve().parent / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


_SETTINGS_CACHE: dict[str, Any] = {}
_SETTINGS_CACHE_AT: float = 0.0
_SETTINGS_TTL = 5.0  # 秒；PATCH settings 后最迟 5 秒生效（无需重启服务）

# ★ 记录"池/管理器是按哪组参数建的"——设置变了自动重建（否则 provider/model 改动不生效）
_POOL_SIG: tuple[Any, ...] | None = None
_MGR_SIG: tuple[Any, ...] | None = None


def _plugin_settings(force: bool = False) -> dict[str, Any]:
    """读本插件的 plugin_setting 表（容错：读不到 → 返回 {}，绝不影响对话）。

    ★ 5 秒 TTL 缓存：避免每条消息都打库；PATCH settings 之后最迟 5 秒生效。
    """
    global _SETTINGS_CACHE, _SETTINGS_CACHE_AT
    import time as _time

    now = _time.monotonic()
    if not force and _SETTINGS_CACHE_AT and (now - _SETTINGS_CACHE_AT) < _SETTINGS_TTL:
        return _SETTINGS_CACHE
    try:
        from core.deps import db_session
        from core.plugins.settings import read_settings

        with db_session() as db:
            _SETTINGS_CACHE = read_settings(db, "pi-agent") or {}
    except Exception as exc:  # noqa: BLE001 — 设置读不到就用默认值，不该阻断对话
        log.debug("[pi-agent] 读插件设置失败（走默认值）：%s", exc)
        _SETTINGS_CACHE = {}
    _SETTINGS_CACHE_AT = now
    return _SETTINGS_CACHE


def _runtime_options() -> dict[str, Any]:
    """把插件设置翻译成运行时参数（缺省值与 settingsSchema 的 default 保持一致）。

    ★ 为什么要有这层（2026-10-10 修复）：此前 provider/model 硬编码在
      process.py / sessions.py（`DEFAULT_PROVIDER = "alongside"`），
      settingsSchema 里的 provider / model / pi_binary / max_parallel_sessions
      全是"摆设"——配了不生效，换网关必须改代码。这是实打实的风险点。
    """
    s = _plugin_settings()
    thinking = (str(s.get("thinking_level") or "high").strip()) or "high"
    model = (str(s.get("model") or "alongside").strip()) or "alongside"
    binary = str(s.get("pi_binary") or "").strip() or None
    try:
        max_sessions = int(s.get("max_parallel_sessions") or 4)
    except (TypeError, ValueError):
        max_sessions = 4
    # sandbox: None = 未设置（交给 get_pool 读环境变量）；bool = 显式开关
    sandbox = s.get("sandbox") if isinstance(s.get("sandbox"), bool) else None
    return {
        "provider": (str(s.get("provider") or "alongside").strip()) or "alongside",
        # pi 的 --model 支持 ":<thinking>" 后缀（见 rpc.py 注）
        "model": model if ":" in model else f"{model}:{thinking}",
        "binary": binary,
        "max_sessions": max_sessions,
        "sandbox": sandbox,
        "sandbox_image": str(s.get("sandbox_image") or "").strip() or None,
    }


def _manager():
    """取单会话进程管理器（形态；health/status 用）。

    ★ 设置变更（provider/model/binary）时自动重建，否则新配置不生效。
    """
    global _MGR_SIG
    proc = _load_sibling("process")
    o = _runtime_options()
    sig = (o["binary"], o["provider"], o["model"])
    if _MGR_SIG is not None and _MGR_SIG != sig:
        log.info("[pi-agent] 设置变更 → 重建进程管理器：%s → %s", _MGR_SIG, sig)
        try:
            proc.shutdown_manager()
        except Exception:  # noqa: BLE001
            pass
    _MGR_SIG = sig
    return proc.get_manager(
        cwd=str(_PLUGIN_DIR / "runtime"),
        binary=o["binary"], provider=o["provider"], model=o["model"],
    )


def _pool():
    """取会话池（对话与会话管理走这里）。

    ★ 设置变更时自动重建 —— 否则 PATCH settings 改了 provider/model 仍走旧值。
    """
    global _POOL_SIG
    sess = _load_sibling("sessions")
    o = _runtime_options()
    sig = (o["binary"], o["provider"], o["model"], o["max_sessions"], o["sandbox"], o["sandbox_image"])
    if _POOL_SIG is not None and _POOL_SIG != sig:
        log.info("[pi-agent] 设置变更 → 重建会话池：%s → %s", _POOL_SIG, sig)
        try:
            sess.shutdown_pool()
        except Exception:  # noqa: BLE001
            pass
    _POOL_SIG = sig
    return sess.get_pool(
        cwd=str(_PLUGIN_DIR / "runtime"),
        binary=o["binary"], provider=o["provider"], model=o["model"],
        max_sessions=o["max_sessions"], sandbox=o["sandbox"],
        sandbox_image=o["sandbox_image"],
    )


def _level() -> str:
    """降级层级（给 UI 顶栏状态点用）。

    ★ 2026-09-28 方案 A 重写（主人候办③「状态点恒显启动中」）：
      修复前只看**单会话管理器**（`_manager()`）—— 而**真实对话走的是会话池**
      （`_pool()`）。两条路各管各的：从没聊过天时单管理器 `_client` 恒为 None
      ⇒ 恒报 L2「启动中」，**pi 明明健康可用也一直显示"启动中"**。

      修后口径（按"哪个更活"取最高态）：
        L3 熔断（单管理器熔断态）
        L1 有活进程（池里有活会话 **或** 单管理器有活进程）
        L0 从未启动过（懒启动待命 —— 正常态）
        L2 尝试过但没进程（真·启动失败/重启中）
    """
    # 熔断优先（单管理器管熔断；池层面暂无熔断态）
    try:
        m_st = _manager().status()
    except Exception:  # noqa: BLE001
        m_st = {}
    if m_st.get("circuit_open"):
        return "L3"

    # ★ 有活进程：池 or 单管理器，任一活着即 L1
    p_st: dict[str, Any] = {}
    try:
        p_st = _pool().status()
        if int(p_st.get("alive", 0)) > 0:
            return "L1"
    except Exception:  # noqa: BLE001
        pass
    if m_st.get("alive"):
        return "L1"

    # ★ 从未启动过 = 待命（懒启动正常态），不是"启动中"
    #   池与单管理器**任一**尝试过启动 → 才算真·启动失败（L2）
    if not m_st.get("ever_started", False) and not p_st.get("ever_started", False):
        return "L0"

    return "L2"


# ── 出入参 ────────────────────────────────────────────────────


class ChatIn(BaseModel):
    message: str = Field(
        min_length=1, max_length=8000, description="发给 Pi 的消息（纯文本，≤8000 字）"
    )
    # ★ session 名 = 会话锁（同名同进程，异名异进程）
    session_id: str = Field(
        default="default", max_length=64,
        description="会话名（一个 session = 一个 Pi 子进程）；不传用 default，可锁定/切换",
    )


class SessionIn(BaseModel):
    """会话操作（★ 走 POST 而非路径参数 —— MCP 桥的路径是固定的）。"""

    action: str = Field(description="new | lock | release | stats")
    session: str = Field(default="default", max_length=64, description="目标会话名")


class ChatOut(BaseModel):
    session_id: str
    reply: str
    level: str
    settled: bool
    degraded: bool = False
    detail: str | None = None


# ── 端点 ──────────────────────────────────────────────────────


@router.get("/health")
def health() -> dict[str, Any]:
    """存活探针。ready=子进程是否活着（不主动起进程，避免探针触发拉起）。

    ★ 2026-09-28（方案 A）：`level` 改用综合口径 `_level()`（池 + 单管理器），
      与 /status 一致 —— 此前用 `st.get("level")`（只看单管理器）会误报。
    """
    try:
        st = _manager().status()
        return {"ok": True, "ready": bool(st.get("alive")), "level": _level()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "ready": False, "level": "L2", "detail": f"{type(exc).__name__}: {exc}"}


@router.get("/manifest")
def manifest() -> dict[str, Any]:
    """模块清单（前端 / AI 发现能力用）。"""
    return _MANIFEST


@router.get("/status")
def status() -> dict[str, Any]:
    """状态快照（UI 顶栏状态点）。

    ★ 2026-09-28（方案 A）关键修正：`level` 必须取 **`_level()`**（池 + 单管理器综合口径），
      不能取 `m.status()["level"]`（只看单管理器）—— 后者从没聊过天时恒为 L0/L2，
      正是「状态点恒显启动中」的最后一处漏洞。
    """
    m = _manager()
    st = m.status()
    try:
        st["pool"] = _pool().status()
    except Exception as exc:  # noqa: BLE001
        st["pool"] = {"detail": str(exc)}
    st["id"] = "pi-agent"
    st["stage"] = "rpc+sessions"
    # ★ 用综合口径覆盖（此前直接用 m.status()["level"] → 恒报错值）
    st["level"] = _level()
    st["level_text"] = {
        "L0": "AI 工具能力待命（未启动，发消息即用）",
        "L1": "AI 工具能力可用",
        "L2": "AI 服务启动失败，请稍后重试",
        "L3": "AI 工具能力暂不可用（pi 熔断）· 可重试",
    }.get(str(st.get("level")), "未知")
    return st


@router.post("/reset-circuit")
def reset_circuit() -> dict[str, Any]:
    """人工/长周期重试入口：把 L3 熔断解开（对应 UI 上的「重试」）。"""
    m = _manager()
    m.reset_circuit()
    return {"ok": True, "status": m.status()}


@router.post("/chat", response_model=ChatOut)
def chat(body: ChatIn) -> ChatOut:
    """同步版对话（**非流式**，便于测试与简单客户端）。

    ★ L2/L3 不抛 5xx，而是返回 `degraded=True` + 原因 ——
      让前端能优雅降级到「轻量对话」而不是白屏。
    """
    pool = _pool()
    text: list[str] = []
    settled = False
    try:
        for ev in pool.prompt(body.session_id, body.message):
            d = ev.text_delta
            if d:
                text.append(d)
            if ev.is_settled:
                settled = True
                break
    except Exception as exc:  # noqa: BLE001
        log.warning("[pi-agent] chat 降级（session=%s）：%s", body.session_id, exc)
        return ChatOut(
            session_id=body.session_id,
            reply="",
            level=_level(),
            settled=False,
            degraded=True,
            detail=str(exc),
        )
    return ChatOut(
        session_id=body.session_id,
        reply="".join(text),
        level=_level(),
        settled=settled,
    )


# ── 会话管理（★ 外部 agent 锁定/切换会话）────────────────


@router.get("/sessions/history")
def get_session_history(
    session: str | None = Query(default=None, description="会话名（不传=默认会话）"),
    limit: int = Query(default=200, description="最多取多少条历史（默认 200）"),
    include_tools: bool = Query(
        default=False, description="是否**含 toolResult 条目**（默认否 —— 前端只展示对话流）"
    ),
) -> dict[str, Any]:
    """★ 补刀（2026-09-27）：**读取会话历史消息**。

    主人报障「找不到历史」—— 实测此前 8 个端点**无任何读消息接口**（下游不存在，
    不是"读路径不统一"）。本端点补齐：读 runtime/sessions/*.jsonl（pi 原生 JSONL）
    并归一化为前端可直接渲染的 [{role, text, ts}]。

    - session：pi 会话 id / 文件名前缀 / 序号（0=最新）；空或 default = 最新会话
    - limit：返回末尾 N 条（默认 200）
    - include_tools：是否含 toolResult 条目（默认否 —— 前端只展示对话流）
    """
    # ★ 2026-09-29（云昔）：改为 _load_sibling —— 本插件是**按文件路径加载的第三方插件**，
    #   相对导入会抛 ImportError（插件挂载失败期间，这个 bug 一直被掩盖着）。
    read_messages = _load_sibling("history").read_messages

    return read_messages(session, limit=limit, include_tools=include_tools)


@router.get("/sessions")
def list_sessions() -> dict[str, Any]:
    """★ 列出当前活跃会话（外部 agent 据此发现"谁在用哪个会话"）。

    → 经 MCP 桥暴露为工具 `pi_session_read`。
    """
    pool = _pool()
    return {"sessions": pool.list_sessions(), "pool": pool.status()}


@router.post("/sessions")
def session_action(body: SessionIn) -> dict[str, Any]:
    """★ 会话操作：`new`（新开）/ `lock`（锁定）/ `release`（释放）/ `stats`（统计）。

    → 经 MCP 桥暴露为工具 `pi_session_write`。
    ★ 用 POST + action 而非路径参数 —— MCP 桥的转发路径是固定的，带不了路径变量。
    """
    pool = _pool()
    act = (body.action or "").strip().lower()
    if act == "new":
        return {"action": act, **pool.new_session(body.session)}
    if act == "lock":
        return {"action": act, **pool.lock(body.session)}
    if act == "release":
        return {"action": act, **pool.release(body.session)}
    if act == "stats":
        return {"action": act, **pool.stats(body.session)}
    return {"action": act, "ok": False, "detail": f"未知 action：{act!r}（支持 new|lock|release|stats）"}


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
def chat_stream(body: ChatIn) -> StreamingResponse:
    """★ 流式对话（SSE）。事件：`delta`（文本增量）/ `settled` / `error`。"""

    def gen() -> Iterator[str]:
        pool = _pool()
        try:
            for ev in pool.prompt(body.session_id, body.message):
                d = ev.text_delta
                if d:
                    yield _sse("delta", {"text": d})
                if ev.is_settled:
                    yield _sse("settled", {"session_id": body.session_id})
                    return
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {
                "detail": str(exc),
                "level": _level(),
                "degraded": True,
            })

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
