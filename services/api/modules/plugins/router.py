"""插件管理 API（T14 对外暴露的管理面）。

★ 不写 prefix=：内核按 manifest.api.base 自动挂 /api/v1/plugins。
★ 不自己捕获异常：内核统一转 RFC7807（manager 抛出的 AppError 自动变 problem+json）。
★ 每个端点都要 Request 注解（不能写 Any，否则会被当成必填查询参数 → 422）。

端点（对照总纲 §1.3 契约，路径前缀 /api/v1/plugins）：
  GET    /                     全部插件：id/名称/版本/类型/状态/扩展点/权限
  GET    /{id}                单个插件详情（含 manifest 原文、错误）
  POST   /{id}/enable         启用（幂等）
  POST   /{id}/disable        禁用（幂等；core 不可禁用）
  POST   /install             安装第三方插件（body: {"id": "..."}）
  POST   /{id}/uninstall      卸载（仅 third-party；必须清理干净）
  GET    /{id}/settings       读设置
  PATCH  /{id}/settings       写设置（按 settingsSchema 校验）
  GET    /{id}/health         单插件健康
  GET    /slots/{slotName}    某扩展点的全部贡献
  POST   /{id}/reload         原地升级（真 reload，不重启进程；?dry_run=true 只算差异）
  POST   /reload              批量：缺省只算差异（dry-run），body {ids, dry_run:false} 才真重载
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, File, Query, Request, UploadFile
from fastapi import Path as FPath
from typing import Annotated

from core.deps import get_current_user
from core.errors import ValidationError
from core.plugins import get_plugin_manager
from core.security import User

router = APIRouter()

MGR = get_plugin_manager()


def _plugins_data_root() -> Any:
    """运行时安装的插件落点：`data_dir/plugins`（r5 热插拔 · 层1.1）。

    ★ 路径解析只有一处（发现层的 `default_data_plugins_root`），
      这里直接复用 —— 上传落到 A、发现扫 B 是最难查的一类错
      （「装成功了但列表里没有」）。
    ★ 回退：发现层可能还没实现该函数（并行开发期）⇒ 用 db 路径推数据根。
      宁可退回一个确定的约定，也不要让端点整个 500。
    """
    from core.plugins import discover as _discover

    fn = getattr(_discover, "default_data_plugins_root", None)
    if callable(fn):
        return fn()
    from core.config import get_settings
    from packs.boot import resolve_data_dir

    return resolve_data_dir(get_settings().db_path) / "plugins"


def _registry(request: Request) -> Any:
    reg = getattr(request.app.state, "registry", None)
    if reg is None:
        raise RuntimeError("插件管理无法访问路由注册表（registry 未绑定）")
    return reg


@router.get("/health")
def health() -> dict[str, bool]:
    """插件子系统健康探针（恒 200）。"""
    return {"ok": True}


@router.get("")
def list_plugins(_user: User = Depends(get_current_user)) -> dict[str, Any]:  # noqa: B008
    """列出所有插件（含内核内置 / 第三方），带 `count`。"""
    plugins = MGR.list_plugins()
    return {"plugins": plugins, "count": len(plugins)}


@router.get("/{plugin_id}")
def get_plugin(
    plugin_id: Annotated[str, FPath(description="插件 id（目录名，如 calendar / pi-agent）—— 从 GET /plugins 列表里取")],
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:  # noqa: B008
    """读单个插件的完整清单与运行态（启用状态 / 权限 / 设置 schema）。"""
    return MGR.get_plugin(plugin_id)


@router.post("/{plugin_id}/enable")
def enable_plugin(
    plugin_id: Annotated[str, FPath(description="插件 id（目录名，如 calendar / pi-agent）—— 从 GET /plugins 列表里取")],
    request: Request,
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """启用一个插件（登记表生效 + 挂载它的路由）。⚠️ 只有**已安装**的插件才能启用。"""
    return MGR.enable(plugin_id, _registry(request))


@router.post("/{plugin_id}/disable")
def disable_plugin(
    plugin_id: Annotated[str, FPath(description="插件 id（目录名，如 calendar / pi-agent）—— 从 GET /plugins 列表里取")],
    request: Request,
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """停用插件（摘掉它的路由与能力面；**数据与设置都保留**）。"""
    result = MGR.disable(plugin_id, _registry(request))
    # 禁用的插件同时清掉激活器的 pending 登记，
    # 防止事件命中把已禁用插件重新挂回路由（击穿 disable 语义）。
    activator = getattr(request.app.state, "activator", None)
    if activator is not None:
        activator.clear_pending(plugin_id)
    return result


@router.post("/install")
def install_plugin(
    request: Request,
    body: dict[str, Any] = Body(...),
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """安装插件（`plugin_id` 取自上传包解析出的标识）。⚠️ 安装 ≠ 启用。"""
    plugin_id = body.get("id")
    if not plugin_id:
        raise ValidationError("install 需要 body: {\"id\": \"<插件id>\"}")
    return MGR.install(plugin_id, _registry(request))


@router.post("/upload")
async def upload_plugin(
    request: Request,
    file: Annotated[UploadFile, File(description="插件 zip 包（含 manifest.json）")],
    _user: User = Depends(get_current_user),  # noqa: B008
    activate: bool = Query(
        default=False,
        description="落地后是否立即 install+enable（默认 false：只落地，等显式 /install）",
    ),
) -> dict[str, Any]:
    """上传并落地一个第三方插件包（zip）→ 返回解析出的 id。

    ★ 分两步（upload 只落地、install 才激活）**不是**多余的仪式：
      「文件到了磁盘」与「代码开始在这个进程里跑」是两件风险完全不同的事。
      分开放，管理员能在激活前先看一眼包内容（`GET /plugins` 会列出未启用的它）。
      `?activate=true` 是给「我知道我在装什么」的场景留的一条快捷路径。

    ★ 鉴权：`get_current_user`（单用户系统即 admin）。这是**明示信任**的入口 ——
      装一个插件等价于允许它的 JS 以管理员身份跑（见 core/plugins/assets.py 的信任模型）。

    ★ 校验链全在 `core/plugins/package.py`（尺寸/形状/契约/落地四道闸），
      本端点只负责：收字节 → 交给它 → 按结果回话。校验逻辑放路由层会长在这里，
      而它需要被测试单独调用（不起 app）。
    """
    from core.plugins.package import unpack_plugin_zip

    archive = await file.read()
    data_root = _plugins_data_root()
    result = unpack_plugin_zip(archive, data_root)

    payload: dict[str, Any] = {
        "id": result.plugin_id,
        "directory": str(result.directory),
        "installed": False,
        "enabled": False,
    }
    if activate:
        # ★ 激活失败要**留住落地结果**：包已经在磁盘上、清单可见，
        #   只是没跑起来 —— 这条信息（installed=true, enabled=false, error=…）
        #   比「整个请求失败、但包其实已经在磁盘上」诚实得多。
        try:
            MGR.install(result.plugin_id, _registry(request))
            payload["installed"] = True
            payload["enabled"] = True
        except Exception as exc:  # noqa: BLE001
            payload["error"] = f"{type(exc).__name__}: {exc}"
    return payload


@router.post("/{plugin_id}/uninstall")
def uninstall_plugin(
    plugin_id: Annotated[str, FPath(description="插件 id（目录名，如 calendar / pi-agent）—— 从 GET /plugins 列表里取")],
    request: Request,
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """卸载插件（**先停用再卸载**；插件数据保留，不会被删）。"""
    return MGR.uninstall(plugin_id, _registry(request))


@router.get("/{plugin_id}/settings")
def get_settings(
    plugin_id: Annotated[str, FPath(description="插件 id（目录名，如 calendar / pi-agent）—— 从 GET /plugins 列表里取")],
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """读某个插件的设置（结构按它的 `settings.schema.json`）。"""
    return {"plugin_id": plugin_id, "settings": MGR.get_settings(plugin_id)}


@router.patch("/{plugin_id}/settings")
def patch_settings(
    plugin_id: Annotated[str, FPath(description="插件 id（目录名，如 calendar / pi-agent）—— 从 GET /plugins 列表里取")],
    body: dict[str, Any] = Body(...),
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """改某个插件的设置（按它自己的 `settings.schema.json` 校验；**部分更新**）。"""
    return {"plugin_id": plugin_id, "settings": MGR.set_settings(plugin_id, body)}


@router.get("/{plugin_id}/health")
def plugin_health(
    plugin_id: Annotated[str, FPath(description="插件 id（目录名，如 calendar / pi-agent）—— 从 GET /plugins 列表里取")],
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """问某个插件的健康状态 —— 内核据此判断「该能力此刻是否可用」。"""
    return MGR.health(plugin_id)


@router.get("/slots/{slot_name}")
def slot_contributions(
    slot_name: Annotated[
        str, FPath(description="插槽名（如 dashboard / sidebar）—— 前端按插槽组装一屏")
    ],
    _user: User = Depends(get_current_user),
    include_disabled: bool = Query(
        default=False,
        description="是否包含已停用的贡献（管理界面用；渲染路径不要开）",
    ),
) -> dict[str, Any]:
    """列出挂在某个**插槽（slot）**上的全部贡献 —— 前端据此组装一屏。

    ★ 默认**只返回启用中的插件**（r5 · 层0.4）：停用插件的 API 已 404，
      把它列进插槽只会渲染出「点了没反应」的空卡片。
      `include_disabled=true` 给管理界面留全量视图。
    """
    return {
        "slot": slot_name,
        "contributions": MGR.slots(slot_name, include_disabled=include_disabled),
    }


@router.post("/{plugin_id}/reload")
def reload_plugin(
    plugin_id: Annotated[str, FPath(description="插件 id（目录名）—— 从 GET /plugins 列表里取")],
    request: Request,
    _user: User = Depends(get_current_user),  # noqa: B008
    dry_run: bool = Query(
        default=False,
        description="true = 只报告会动什么，不执行（先看后动）",
    ),
) -> dict[str, Any]:
    """原地升级一个插件（真 reload，不重启进程）。

    ★ 撤销走既有 effect 逆序回放（`kernel.replay_runtime`），代码重新 exec，
      数据与迁移台账**保留**。失败任一步 → 插件落 FAILED 并留 last_error
      （绝不半提交；失败后再修代码、再 reload/enable 即可恢复）。

    ★ `dry_run=true`：只算差异（当前阶段、可否升级、将走的阶段序列），
      不撤任何 effect —— 给管理面「先看后动」。
    """
    reg = _registry(request)
    return MGR.reload(plugin_id, reg, dry_run=dry_run)


@router.post("/reload")
def reload_plugins(
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """批量重载入口。

    ★ 语义分两半（r8 起）：
      · **dry-run（缺省）**：保持 r5 之前的行为 —— 只扫「磁盘与运行时的差异」，
        返回 `reload_required`（需要重启/重载才能生效的插件清单）。
        这是管理面「先看后动」的只读视图，不做任何事。
      · **真重载**：body 里给 `{"ids": [...], "dry_run": false}` 时，
        对指定插件逐个执行真 reload（复用单插件入口）。ids 缺省 = 全部
        **可重载**（ACTIVE/FAILED）的插件。

    ★ 为什么缺省仍是 dry-run：不带参数的 POST /reload 是既有调用方
      （前端 / MCP 面）的存量入口，把「扫差异」悄悄变成「重启所有插件」
      是一次静默的语义升级——那类升级必须显式，否则第一次误触就是全量停机。
    """
    reg = _registry(request)
    body = body or {}
    dry_run = bool(body.get("dry_run", True))
    ids = body.get("ids")

    # ── 差异视图（两个模式都返回，供调用方对照）───────────────────────
    mounted = set(reg.mounted())
    diffs: list[dict[str, Any]] = []
    reloadable_ids: list[str] = []
    for item in MGR.list_plugins():
        pid = item.get("id")
        if not pid:
            continue
        enabled = item.get("enabled", True)
        is_mounted = pid in mounted
        if enabled != is_mounted:
            diffs.append(
                {"id": pid, "enabled": enabled, "mounted": is_mounted, "needs_restart": True}
            )
        if item.get("phase") in ("ACTIVE", "FAILED") and item.get("kind") != "core":
            reloadable_ids.append(pid)

    if dry_run:
        return {
            "dry_run": True,
            "reload_required": diffs,
            "count": len(diffs),
            "reloadable": reloadable_ids,
        }

    target_ids = [str(i) for i in ids] if ids else reloadable_ids
    results: list[dict[str, Any]] = []
    for pid in target_ids:
        try:
            out = MGR.reload(pid, reg, dry_run=False)
            results.append({"id": pid, "ok": True, "phase": out.get("phase")})
        except Exception as exc:  # noqa: BLE001 —— 单个失败不影响其余（与启动恢复同口径）
            results.append({"id": pid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "dry_run": False,
        "reload_required": diffs,
        "applied": results,
        "count": len(results),
    }
