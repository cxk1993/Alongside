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

from core.logging import get_logger
from core.errors import ManifestError
from core.manifest import load_router
from core.plugins.crosscheck import crosscheck_detailed
from core.plugins.validate import validate_manifest

log = get_logger("kernel.plugins.discover")

_API_ROOT = Path(__file__).resolve().parents[2]  # services/api/
_DEFAULT_MODULES_DIR = _API_ROOT / "modules"
_DEFAULT_PLUGINS_DIR = _API_ROOT.parent.parent / "plugins"  # 项目根/plugins

#: 用户数据里的插件根子目录名（相对数据目录）。
#: ★ 与包层 `packs/roots.py` 的 `DATA_DIRNAME="packs"` 对称：
#:   发行版自带的东西在仓库里（只读），用户自己装的东西在数据目录里（可写）。
#:   容器部署只挂 `./data`，装进仓库根的东西重启即丢 —— 这条对称不是美学，
#:   是「装上去的插件能活过重启」的前提。
DATA_DIRNAME = "plugins"


def data_plugins_root(data_dir: Path) -> Path:
    """用户数据里的插件根（`<data_dir>/plugins/`）——运行时安装的落点。"""
    return Path(data_dir) / DATA_DIRNAME


def default_data_plugins_root() -> Path:
    """默认数据插件根：数据根 = 数据库所在目录（与包层同一真相源）。

    ★ 为什么不新增 `PLUGINS_DIR` 配置项：`packs.boot.resolve_data_dir` 的
      理由同样适用 —— 数据库/备份/日志/包/插件本来就在同一个 `data/` 下，
      各配各的迟早分叉（改了 DB_PATH 忘了改插件目录 ⇒ 插件在一个地方、
      状态在另一个地方）。⇒ 一个真相源。
    """
    from core.config import get_settings
    from packs.boot import resolve_data_dir

    return data_plugins_root(resolve_data_dir(get_settings().db_path))


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

    # ★ kind ↔ 来源一致性（r5 热插拔 · 层1.5）
    #   此前内核不校验这两者是否自洽：把一个 `kind=builtin` 的目录丢进
    #   plugins/ 下，`source` 仍是 third-party，但 install()/uninstall()
    #   都按 kind 拒掉它 ⇒ **既装不了也卸不掉**，只能靠 enable 挂上，
    #   成为一条没人管的缝。热插拔必须堵住它：外来目录只许自称 third-party。
    #   （container 允许：容器型是纯前端编排，放在用户目录里同样合法。）
    if source == "third-party" and raw["kind"] in ("core", "builtin"):
        return raw, PluginError(
            manifest_path.parent, source,
            f"第三方插件目录里的 manifest.kind={raw['kind']!r} 不合法："
            "core/builtin 随系统发布，只能住在 services/api/modules/；"
            "放在插件根下的插件必须声明 kind='third-party'（或 container）",
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
    *,
    data_plugins_dir: Path | None = None,
) -> DiscoveryResult:
    """扫描内置与第三方插件，返回通过校验的插件列表 + 失败清单。

    ★ 校验三层（协议报告 §19）：
      ① 结构校验   —— core/plugins/validate.py（单插件形状）
      ② 跨字段一致性 —— 同上（单插件内部矛盾）
      ③ 跨插件一致性 —— core/plugins/crosscheck.py（需全量插件，故在此处统一跑）
    ★ ③ 必须等全量扫描结束才能判：「这个能力有没有人 provides / defines」
      的答案可能出现在扫描顺序的后面。

    ★★ 三根发现（r5 热插拔 · 层1.1）：
      | 根 | source | 可写性 | 用途 |
      |---|---|---|---|
      | `services/api/modules/` | builtin | 只读（随系统发布） | 内置模块 |
      | `<data_dir>/plugins/` | third-party | **可写** | 运行时安装的落点 |
      | 仓库根 `plugins/` | third-party | 只读兼容 | 发行版自带 / 开发者手放 |
      数据根**优先**：同一个 id 在两处都有时，用户自己装的那份赢
      （与 `packs.roots.resolve_pack_dir` 同口径）。仓库根那份被跳过并
      留一条 info 日志 —— 静默丢弃会让「我明明放了却没生效」变成悬案。

    ★★ ③ 的违规**不再 raise**（r5 热插拔 · 层0.1）：
      旧实现在这里直接抛 ManifestError，而 `PluginManager.discover()` /
      `_require()` 全部裸调它 ⇒ 用户往插件根丢一个坏插件，
      `GET /plugins`、`/slots`、enable/disable/install **全部 500**。
      单插件故障被放大成整个管理面瘫痪，这与热插拔的前提正好相反。
      现在违规按**涉事插件**落成 `errors` 项（归因由 crosscheck_detailed
      结构化给出，不解析人话字符串），涉事插件被剔出 plugins 列表，
      其余插件照常工作。⇒ 「插件坏了只坏它自己」。
      ★ 为什么不选「保留插件但标 degraded」：规则 12/13 的违规是
        **能力归属不明**，让它继续挂载等于让一个没有主人的能力对外服务，
        调用方会拿到看似成功实则无主的结果。剔出 + 明示错误更诚实。
    """
    root = Path(modules_dir) if modules_dir else _DEFAULT_MODULES_DIR
    plug = Path(plugins_dir) if plugins_dir else _DEFAULT_PLUGINS_DIR
    data_root = (
        Path(data_plugins_dir)
        if data_plugins_dir is not None
        else default_data_plugins_root()
    )
    builtins, b_errs = _scan(root, "builtin")
    # 数据根优先扫；仓库根里与它同 id 的目录让位（见 docstring）。
    data_party, d_errs = _scan(data_root, "third-party")
    bundled_party, p_errs = _scan(plug, "third-party")
    taken = {p.id for p in data_party}
    third_party: list[PluginInfo] = list(data_party)
    for p in bundled_party:
        if p.id in taken:
            log.info(
                "插件 %s 在数据根与发行根各有一份，采用数据根那份（用户安装的优先）",
                p.id,
                extra={"module": p.id, "shadowed": str(p.directory)},
            )
            continue
        third_party.append(p)
    plugins = builtins + third_party

    # ── ③ 跨插件一致性（规则 12 / 13）──
    #   违规按涉事插件归因，落成错误项而不是全局抛（层0.1）。
    errors = b_errs + d_errs + p_errs
    if plugins:
        detailed = crosscheck_detailed([p.manifest for p in plugins])
        if detailed:
            by_id = {p.id: p for p in plugins}
            blamed: set[str] = set()
            for ids, message in detailed:
                # 归因到具体插件；找不到对应目录时（理论上不该发生）
                # 仍要留下痕迹，绝不静默吞掉违规。
                hit = [by_id[i] for i in ids if i in by_id]
                if not hit:
                    errors.append(
                        PluginError(root, "builtin", f"跨插件一致性：{message}")
                    )
                    continue
                for info in hit:
                    blamed.add(info.id)
                    errors.append(
                        PluginError(info.directory, info.source, f"跨插件一致性：{message}")
                    )
            plugins = [p for p in plugins if p.id not in blamed]

    return DiscoveryResult(plugins=plugins, errors=errors)


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


