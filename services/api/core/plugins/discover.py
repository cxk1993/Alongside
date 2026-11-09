# ── 三层校验的串接点 ──
"""插件发现：扫描内置(services/api/modules/*) + 第三方(plugins/*) 的 manifest（总纲 §1.3）。

★ 加载器对两者一视同仁——除了 uninstall 只对 third-party 开放（见 manager）。
本模块负责"找到 + 校验 + 标记来源"，并提供**统一挂载入口** mount_plugin()：
幂等检查 + 统一路由加载 + 委托 ModuleRegistry 落挂。前置小步——
启动全量挂载（create_app）与运行时启停（enable/install）共用同一入口，
不再各写一份。

校验三层：
  1. JSON 合法；
  2. 对照 contracts/plugin.schema.json 强校验（精确到字段）；
  3. kernelApi 与当前内核接口版本兼容（不兼容明确拒绝，不许静默）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.errors import ManifestError
from core.manifest import load_router
from core.plugins.crosscheck import crosscheck_manifests
from core.plugins.validate import validate_manifest

_API_ROOT = Path(__file__).resolve().parents[2]  # services/api/
_DEFAULT_MODULES_DIR = _API_ROOT / "modules"
_DEFAULT_PLUGINS_DIR = _API_ROOT.parent.parent / "plugins"  # 项目根/plugins


@dataclass(frozen=True)
class PluginInfo:
    """一个被发现、且通过校验的插件。"""

    id: str
    kind: str  # core | builtin | third-party
    source: str  # "builtin"(modules/) | "third-party"(plugins/)
    manifest: dict[str, Any]
    directory: Path
    compat_status: str = "ok"  # "ok" | "degraded" | "failed"


@dataclass(frozen=True)
class PluginError:
    """一个被发现、但校验失败的插件（id 可能拿不到）。"""

    directory: Path
    source: str
    message: str


class DiscoveryResult:
    def __init__(
        self, plugins: list[PluginInfo], errors: list[PluginError]
    ) -> None:
        self.plugins = plugins
        self.errors = errors

    @property
    def ok(self) -> bool:
        return not self.errors


def _read_manifest(manifest_path: Path, source: str) -> tuple[dict[str, Any], PluginError | None]:
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return {}, PluginError(manifest_path.parent, source, f"manifest 不是合法 JSON：{exc}")
    except OSError as exc:
        return {}, PluginError(manifest_path.parent, source, f"读取 manifest 失败：{exc}")

    if not isinstance(raw, dict):
        return {}, PluginError(manifest_path.parent, source, "manifest 根必须是对象")

    try:
        validate_manifest(raw)
    except Exception as exc:  # validate_manifest 抛 ManifestError；也兜底其它
        return raw, PluginError(manifest_path.parent, source, f"契约校验失败：{exc}")

    pid = raw["id"]
    dir_name = manifest_path.parent.name
    if pid != dir_name:
        return raw, PluginError(
            manifest_path.parent, source,
            f"manifest.id={pid!r} 必须等于目录名 {dir_name!r}",
        )

    # ★ G1：minKernel 校验（内核版本不满足时降级，不拒绝）
    min_kernel = raw.get("minKernel")
    if min_kernel:
        # ★ 2026-09-25 修：minKernel 是"最低版本"语义（>=），不是 caret/精确。
        #   此前误用 satisfies() → 所有 minKernel="0.1.0" 的插件在 1.0.0 内核上
        #   一律 degraded → 跳过激活 → 路由不挂载（踩到）。
        from core.plugins.version import KERNEL_API_VERSION, meets_min_kernel
        try:
            if not meets_min_kernel(min_kernel):
                raw["_compat_status"] = "degraded"
                raw["_compat_reason"] = f"minKernel={min_kernel} 不满足，当前 {KERNEL_API_VERSION}"
        except Exception:
            raw["_compat_status"] = "degraded"
            raw["_compat_reason"] = f"minKernel={min_kernel} 校验失败"

    # ★ G2：kernelApi 不兼容时降级而非 fail-fast（台账 #026 要求：个人系统一插件坏不应带崩全坞）
    try:
        from core.plugins.version import check_compatibility
        check_compatibility(raw["kernelApi"])
    except Exception as exc:
        raw["_compat_status"] = "degraded"
        raw["_compat_reason"] = f"kernelApi 不兼容：{exc}"

    return raw, None


def _scan(root: Path, source: str) -> tuple[list[PluginInfo], list[PluginError]]:
    plugins: list[PluginInfo] = []
    errors: list[PluginError] = []
    if not root.is_dir():
        return plugins, errors
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest_path = sub / "manifest.json"
        if not manifest_path.is_file():
            continue
        raw, err = _read_manifest(manifest_path, source)
        if err is not None:
            errors.append(err)
            continue
        plugins.append(
            PluginInfo(
                id=raw["id"],
                kind=raw["kind"],
                source=source,
                manifest=raw,
                directory=sub,
                compat_status=raw.get("_compat_status", "ok"),
            )
        )
    return plugins, errors


def discover_plugins(
    modules_dir: Path | None = None,
    plugins_dir: Path | None = None,
) -> DiscoveryResult:
    """扫描内置与第三方插件，返回通过校验的插件列表 + 失败清单。

    ★ 校验三层（协议报告 §19）：
      ① 结构校验   —— core/plugins/validate.py（单插件形状）
      ② 跨字段一致性 —— 同上（单插件内部矛盾）
      ③ 跨插件一致性 —— core/plugins/crosscheck.py（需全量插件，故在此处统一跑）
    ★ ③ 必须等全量扫描结束才能判：「这个能力有没有人 provides / defines」
      的答案可能出现在扫描顺序的后面。
    """
    root = Path(modules_dir) if modules_dir else _DEFAULT_MODULES_DIR
    plug = Path(plugins_dir) if plugins_dir else _DEFAULT_PLUGINS_DIR
    builtins, b_errs = _scan(root, "builtin")
    third_party, t_errs = _scan(plug, "third-party")
    plugins = builtins + third_party

    # ── ③ 跨插件一致性（规则 12 / 13）──
    #   ★ 违反时不丢弃任何插件：能力重复/无定义是**系统级**问题，
    #   把出错的那个插件单独剔掉会让系统静默地少一块能力，比报错更糟。
    if plugins:
        violations = crosscheck_manifests([p.manifest for p in plugins])
        if violations:
            raise ManifestError(
                "跨插件一致性校验失败：\n  - " + "\n  - ".join(violations)
            )

    return DiscoveryResult(plugins=plugins, errors=b_errs + t_errs)


def load_python_module_from_file(path: Path, module_name: str) -> Any:
    """按文件路径加载一个 .py 模块（用于第三方插件的 router / models，不走包 import）。"""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载插件模块：{path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001 — 插件自己的代码出错，一律归成「插件加载失败」
        # ★ 为什么必须包一层：插件代码抛什么（ModuleNotFoundError、SyntaxError、
        #   业务异常……）内核管不了，但**对外必须是一个统一的应用级错误**。
        #   否则调用方要区分「插件坏了」和「内核坏了」，而后者根本不是。
        #   原始异常类型与信息都带在消息里，不丢诊断线索。
        raise ManifestError(
            f"插件模块加载失败：{path}（{type(exc).__name__}: {exc}）"
        ) from exc
    return mod


def load_plugin_router(info: PluginInfo) -> Any:
    """加载某插件的 APIRouter。

    builtin/core：走内核的包 import（modules.<id>.router）。
    third-party：按文件路径加载（plugins/<id>/api/router.py）。
    """
    if info.source == "third-party":
        router_path = info.directory / "api" / "router.py"
        if not router_path.is_file():
            raise ManifestError(f"插件「{info.id}」缺少 api/router.py")
        mod = load_python_module_from_file(
            router_path, f"plugin_thirdparty_{info.id}_router"
        )
        router = getattr(mod, "router", None)
        if router is None:
            raise ManifestError(f"插件「{info.id}」router.py 未定义 router = APIRouter()")
        return router
    # builtin / core：复用内核的包 import 逻辑（与 core.manifest.load_router
    # 同一实现，不再各写一份；对 modules/ 下的模块两者逐字等价）
    return load_router(info.id)


def mount_plugin(info: PluginInfo, registry: Any) -> None:
    """统一挂载入口（前置小步）：幂等 + 统一路由加载 + 委托挂载。

    启动全量挂载（create_app）与运行时启停（PluginManager.enable/install）
    共用本入口。已挂载时静默返回（幂等），重复调用安全。
    """
    if info.id in registry.mounted():
        return
    router = load_plugin_router(info)
    registry.mount(info.id, router, prefix=info.manifest["api"]["base"])
