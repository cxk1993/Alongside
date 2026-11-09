"""装配阶段——启动时把已登记的整合包装起来。

★ 这一层做什么
--------------
把「内核能力」（发现、启用、禁用、路由注册表）接到「装配器」上，
然后按登记表逐个装配用户装过的包。

★ 为什么单独一个文件，而不是把逻辑写进 `core/app.py`
---------------------------------------------------
1. `core/app.py` 归在「装配器（不算内核）」层，但它已经 527 行。
   装配逻辑有分支（开关、时机、失败姿态），写进去会让启动流程更难读。
2. ★ 更要紧：**内核不该知道包层的事**。
   `core/app.py` 只需要知道「有一件事叫装配，具体怎么装不归我管」。
   逻辑放在包层，边界才是实的——不是靠约定，是靠代码位置。
3. 门禁只看得到 import，看不到「谁该知道谁」。
   **位置是比注释更硬的边界。**

★ 三条产品判断，做成了配置而不是写死
--------------------------------------
「装配什么时候触发」「失败了怎么办」是**产品判断**，不是技术判断。
实现的默认不该替产品做决定——所以做成配置项（见 `core/config.py`）：

- `packs_enabled`：总开关（默认关——不做没被要求的事）
- `packs_autoload`：启动时自动装 / 只在显式请求时装
- `packs_failure_mode`：整包回滚 / 部分成功

★ 这样产品决定变成**改一行配置**，不是改代码、不是改接口。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from packs.model import LoadReport

log = logging.getLogger(__name__)


def resolve_data_dir(db_path: str) -> Path:
    """从数据库路径推出数据根目录。

    ★ 为什么从 `db_path` 推而不是新增一个配置项：
      数据库、备份、日志、包目录本来就在同一个 `data/` 下，
      各配各的迟早会分叉（用户改了 DB_PATH 却忘了改包目录，
      ⇒ 包清单在一个地方、数据库在另一个地方）。
      ⇒ **一个真相源**：数据根 = 数据库所在目录。
    """
    p = Path(db_path)
    if not p.is_absolute():
        # 相对路径相对 services/api 解析（与 db/engine.py 的 _resolve_db_path 同口径）
        p = Path(__file__).resolve().parent.parent / p
    return p.parent


def build_boot_assembler(
    *,
    db_path: str,
    discover: Any,
    enable: Any,
    disable: Any,
    registry: Any,
    failure_mode: str = "atomic",
) -> Any:
    """用内核能力组装一个**启动用**的装配器。

    ★ `packs_root` **不传**——它现在的语义是「**额外**的查找根」，
      而数据目录本来就是默认根（`packs.roots` 会去找它）。
      早先这里显式传 `data_dir / "packs"`，是因为那时
      「包在哪」由每个调用方自己算 ⇒ 显式写出来是为了让答案唯一。
      现在答案唯一由 `packs.roots` 保证，
      再显式传一遍反而会让它变成「额外根」而抢在默认根前面——
      那是把「显式 > 默认」的优先级用错了地方。

    ★ `failure_mode` 传进去，**不是**只在日志里说说——
      否则配置项就是个摆设：界面显示 partial、实际跑 atomic。

    ★ 设置读写（`read_settings` / `write_settings`）在这里接上：
      没有它，包清单里的 `config` **不会落地**——
      而「配置不生效」的表现是「插件行为不对」，极难归因。
      ⇒ 接上，且**接不上就明说**（`_settings_bridge` 会记日志）。

    ★ 为什么在这里 import 插件管理器，而不是让内核传进来：
      包层要的是「读某个插件的设置」「写某个插件的设置」两个窄动作，
      而 `PluginManager` 是一整个对象（带状态机、审计、台账）。
      传整个对象等于把内核的一大块知识塞进包层的接口里——
      包层就会开始「认识内核的内部结构」，那个边界就破了。
      ⇒ 与 `enable` / `disable` 同一纪律：传窄动作，不传整个对象。
    """
    from packs.assembler import PackAssembler  # 局部导入：让本模块能被无依赖地读

    data_dir = resolve_data_dir(db_path)
    read_settings, write_settings = _settings_bridge()
    return PackAssembler(
        discover=discover,
        enable=enable,
        disable=disable,
        registry=registry,
        data_dir=data_dir,
        failure_mode=failure_mode,
        read_settings=read_settings,
        write_settings=write_settings,
    )


def _settings_bridge() -> tuple[Any | None, Any | None]:
    """把内核的「读/写插件设置」适配成装配器要的两个窄回调。

    ★ 接不上就返回 `(None, None)`，**不抛**：
      装配器拿到 `None` 会「不落地任何配置，并在报告里说没落地」——
      这比抛异常好：包还能装，只是配置没生效（且这件事被记下了）。

    ★ 为什么不让装配器自己 import 插件管理器：
      装配器是**包层最内层**的东西，它 import 内核就等于包层依赖内核——
      而边界是「内核 → 包层」。⇒ 这一层放在 `boot.py`（已经是接线层）。

    ★ 为什么每次调用都取管理器而不是缓存：
      插件管理器是**单例**（`get_plugin_manager()`），
      但缓存它等于把「管理器什么时候被替换」这个判断固化在这里。
      ⇒ 每次取，取到的永远是当前的。
    """
    try:
        from core.plugins.manager import get_plugin_manager  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 —— 接不上就「不落地」，不拖垮启动
        log.warning("接不上插件设置读写，包提供的配置不会落地：%s", exc)
        return None, None

    def read_settings(plugin_id: str) -> Any:
        return get_plugin_manager().get_settings(plugin_id)

    def write_settings(plugin_id: str, values: Any) -> Any:
        return get_plugin_manager().set_settings(plugin_id, dict(values))

    return read_settings, write_settings


def summarize_reports(reports: list[LoadReport]) -> list[dict[str, Any]]:
    """把装配报告压成**可序列化的摘要**。

    ★ 为什么由包层定义摘要的形状：内核不该翻译包层的报告。
      一旦内核开始把 `LoadReport` 翻译成 dict，它就认识包层的内部结构了——
      「内核不认识包层」这条边界就破了。
      ⇒ 形状在包层定，内核只负责把结果原样挂上去。

    ★ 逐成员留痕是**必带字段**，不是可选：不论最终状态如何，
      用户都要能问出「每个成员到底怎么了」——
      这是「可追问、可审计」的地基。

    ★ `summary_not_verified` 必须透出去（§19.3 的 `summaryNotVerified`）：
      「摘要没校验」是**没有能力发现内容变化**，
      把它藏在内部就等于让用户以为一切都被验过了。
    """
    out: list[dict[str, Any]] = []
    for r in reports:
        out.append(
            {
                "pack_id": r.pack_id,
                "status": r.status,
                "ok": r.ok,
                "error": r.error,
                "members": [
                    {"id": m.id, "status": str(m.status), "reason": m.reason}
                    for m in r.members
                ],
                "new_members": list(r.new_members),
                "summary_not_verified": list(r.summary_not_verified),
                # ★ `overridden` 必须透出去（§19.3 硬要求②「不能省」）：
                #   用户必须能看出「我看到的值是哪个包给的」。
                #   藏在这里 = 用户永远不知道自己看到的是包的值还是自己设的。
                "overridden": [dict(o) for o in r.overridden],
                # ★ 能力路由留痕（§19.3 的 `selectedProviders`）
                "selected_providers": [dict(s) for s in r.selected_providers],
                # ★ 待裁决的多 Provider 冲突（不阻断启动，但必须看得见）
                "provider_conflicts": [dict(c) for c in r.provider_conflicts],
            }
        )
    return out


def pack_settings() -> dict[str, Any]:
    """包层自己的配置。

    ★ 为什么不在 `core/config.py` 的 Settings 里声明：
      内核一旦声明了 `packs_enabled` / `packs_autoload` / `packs_failure_mode`，
      它就**认识了包层有配置**这件事——「内核不认识包层」的边界开始漏。
      ⇒ 配置由包层自己读（`read_setting` 正是为「pydantic-settings 未声明的
      运行期开关」准备的），内核只问「要不要装」。

    ★ 三条都是**产品判断**（装不装 / 什么时候装 / 失败怎么办），
      做成配置而不是写死 ⇒ 产品决定是改一行 .env，不是改代码。

    - `PACKS_ENABLED`（默认关）：装配会启用插件、迁移表、改路由，
      是**有副作用**的动作 ⇒ 默认不做没被要求的事。
    - `PACKS_AUTOLOAD`（默认开）：true=启动时自动装已登记的包；
      false=只在用户显式请求时装。
    - `PACKS_FAILURE_MODE`（默认 atomic）：atomic=任一成员失败则整包回滚；
      partial=保留成功的成员、失败成员留痕。
    """
    from core.config import read_setting  # 局部导入：包层不与内核顶层耦合

    def _bool(key: str, default: bool) -> bool:
        raw = read_setting(key, "")
        if raw == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    mode = (read_setting("PACKS_FAILURE_MODE", "atomic") or "atomic").strip().lower()
    if mode not in {"atomic", "partial"}:
        # 配错了不该静默——但也不该让启动失败。取默认并记一笔。
        log.warning("PACKS_FAILURE_MODE=%r 不是合法取值（atomic/partial），已按 atomic 处理", mode)
        mode = "atomic"
    return {
        "enabled": _bool("PACKS_ENABLED", False),
        "autoload": _bool("PACKS_AUTOLOAD", True),
        "failure_mode": mode,
    }


def boot_packs(app: Any) -> list[dict[str, Any]]:
    """启动装配的**单一入口**——内核只调这一个函数。

    ★ 为什么包层要提供这个入口，而不是让内核自己拼装：
      内核一旦开始「读配置、决定要不要装、调装配器、把报告转成 dict」，
      它就认识了包层的**四个概念**（配置项、装配器、报告、摘要形状）。
      那条「内核不认识包层」的边界就破了。
      ⇒ 内核只做：把能力递过去、把结果接住。

    ★ 为什么接收整个 `app` 而不是一堆参数：
      参数版（discover/enable/disable/registry/settings）要内核写十几行，
      而内核每多写一行就多认识包层一分。
      ⇒ 让包层自己从 `app` 取（registry 在 `app.state.registry`，
      配置自己读 .env，插件管理器自己 import）——
      就像 `modules/` 下的普通插件用 `core.plugins.get_plugin_manager` 那样，
      这是本项目已有的惯例。

    ★ 返回的是**已压平的摘要**（list[dict]），不是 `LoadReport` 对象——
      内核不需要知道包层的数据模型。

    ★ 绝不抛异常：包层是**增强**，不是**前提**。
      内核已经能跑（插件都挂好了），包层挂了不该拖垮启动。
    """
    cfg = pack_settings()
    if not cfg["enabled"]:
        return []
    if not cfg["autoload"]:
        # 手动模式：启动时不装，等用户显式触发。
        return []
    try:
        from core.config import get_settings  # noqa: PLC0415
        from core.plugins.manager import get_plugin_manager  # noqa: PLC0415

        mgr = get_plugin_manager()
        reports = autoload_packs(
            db_path=get_settings().db_path,
            discover=mgr.discover,
            enable=mgr.enable,
            disable=mgr.disable,
            registry=getattr(app.state, "registry", None),
            autoload=True,
            failure_mode=cfg["failure_mode"],
        )
    except Exception as exc:  # noqa: BLE001 —— 同上：绝不拖垮启动
        log.error("整合包装配失败，已跳过包层：%s", exc)
        return []
    return summarize_reports(reports)


def autoload_packs(
    *,
    db_path: str,
    discover: Any,
    enable: Any,
    disable: Any,
    registry: Any,
    autoload: bool = True,
    failure_mode: str = "atomic",
) -> list[LoadReport]:
    """装配已登记的包。返回每���包的报告。

    ★ **绝不抛异常**：装配失败不该拖垮启动。
      内核已经能跑（插件都挂好了），包层是**增强**不是**前提**。
      ⇒ 失败记日志 + 进报告，让上层能看见「哪些包没装上」。
      这与第三方插件恢复段的纪律一致（单个失败只记目击，不影响其它）。

    ★ `autoload=False` 时**什么都不做**（连读登记表都不读）——
      手动模式下装配由用户显式触发。
    """
    if not autoload:
        return []

    from packs.wiring import load_registered_packs  # 局部导入同上

    data_dir = resolve_data_dir(db_path)
    try:
        assembler = build_boot_assembler(
            db_path=db_path,
            discover=discover,
            enable=enable,
            disable=disable,
            registry=registry,
            failure_mode=failure_mode,
        )
    except Exception as exc:  # noqa: BLE001 —— 装配器构造失败也不该拖垮启动
        log.error("整合包装配器构造失败，已跳过包层：%s", exc)
        return []

    try:
        reports = load_registered_packs(assembler, data_dir=data_dir)
    except Exception as exc:  # noqa: BLE001 —— 同上：登记表坏了也不该拖垮启动
        log.error("整合包登记读取失败，已跳过包层：%s", exc)
        return []

    if not reports:
        # 没装过任何包是**正常状态**，不该刷 error 级日志。
        log.debug("整合包：没有已登记的包，跳过装配")
        return []

    ok = [r for r in reports if r.ok]
    bad = [r for r in reports if not r.ok]
    if ok:
        log.info(
            "整合包装配完成：%d 个成功（%s）",
            len(ok),
            "、".join(r.pack_id for r in ok),
        )
    if bad:
        # ★ ADR-0005 的纪律：失败必须点名，不许只留在单条 warning 里。
        log.error(
            "整合包装配失败 %d 个 —— %s",
            len(bad),
            "；".join(f"{r.pack_id}（{r.error or '未装完'}）" for r in bad),
        )
    # ★ partial 姿态下「部分成功」的报告 status 仍是 loaded、error 非空——
    #   所以它落在 ok 里，但用户必须看得见「这里有没装上的」。
    #   ⇒ 逐包点名，不让「部分成功」悄悄混在「全部成功」里。
    partial = [r for r in ok if r.error]
    if partial:
        log.warning(
            "整合包部分成功 %d 个（失败姿态=partial，已保留成功成员）—— %s",
            len(partial),
            "；".join(f"{r.pack_id}（{r.error}）" for r in partial),
        )
    return reports