def thirdparty_package_name(plugin_id: str) -> str:
    """第三方插件的**包名**（`api/` 目录作为一个包）。

    ★ 连字符保留原样：包名只被 importlib 的字符串运算使用（相对导入解析
      走 `__package__` 字符串），不经 `import` 语句的词法分析，故 `pi-agent`
      这类 id 可以安全做包名。换成下划线反而会与「id == 目录名」的口径分叉。
    """
    return f"plugin_thirdparty_{plugin_id}"


def _ensure_thirdparty_package(info: PluginInfo) -> str:
    """把插件的 `api/` 目录注册成一个**包**，返回包名（幂等）。

    ★ 为什么必须这么做（r5 热插拔 · 层3.6）：
      旧实现用 `spec_from_file_location(name, router.py)` 加载路由，
      **没有给 submodule_search_locations** ⇒ 模块不属于任何包 ⇒
      `from .models import X` 抛「attempted relative import with no known
      parent package」。于是第三方插件作者被迫手写路径加载
      （`plugins/countdown/api/router.py` 把这条写成红线给下一个人抄），
      而**多文件插件根本写不出来** —— 学长的 sdu-feeds 就是多文件的。
      ⇒ 热插拔要正经起来，必须让第三方与内置一样能用相对导入。

    ★ 做法：先造一个父包模块（`__path__` 指向 api/），再让 router 以
      `<pkg>.router` 的身份加载。这样相对导入由 importlib 正常解析，
      兄弟模块（models/schema/service）各自只加载一次并进 sys.modules ——
      SQLAlchemy 的 Table 也因此只有一份（重复定义会炸）。
    """
    pkg_name = thirdparty_package_name(info.id)
    api_dir = info.directory / "api"
    existing = sys.modules.get(pkg_name)
    if existing is not None and getattr(existing, "__path__", None) == [str(api_dir)]:
        return pkg_name
    spec = importlib.util.spec_from_file_location(
        pkg_name,
        api_dir / "__init__.py",
        submodule_search_locations=[str(api_dir)],
    )
    if spec is None or spec.loader is None:
        # 没有 __init__.py 也能成包：spec_from_file_location 对不存在的
        # __init__ 仍会给出带 search_locations 的 spec；真拿不到才报错。
        raise ManifestError(f"插件「{info.id}」无法把 {api_dir} 注册为包")
    pkg = importlib.util.module_from_spec(spec)
    pkg.__path__ = [str(api_dir)]  # type: ignore[attr-defined]
    pkg.__package__ = pkg_name
    sys.modules[pkg_name] = pkg
    init_py = api_dir / "__init__.py"
    if init_py.is_file():
        try:
            spec.loader.exec_module(pkg)
        except Exception as exc:  # noqa: BLE001
            sys.modules.pop(pkg_name, None)
            raise ManifestError(
                f"插件包初始化失败：{init_py}（{type(exc).__name__}: {exc}）"
            ) from exc
    return pkg_name


