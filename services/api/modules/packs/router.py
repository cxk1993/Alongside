"""整合包管理面（`modules/packs/`，内置模块，`kind=core`）。

★ 六端点、四组语义
------------------
  登记（install / uninstall）—— 只动「已装配包登记表」，不动任何插件启用态
  试装（dry-run）        —— 装配器在 `dry_run=True` 下的**真实走法**，不是另一套模拟
  装配（assemble）        —— 真装：启用成员、迁移表、挂路由；**装完即登记**
  探针（health）          —— 恒 200，免认证

★ 判据不是文档里写的，是测试里断言的：
  登记与试装之后，成员的路由必须**仍是 404**；只有 assemble 之后才变 200。

★ 三条纪律（与 `modules/plugins/router.py` 平行）
  1. 不写 `prefix=`：内核按 `manifest.api.base` 自动挂 `/api/v1/packs`。
  2. 不自己 `include_router`：全局副作用必须登记为 effect，
     `registry.mount(...)` 只许出现在 `core/plugins/discover.py`。
  3. 不整块捕获异常：`PackError` 在这里转成 404/422，其余交给内核统一转 RFC7807。

★ 为什么 `_pack_dir` 必须委托 `packs.roots.resolve_pack_dir`
------------------------------------------------------------
早先路由算 `data_dir / "packs" / <id>`、装配器算 `packs_root / <id>`——
两处各自自洽，合起来才不对：「界面能看见这个包、点装配却找不到」。
⇒ 解析只有一处。本函数只是它的**调用点**，不是第二个答案。
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi import Path as FPath

from core.deps import get_current_user
from core.errors import NotFoundError, ServiceUnavailableError, ValidationError
from core.plugins import get_plugin_manager
from core.security import User
from packs.boot import resolve_data_dir, summarize_reports
from packs.catalog import build_catalog
from packs.model import PackError
from packs.roots import DATA_DIRNAME, resolve_pack_dir
from packs.validate import parse_manifest
from packs.wiring import build_assembler, read_registry, register_pack, unregister_pack

router = APIRouter()

MANIFEST_NAME = "pack.json"

_PACK_ID_DESC = "整合包 id（反向命名空间，如 official.life-tasks）—— 从 GET /packs 列表里取"


# ─────────────────────────── 路径与数据目录 ───────────────────────────


def _pack_dir(data_dir: Path, pack_id: str) -> Path:
    """包 id → 目录。与装配器**同口径**（都走 `packs.roots.resolve_pack_dir`）。

    ★ 找不到时退回 `data_dir/packs/<id>`，**不抛、不返回 None**：
      调用方要用它判「这个包在不在」（404 / `pack_dir_exists`），
      必须永远拿得到一个可判断的路径。

    ★ 为什么不在这里自己拼优先级：`roots` 已经把「显式 > 数据目录 > 发行版」
      钉死了。在这里再拼一遍就等于开出第二个答案。
    """
    resolved = resolve_pack_dir(pack_id, data_dir)
    if resolved is not None:
        return resolved
    return Path(data_dir) / DATA_DIRNAME / pack_id


def _data_dir() -> Path:
    """数据根 = 数据库所在目录（`packs.boot.resolve_data_dir`，唯一真相源）。

    ★ 局部 import：`core.config` 会在导入时读环境，路由模块本身要能在
      不启动应用的情况下被 import（测试只 import `_pack_dir`）。
    """
    from core.config import get_settings  # noqa: PLC0415

    return resolve_data_dir(get_settings().db_path)


def _members_of(pack_dir: Path) -> list[str]:
    """从 `pack.json` 里读全部成员 id（required + optional）。

    ★ 读不出来返回空列表——**不抛**：列表是展示用途，
      一个坏清单不该让整个包目录列表挂掉。
    """
    try:
        raw = json.loads((Path(pack_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    members = raw.get("members") if isinstance(raw, dict) else None
    if not isinstance(members, dict):
        return []
    out: list[str] = []
    for key in ("required", "optional"):
        for item in members.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                out.append(item["id"])
    return out


def _kind_of(pack_dir: Path) -> str:
    """包的来源（official / community / local）——登记表记它，界面「来源」列才对得上。

    ★ 读不出来按 `local`：登记表里这一列是**展示**用途，
      宁可显示「本地」也不该让装配在装配成功之后倒在这里。
    """
    try:
        raw = json.loads((Path(pack_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "local"
    kind = raw.get("kind") if isinstance(raw, dict) else None
    return kind if kind in {"official", "community", "local"} else "local"


def _is_installed(data_dir: Path, pack_id: str) -> bool:
    """「这个包装上了没有」= 它的**成员插件**是否全部在启用态。

    ★ 不能拿**包** id 去查插件表：包不是插件，它根本不在那张表里。
      查不到 ⇒ 所有包都显示「未装上」，无论装没装。
      这是本模块最容易被写错的一行，也是单测「装完能通」抓不到的那类错。
    """
    pack_dir = resolve_pack_dir(pack_id, data_dir)
    if pack_dir is None:
        return False
    members = _members_of(pack_dir)
    if not members:
        # 没声明成员的包没有可装配的东西 —— 不算「装上了」。
        return False
    mgr = get_plugin_manager()
    for member_id in members:
        try:
            info = mgr.get_plugin(member_id)
        except Exception:  # noqa: BLE001 —— 查不到就是没装上，不让列表挂掉
            return False
        if not info.get("enabled"):
            return False
    return True


def _assembler(request: Request, data_dir: Path) -> Any:
    """用**这个 app 的** registry 构造装配器。

    ★ 必须是 `request.app.state.registry`：挂到别的 registry 上，
      报告会显示成功、接口却 404 —— 「每个零件都对、装起来不能用」那类错。
    """
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise ServiceUnavailableError("路由注册表尚未绑定，包层此刻不可用。")
    mgr = get_plugin_manager()
    return build_assembler(
        discover=mgr.discover,
        enable=mgr.enable,
        disable=mgr.disable,
        registry=registry,
        data_dir=data_dir,
    )


def _as_validation(exc: PackError) -> ValidationError:
    """包清单/装配的校验失败 → 422，并把**规则编号**带在 detail 里。

    ★ 带编号不是为了好看：用户要能拿着 `B1-3` 去协议里查这一条。
      只说「包不合法」等于让他自己去猜哪一行错了。
    """
    code = getattr(exc, "code", None)
    suffix = f"（规则 {code}）" if code else ""
    return ValidationError(f"{exc}{suffix}")


def _report_dict(report: Any) -> dict[str, Any]:
    """`LoadReport` → 可序列化摘要（形状由包层定，见 `boot.summarize_reports`）。"""
    return summarize_reports([report])[0]


# ─────────────────────────── 六个端点 ───────────────────────────


@router.get("/health")
def health() -> dict[str, bool]:
    """包层健康探针（**免认证**、恒 200）。

    语义是「这个子系统活着」，不是「包都装好了」——
    带认证的探针，监控要么存凭据、要么拉不到；包层挂了也不该让整个服务 5xx。
    """
    return {"ok": True}


@router.get("")
def list_packs(_user: User = Depends(get_current_user)) -> dict[str, Any]:  # noqa: B008
    """列出所有包，三态分开：`registered` / `installed` / `pack_dir_exists`。"""
    data_dir = _data_dir()
    rows = build_catalog(
        data_dir,
        read_registry(data_dir),
        is_installed=lambda pid: _is_installed(data_dir, pid),
    )
    return {"packs": rows, "count": len(rows)}


@router.post("/install")
def install_pack(
    body: dict[str, Any] = Body(...),
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """登记一个包（**只进登记表**，不启用任何成员）。

    body: `{"path": "<包目录>"}`；包 id 以 `pack.json` 为准（不接受覆盖——
    两者不一致正是「装的不是你以为的那个包」）。
    """
    raw_path = body.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValidationError('install 需要 body: {"path": "<包目录>"}')

    pack_path = Path(raw_path).expanduser()
    with contextlib.suppress(OSError):  # pragma: no cover —— 解析失败按「不是包目录」处理
        pack_path = pack_path.resolve()
    if not (pack_path / MANIFEST_NAME).is_file():
        raise ValidationError(f"{pack_path} 不是包目录：缺少 {MANIFEST_NAME}。")

    try:
        manifest_raw = json.loads((pack_path / MANIFEST_NAME).read_text(encoding="utf-8"))
        manifest = parse_manifest(manifest_raw, directory=pack_path)
    except ValueError as exc:
        raise ValidationError(f"{pack_path / MANIFEST_NAME} 不是合法 JSON：{exc}") from exc
    except PackError as exc:
        raise _as_validation(exc) from exc

    data_dir = _data_dir()
    entries = register_pack(
        data_dir,
        pack_id=manifest.id,
        path=str(pack_path),
        source=manifest.kind,
    )
    return {
        "registered": True,
        "id": manifest.id,
        "path": str(pack_path),
        "count": len(entries),
    }


@router.post("/{pack_id}/dry-run")
def dry_run_pack(
    pack_id: Annotated[str, FPath(description=_PACK_ID_DESC)],
    request: Request,
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """试装：**只算不装**。返回的报告与真装逐字段可比。"""
    data_dir = _data_dir()
    pack_dir = _pack_dir(data_dir, pack_id)
    if not (pack_dir / MANIFEST_NAME).is_file():
        raise NotFoundError(f"未找到整合包 {pack_id}（查找位置：{pack_dir}）。")

    try:
        report = _assembler(request, data_dir).load(pack_dir, dry_run=True)
    except PackError as exc:
        raise _as_validation(exc) from exc
    return {"dry_run": True, "pack": _report_dict(report)}


@router.post("/{pack_id}/assemble")
def assemble_pack(
    pack_id: Annotated[str, FPath(description=_PACK_ID_DESC)],
    request: Request,
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """装配：**真装**——启用成员、迁移表、挂路由；装成功即登记。

    ★ 装配失败（成员缺版本 / 摘要不符）不抛 5xx：
      返回 200 + 逐成员报告 + `assembled: false`。
      用户要的是「每个成员到底怎么了」，一个 4xx 会把这份报告吞掉。
      只有**清单本身不合法**才 422（那时根本没有可追问的成员结论）。
    """
    data_dir = _data_dir()
    pack_dir = _pack_dir(data_dir, pack_id)
    if not (pack_dir / MANIFEST_NAME).is_file():
        raise NotFoundError(f"未找到整合包 {pack_id}（查找位置：{pack_dir}）。")

    try:
        report = _assembler(request, data_dir).load(pack_dir, dry_run=False)
    except PackError as exc:
        raise _as_validation(exc) from exc

    registered = False
    if report.ok:
        # ★ 装配即登记：装成功了用户才「装过它」。反过来说，
        #   没有真装上的包不进登记表——否则重启后会被自动重装一遍失败的那个。
        register_pack(
            data_dir,
            pack_id=report.pack_id or pack_id,
            path=str(pack_dir),
            source=_kind_of(pack_dir),
        )
        registered = True

    return {
        "assembled": report.ok,
        "registered": registered,
        "pack": _report_dict(report),
    }


@router.post("/{pack_id}/uninstall")
def uninstall_pack(
    pack_id: Annotated[str, FPath(description=_PACK_ID_DESC)],
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """卸包：**只移登记表**，不动成员启用态（停成员是插件管理页的事）。"""
    data_dir = _data_dir()
    if not unregister_pack(data_dir, pack_id):
        raise NotFoundError(f"整合包 {pack_id} 不在登记表里。")
    return {
        "unregistered": True,
        "id": pack_id,
        "count": len(read_registry(data_dir)),
    }
