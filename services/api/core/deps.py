"""依赖注入：数据库会话 + 当前用户 + 作用域。

★ T04 未就绪：core 只依赖接口（Protocol），不 import db/。
  get_db 在没有引擎时明确报错（"数据库未就绪"），不许静默成功。
  T04 就绪后调用 set_engine(engine) 注入真实引擎即可，无需改其它代码。

★ ISSUE-005 A 案（2026-09-20，hermes）：get_plugin_client —— 插件间内部调用通道。
  ADR-0002「声明即授权」机器强制：caller 的 manifest.requires 必须包含
  目标能力，否则 403。消除各插件自签 token / 自管 httpx 的样板成本。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol, runtime_checkable

from fastapi import Request
from sqlmodel import Session

from core.db_scope import scope_session
from core.errors import ForbiddenError, ServiceUnavailableError
from core.security import get_current_user, require_scope

__all__ = [
    "get_db",
    "db_session",
    "kernel_session",
    "get_current_user",
    "require_scope",
    "set_engine",
    "DatabaseProvider",
    "get_plugin_client",
    "InternalHttpClient",
]


@runtime_checkable
class DatabaseProvider(Protocol):
    """T04 提供的数据库引擎接口（最小形态）。"""

    def __call__(self) -> Session: ...  # 返回一个可用 Session


_engine_factory: Any = None


def set_engine(factory: Any) -> None:
    """T04 注入引擎工厂（callable -> Session）。core 自身不建引擎。"""
    global _engine_factory
    _engine_factory = factory


def get_db(request: Request) -> Iterator[Session]:
    """依赖注入：返回数据库会话。

    ★ 根因修复（2026-09-25 · workbuddy 执行）：
      此前为**普通函数** `return _engine_factory()` —— FastAPI 对**非生成器依赖永不
      close**，139 处 `Depends(get_db)` 全部裸漏连接（QueuePool exhausted → 500，
      75 次存量泄漏实测）。
      改为**生成器** `yield` + `finally close`：**一处改，全部调用点受益**，
      且与 FastAPI 的 `Depends(get_db)` 完全兼容（原生支持生成器依赖）。

    开发期/测试期若 T04 尚未注入引擎，调用即报错（明确告知等待 T04），
    绝不会返回假连接掩盖故障。

    ★ 数据访问作用域（2026-10-28）：
      插件的请求落在**该插件的作用域**里 ⇒ 它只能碰自己前缀的表。
      作用域从 `request.state.db_scope` 取（由 `ModuleRegistry.mount` 挂的
      路由级依赖写入），记在 `session.info` 上，
      由 SQLAlchemy 的 `after_begin` 事件挂到连接上。
      ★ 为什么不用 contextvar：实测它跨不过 anyio 的 context 切换
        （依赖里设的，端点里读不到），且线程池里 reset 会抛 ValueError。
        作用域随 Session 走，与线程无关。
    """
    if _engine_factory is None:
        raise ServiceUnavailableError(
            "数据库未就绪：等待 T04 注入引擎（core.deps.set_engine）"
        )
    session = _engine_factory()
    scope_session(session, getattr(request.state, "db_scope", None))
    try:
        yield session
    finally:
        # ★ 必须 close：把连接还给池（生成器依赖的收尾钩子由 FastAPI 保证执行）
        session.close()


@contextmanager
def db_session(plugin_id: str) -> Iterator[Session]:
    """**插件作用域会话**：`plugin_id` 必填，只能碰 `{plugin_id}_*` 的表。

    ★ 2026-11-09「翻转默认值」：这个参数从**可选**改成**必填**。

      旧签名是 `db_session(plugin_id: str | None = None)`，并且文档里写着
      「不传（默认）= 内核级，不受边界约束」。那条设计的出发点是善意的
      （后台任务要写 `audit_log` 这类内核表），但代价是：
      **「读别人的表会被拒」只在「插件端点 + Depends(get_db)」这一条路径上成立**，
      定时任务 / 事件处理器 / 生命周期钩子 / 插件自开的 session 全都绕开了它。
      AI 动态改造的第一条红线「AI 不许修改审计日志」，在数据层等于没有执行点。

      ⇒ 现在分成两个函数，**没有第三种**：

      | 想做什么 | 用哪个 |
      |---|---|
      | 内核/内置模块自己的活（状态机、迁移台账、审计、对账、定时任务） | `kernel_session()` |
      | 第三方插件自己的活（钩子、后台任务） | `db_session("<plugin_id>")` |
      | 忘了声明 | 不存在——签名不允许 |

    ★ 为什么内置模块也用 `kernel_session()`：
      模块之间本来就有跨模块读（dashboard 读 `calendar_event`、finance 读 `habits_habit`、
      catalog 读 `web_entry`…）。它们是**系统的一部分**，不是外来者。
      边界只约束外来者——这条在 `core/registry.py:mount(scoped=...)` 里是同一个判断。
    """
    if _engine_factory is None:
        raise ServiceUnavailableError(
            "数据库未就绪：等待 T04 注入引擎（core.deps.set_engine）"
        )
    session = _engine_factory()
    scope_session(session, plugin_id)
    try:
        yield session
    finally:
        session.close()


@contextmanager
def kernel_session() -> Iterator[Session]:
    """**内核特权会话**：不受插件数据边界约束。

    ★ 与 `db_session(plugin_id)` 的分工（2026-11-09「翻转默认值」）：

    | 谁 | 用哪个 | 能碰哪些表 |
    |---|---|---|
    | 内核自己（状态机、迁移台账、审计落库、启动对账） | `kernel_session()` | 全部 |
    | 插件自己（生命周期钩子、定时任务、事件处理器） | `db_session("<plugin_id>")` | 自己的 `{id}_*` |

    ★ **没有第三种**。不显式声明作用域的 Session 会被 **deny-all** 挡下
      （只放行 `sqlite_*` 内部表与 PRAGMA）——见 `core/db_scope.py` 的三态判定。

    ★ 为什么必须显式（这条是本次改造的核心理由）
      --------------------------------------------------
      旧实现对「不传 `plugin_id`」的默认是**内核级、不受任何约束**。
      于是「读别人的表会被拒」这句话，只在「插件端点 + `Depends(get_db)`」
      这一条路径上成立；**定时任务、事件处理器、生命周期钩子、插件自己开的
      `db_session()` 全都绕开了它**。而 AI 动态改造的四条红线里第一条
      「AI 不许修改审计日志」，在数据层没有任何执行点。
      ⇒ 把默认翻过来：**不声明 = 拒绝；要特权 = 显式写出来**。

    ★ 谁能用：只有 `core/`。`modules/` 与 `plugins/` **禁止 import**——
      由 `tools/gates/` 的门禁守着（写了这条门禁，才不会有人图省事又绕回去）。
    """
    if _engine_factory is None:
        raise ServiceUnavailableError(
            "数据库未就绪：等待 T04 注入引擎（core.deps.set_engine）"
        )
    session = _engine_factory()
    # ★ 显式声明「内核」——它与「压根没声明」（deny-all）是两种不同的状态。
    scope_session(session, None)
    try:
        yield session
    finally:
        session.close()


# ───────────────────────── ISSUE-005 A 案：插件间内部调用 ─────────────────────────

import httpx  # noqa: E402


# 内部 API 基址：同进程内直接打本服务（测试/本地/生产都是 127.0.0.1:18000）。
# 可被 INTERNAL_API_BASE 覆盖（如测试时指向 TestClient 不可行，仍走真实服务）。
def _internal_base() -> str:
    import os

    return os.environ.get("INTERNAL_API_BASE", "http://127.0.0.1:18000")


class InternalHttpClient:
    """插件间内部调用 client（自动带 token / 错误映射 / base_url）。

    由 get_plugin_client() 创建，插件侧只拿它调目标插件的 API：
        client = get_plugin_client(request, ["docs.node.read"])
        client.get("/api/v1/docs/nodes")
    错误统一抛 ValidationError（与 RFC7807 对齐）。
    """

    def __init__(self, token: str, base: str | None = None) -> None:
        self._headers = {"Authorization": f"Bearer {token}"}
        self._base = base or _internal_base()

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        with httpx.Client(base_url=self._base, headers=self._headers, timeout=10) as c:
            r = c.request(method, path, **kw)
            if r.status_code >= 400:
                from core.errors import ValidationError

                raise ValidationError(
                    f"内部 API {method} {path} → HTTP {r.status_code}: {r.text[:200]}"
                )
            if r.status_code == 204 or not r.content:
                return None
            return r.json()

    def get(self, path: str, **kw: Any) -> Any:
        return self._req("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self._req("POST", path, **kw)

    def patch(self, path: str, **kw: Any) -> Any:
        return self._req("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self._req("DELETE", path, **kw)


def _caller_plugin_id(request: Request) -> str:
    """从请求路径反查调用者插件 id（真实路径，伪造不了）。

    内核挂载路由时 prefix=manifest.api.base（如 /api/v1/diary）。
    反查 app.state.modules：哪个模块的 api.base 是当前路径的前缀。
    内核自己的 /api/v1/modules 等不算插件调用，无 caller → 返回空。
    """
    path = request.url.path
    modules: dict[str, dict[str, Any]] = getattr(request.app.state, "modules", {})
    best: tuple[int, str] | None = None
    for pid, manifest in modules.items():
        base = (manifest.get("api") or {}).get("base", "")
        if base and path.startswith(base) and (best is None or len(base) > best[0]):
            best = (len(base), pid)
    return best[1] if best else ""


def get_plugin_client(
    request: Request,
    target_capabilities: list[str],
) -> InternalHttpClient:
    """★ ISSUE-005 A 案：发放作用域受限的内部调用 client。

    机器强制「声明即授权」（ADR-0002）：
      1. 反查 caller 插件（由请求路径决定）
      2. 读 caller manifest.requires
      3. target_capabilities 必须 ⊆ requires，否则 403 Forbidden
    通过则返回带当前用户 token 的 client（口径统一，消除自签样板）。

    用法（插件路由内）：
        from core.deps import get_plugin_client
        client = get_plugin_client(request, ["docs.node.read", "docs.node.write"])
        nodes = client.get("/api/v1/docs/nodes")
    """
    caller = _caller_plugin_id(request)
    if not caller:
        raise ForbiddenError(
            "内部调用未授权：无法识别调用方插件（仅插件路由内可调用）"
        )
    modules: dict[str, dict[str, Any]] = getattr(request.app.state, "modules", {})
    manifest = modules.get(caller) or {}
    requires: list[str] = manifest.get("requires", []) or []
    # ★ 2026-09-25：**软依赖也算已授权**。
    #   语义：optionalDependencies 是"缺了降级不死"的可选能力 ——
    #   **能力在时就应该能用**（否则声明软依赖毫无意义：既要调又永远 403）。
    optional: list[str] = manifest.get("optionalDependencies", []) or []
    granted: list[str] = [*requires, *optional]

    missing = [cap for cap in target_capabilities if cap not in granted]
    if missing:
        raise ForbiddenError(
            f"内部调用未授权：插件「{caller}」requires={requires} optional={optional}，"
            f"未声明能力 {missing}（ADR-0002 声明即授权）"
        )

    # 当前用户 token：从请求 Authorization 头解析当前 user（插件路由必经
    # Depends(get_current_user)，头必然有合法 token），签发短时内部调用 token。
    token = _issue_token_for_request(request)
    return InternalHttpClient(token)


def _issue_token_for_request(request: Request) -> str:
    """从当前请求的 Authorization 头取 user，签发短时内部调用 token。

    get_current_user 不写 request.state，所以这里直接从原始请求头解析。
    插件路由必经 Depends(get_current_user)，头必然带合法 Bearer token。
    """
    from core.security import USER_SUB, create_access_token, decode_token

    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise ForbiddenError("内部调用未授权：请求头缺少 Bearer token")
    payload = decode_token(auth[7:].strip(), expected_type="access")
    sub = payload.get("sub", USER_SUB)
    return create_access_token(sub)