def unload_thirdparty_modules(plugin_id: str) -> None:
    """把某第三方插件在 sys.modules 里的包与子模块清掉（幂等）。

    ★ 为什么卸载必须清（r5 · 层3.5）：目录已被 rmtree，但模块对象还在
      sys.modules 里 —— 旧代码仍可被调用，且同 id 重装时会拿到**上一版的
      兄弟模块**（router 每次重新 exec，models 却复用缓存）⇒ 新路由配旧表模型，
      是最难查的一类不一致。清掉之后重装是一次干净的新加载。

    ★ reload 也用这个（r8）：不清就等于「只重载了 router.py，models/service
      还是旧的」，而那恰恰是升级最常改的文件。
      清之前请先 `detach_plugin_tables()`，否则重新 exec models.py 会撞
      「表已定义」——见那个函数的说明。
    """
    pkg_name = thirdparty_package_name(plugin_id)
    prefix = pkg_name + "."
    for name in [n for n in sys.modules if n == pkg_name or n.startswith(prefix)]:
        sys.modules.pop(name, None)
    # 旧命名（层3.6 之前）留下的条目也一并清，避免升级后残留。
    sys.modules.pop(f"{pkg_name}_router", None)


def _plugin_module_names(plugin_id: str) -> list[str]:
    """该第三方插件当前在 sys.modules 里的全部模块名（包 + 子模块）。"""
    pkg_name = thirdparty_package_name(plugin_id)
    prefix = pkg_name + "."
    return [n for n in list(sys.modules) if n == pkg_name or n.startswith(prefix)]


def detach_plugin_tables(plugin_id: str) -> dict[str, Any]:
    """把该插件已注册到 `SQLModel.metadata` 里的表摘下来，返回可恢复的现场。

    ★ 为什么重新加载前必须摘（r8 实测，不是推理）
    ------------------------------------------------
    `SQLModel.metadata` 是**进程级全局**的（db/base.py 的单一 metadata 设计）。
    `api/models.py` 里每个 `class X(table=True)` 在 exec 时把表注册进去。
    于是「清 sys.modules + 重新 exec models.py」会撞：

        InvalidRequestError: Table 'xxx' is already defined for
        this MetaData instance.

    ⇒ 热重载在**改动了 models.py 的插件**上一律失败，而那正是升级最需要的场景。
      实测复现：造一个两表（带外键）的插件连续 exec 两次 → 第二次必炸；
      先 `metadata.remove(table)` 再 exec → 成功，且外键目标重建正确。

    ★ 归属判据为什么用「模块归属」而不是「表名前缀」
    ------------------------------------------------
    表名前缀（`table_prefix(plugin_id)`）有包含关系：插件 `foo` 的前缀 `foo_`
    会匹配到插件 `foo-bar` 的表 `foo_bar_x` —— 摘错人的表，比不摘更糟。
    而 `cls.__module__ == plugin_thirdparty_<id>.models` 是**精确**归属，
    与「代码是谁 exec 出来的」这一事实同源。

    ★ 返回值的用途：装载失败时把表放回去（`restore_plugin_tables`），
      否则 metadata 里永久少几张表（`create_all` / 字典校验会静默漏掉它们）。
    """
    saved: dict[str, Any] = {"tables": {}, "module_names": _plugin_module_names(plugin_id)}
    names = set(saved["module_names"])
    if not names:
        return saved
    try:
        from sqlmodel import SQLModel
    except Exception:  # noqa: BLE001 —— sqlmodel 不在场时无表可摘（不该发生，但不拦）
        return saved
    meta = SQLModel.metadata
    for mod_name in names:
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for attr in list(vars(mod).values()):
            table = getattr(attr, "__table__", None)
            # ★ 只收「归属这个插件、且真的在这个 metadata 里」的表。
            if table is None or getattr(table, "metadata", None) is not meta:
                continue
            if getattr(attr, "__module__", None) not in names:
                continue
            if table.name in saved["tables"]:
                continue
            saved["tables"][table.name] = table
    for table_name, table in list(saved["tables"].items()):
        try:
            meta.remove(table)
        except Exception as exc:  # noqa: BLE001 —— 一张摘不掉不该阻断其余
            log.warning(
                "插件表从 metadata 摘除失败（已跳过该表）",
                extra={"plugin_id": plugin_id, "table": table_name, "error": str(exc)},
            )
            saved["tables"].pop(table_name, None)
    return saved


