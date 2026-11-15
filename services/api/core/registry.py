"""路由挂载注册表（最小骨架，留给 T14 顶替）。

★ 铁律：必须有 unmount —— 否则"插件可禁用"是空话。
  内核只负责"把 router 挂上去/摘下来"，具体发现/生命周期/权限归 T14。

形状对齐投喂包要求的 RouterHost(Protocol)：
    mount(plugin_id, router, prefix) / unmount(plugin_id) / mounted() -> list[str]
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter, Depends, FastAPI, Request


def _scope_dependency(plugin_id: str) -> Any:
    """造一个「写入数据访问作用域」的路由级依赖。

    ★ 为什么放在**路由级**（`include_router(dependencies=[...])`）
      而不是每个端点自己加：
      一处挂载 ⇒ 全部端点受益。插件作者不需要知道这件事，
      也不会有人漏写（漏写 = 该插件的请求没有边界）。
      ★ 而「每个端点自己加依赖」正是最容易被漏的写法。

    ★ 写进 `request.state`：`get_db` 从那里读作用域。
      （不用 contextvar：实测它跨不过 anyio 的 context 切换。）

    ★ 内核自己的路由不经过这里 ⇒ 无作用域 ⇒ 不受边界约束。
    """

    def _set_scope(request: Request) -> None:
        request.state.db_scope = plugin_id

    return _set_scope


@runtime_checkable
class RouterHost(Protocol):
    def mount(self, plugin_id: str, router: APIRouter, prefix: str) -> None: ...

    def unmount(self, plugin_id: str) -> None: ...

    def mounted(self) -> list[str]: ...


class ModuleRegistry:
    """内核级路由挂载表。

    - mount：把模块的 APIRouter 挂到 app 上，prefix 由 manifest.api.base 决定。
    - unmount：把该模块挂载产生的所有路由摘掉（支持"禁用"）。
    - mounted：返回当前已挂载的模块 id 列表。
    """

    def __init__(self) -> None:
        self._app: FastAPI | None = None
        self._mounted: dict[str, list[Any]] = {}  # plugin_id -> 它产生的 route 对象

    def bind(self, app: FastAPI) -> None:
        self._app = app

    def mount(
        self, plugin_id: str, router: APIRouter, prefix: str, scoped: bool = True
    ) -> None:
        """把模块的 APIRouter 挂到 app 上，prefix 由 manifest.api.base 决定。

        ★ `scoped`（2026-11-09 新增）：要不要给这些路由挂**数据作用域依赖**。

        | kind | scoped | 为什么 |
        |:--:|:--:|---|
        | `third-party` | ✔ True | 外来者，只该碰自己的表 |
        | `builtin` / `core` | ✘ False | 它们是系统的一部分；模块之间本来就有跨模块读（dashboard 读 `calendar_event`、finance 读 `habits_habit`、catalog 读 `web_entry`…），给它们上作用域会把这些**正常功能**全部拦死 |

        ★ 这个参数就是「数据边界只约束外来者」这句话的代码形态。
          没有它，`include_router(dependencies=[scope])` 会一视同仁地挂在所有模块上，
          而内核与内置模块的路由请求也会被设上作用域——那是误伤，不是安全。
        """
        if self._app is None:
            raise RuntimeError("ModuleRegistry 未绑定 app，无法挂载")
        if plugin_id in self._mounted:
            raise RuntimeError(f"模块已挂载：{plugin_id}")
        before = len(self._app.routes)
        deps = [Depends(_scope_dependency(plugin_id))] if scoped else []
        try:
            self._app.include_router(
                router,
                prefix=prefix,
                tags=[plugin_id],
                dependencies=deps,
            )
        except BaseException:
            # ★ ADR-0005（2026-10-03）：include_router 可能**部分写入**后才抛
            #   （prefix 冲突、依赖注入签名错误等）。若就此上抛，这些路由已进
            #   app.routes 却没人登记 → 成为**永远无法 unmount 的孤儿路由**，
            #   "插件可禁用"被击穿。故此处先按对象身份摘掉本次写入的片段再上抛，
            #   保证 mount 失败后 app.routes 与调用前逐元素等价（可逆性）。
            added = self._app.routes[before:]
            if added:
                owned = {id(r) for r in added}
                self._app.router.routes = [
                    r for r in self._app.router.routes if id(r) not in owned
                ]
            raise
        added = self._app.routes[before:]
        self._mounted[plugin_id] = added
        self._invalidate_openapi()

    def unmount(self, plugin_id: str) -> None:
        if self._app is None:
            raise RuntimeError("ModuleRegistry 未绑定 app，无法卸载")
        routes = self._mounted.pop(plugin_id, None)
        if routes is None:
            return
        owned = set(id(r) for r in routes)
        # 摘掉该模块产生的路由（用对象身份，不影响其它模块）。
        # 注意：Starlette 的 `app.routes` 是只读 property，要改必须落到 `app.router.routes`。
        self._app.router.routes = [r for r in self._app.router.routes if id(r) not in owned]
        self._invalidate_openapi()

    def _invalidate_openapi(self) -> None:
        """路由表变了 ⇒ 让 OpenAPI 快照失效（r5 热插拔 · 层3.3）。

        ★ `FastAPI.openapi()` 首次调用后把结果缓存在 `app.openapi_schema`，
          之后**不再看路由表**。而热插拔会在运行期改 `app.router.routes` ⇒
          `/openapi.json`、`/api/docs`、`/api/v1/<id>/openapi.json` 全是旧快照：
          新装的插件在文档里不存在，卸载的插件还挂在文档上。
        ★ 放在注册表这一处（而不是 manager 的各个调用点）：
          路由表的**唯一写入者**就是本类，挂/摘两条路都经过这里 ⇒
          结构上不可能漏掉某条生命周期路径。
        """
        if self._app is not None:
            self._app.openapi_schema = None

    def mounted(self) -> list[str]:
        return list(self._mounted.keys())

    def invalidate_openapi(self) -> None:
        """公开入口：让 OpenAPI 快照失效（`mount`/`unmount` 内部已自动调，见 `_invalidate_openapi`）。

        ★ 为什么还要有公开版（r8）：`reload` 走的是「先撤后装」，两条路各自都会
          触发一次失效——但如果插件当前**没有挂载**（例如 FAILED 态升上来），
          两条路都不会跑，而 `/api/v1/modules` 里的 manifest 已换成新版本
          （响应模型可能变了）⇒ 快照仍是旧的。调用方显式清一次才是「不管
          这一轮有没有挂载动作，文档都得对上磁盘」。
        """
        self._invalidate_openapi()

    # ── 注册表同步（r5 热插拔 · 层3.4）──────────────────────────────
    def register_module(self, plugin_id: str, manifest: dict[str, Any]) -> None:
        """把模块登记进 `app.state.modules`（`/api/v1/modules` 的数据源）。

        ★ 为什么需要它：`app.state.modules` 原先**只在 create_app 里写**
          （内置全量登记 + 第三方启动恢复），运行期 enable/install 的插件
          进不去 ⇒ `GET /api/v1/modules` 看不到它（前端点不亮图标），
          `/api/v1/<id>/openapi.json` 直接 404（那里查的就是这张表）。
          于是 `/plugins` 说「它在跑」、`/modules` 说「没有这个模块」——
          两个对外端点口径不一致，而前端两边都在读。

        ★ 为什么放在注册表而不是 manager：注册表已经持有 app 引用，
          且它本来就是「挂载表」的宿主。让 manager 去摸 `app.state`
          等于把 FastAPI 的知识扩散到编排层。这里只暴露两个窄动作。
        ★ 幂等：重复登记同一 id 覆盖为最新 manifest（版本可能变了）。
        """
        if self._app is None:
            return
        modules = getattr(self._app.state, "modules", None)
        if modules is None:
            modules = {}
            self._app.state.modules = modules
        modules[plugin_id] = manifest

    def unregister_module(self, plugin_id: str) -> None:
        """从 `app.state.modules` 摘掉模块（disable / uninstall 时）。

        ★ 与 `unmount` 分开两个动作，而不是合并：
          「路由在不在」与「清单里有没有」是两件事 ——
          事件激活型插件可能已登记但尚未挂路由。合并会让那种状态无法表达。
        ★ 幂等：不存在时静默返回。
        """
        if self._app is None:
            return
        modules = getattr(self._app.state, "modules", None)
        if isinstance(modules, dict):
            modules.pop(plugin_id, None)

    # 兼容 Protocol 命名（也允许属性访问）
    def __call__(self) -> ModuleRegistry:
        return self