def restore_plugin_tables(saved: dict[str, Any]) -> None:
    """把 `detach_plugin_tables` 摘下来的表放回 `SQLModel.metadata`（幂等）。

    ★ 只在**重新装载失败**时调用：那是唯一「旧代码还要继续用这些表描述」
      的场合。装载成功时新代码已经把自己的表注册好了，再放回去就是重复注册
      （`to_metadata` 对同名表会抛）。
    """
    tables = saved.get("tables") or {}
    if not tables:
        return
    try:
        from sqlmodel import SQLModel
    except Exception:  # noqa: BLE001
        return
    meta = SQLModel.metadata
    for table_name, table in tables.items():
        if table_name in meta.tables:
            continue
        try:
            table.to_metadata(meta)
        except Exception as exc:  # noqa: BLE001 —— 恢复失败只记，不掩盖原始错误
            log.warning(
                "插件表恢复到 metadata 失败",
                extra={"table": table_name, "error": str(exc)},
            )


def purge_plugin_bytecode(directory: Path) -> int:
    """删掉插件目录树里的 `__pycache__`（reload 前置），返回删掉的文件数。

    ★ 为什么必须删（r8 实测，不是推理）
    ----------------------------------
    Python 的源加载器按「mtime(秒) + 字节数」判断 .pyc 是否新鲜。
    于是「同一秒内、且字节数恰好相同」的改写（最典型：改一个返回值 `v1` →
    `v2`）会**命中过期的字节码**：

        v1 → v2（同秒同长度）→ 重新 exec → 拿到的还是 v1 的 .pyc
        `importlib.invalidate_caches()` 救不了（它只清 finder 的目录缓存），
        只有把 __pycache__ 删掉才认新源码。

    这是「改了代码没生效」的经典表象，而且**几乎必踩**：
    用户改一行常量再点升级，编辑器写盘和 reload 请求常落在同一秒。

    ★ 为什么放在 reload 而不是 uninstall：卸载是删整棵目录，
      .pyc 跟着一起走。reload 是「原地换血」，目录还在，
      旧字节码不删就一直在 —— 所以这条纪律必须由 reload 自己执行。
    """
    import shutil

    removed = 0
    try:
        for pyc_dir in sorted(directory.rglob("__pycache__")):
            shutil.rmtree(pyc_dir, ignore_errors=True)
            removed += 1
    except OSError as exc:
        log.warning(
            "插件字节码缓存清理失败（已记，不阻断）",
            extra={"directory": str(directory), "error": str(exc)},
        )
    import importlib

    importlib.invalidate_caches()
    return removed


def load_plugin_router(info: PluginInfo) -> Any:
    """加载某插件的 APIRouter。

    builtin/core：走内核的包 import（modules.<id>.router）。
    third-party：把 `api/` 注册成包后加载 `<pkg>.router`（层3.6）——
      于是 `from .models import X` 可用，多文件第三方插件写得出来。
    """
    if info.source == "third-party":
        router_path = info.directory / "api" / "router.py"
        if not router_path.is_file():
            raise ManifestError(f"插件「{info.id}」缺少 api/router.py")
        pkg_name = _ensure_thirdparty_package(info)
        mod = load_python_module_from_file(router_path, f"{pkg_name}.router")
        # 旧模块名保留一个别名：存量插件里有按 `plugin_thirdparty_<id>_router`
        # 取 sys.modules 的写法（countdown/pi-agent 的注释即以它为约定）。
        sys.modules[f"{pkg_name}_router"] = mod
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

    ★ 数据作用域只给 `third-party`（2026-11-09）：
      内置模块与内核是系统的一部分，模块间本来就有跨模块读；
      给它们挂作用域依赖会把正常功能拦死（详见 `core/registry.py:mount` 的说明）。
    """
    if info.id in registry.mounted():
        return
    router = load_plugin_router(info)
    registry.mount(
        info.id,
        router,
        prefix=info.manifest["api"]["base"],
        scoped=(info.kind == "third-party"),
    )
    # ★ 挂载即登记发布端声明（2026-11-10）：运行期 enable / install 的第三方插件
    #   走这条路径进系统，它们的 `emits` 必须在这时进总线注册表，
    #   否则第一条事件就会撞上 UndeclaredPublication。幂等。
    from core.events import event_bus  # 局部 import：避免 discover ⇄ events 的导入期循环

    event_bus.declare_publisher(info)
