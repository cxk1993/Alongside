"""第五章 · Reactive Coeffect —— 依赖满足驱动。

★ 这个模块是**第五章与第三章的接缝**
-----------------------------------
第三章（Phase Machine）回答「插件现在在哪一步」，
第五章（依赖表）回答「插件需要的能力有没有人提供」。

两章各自都对，但**中间缺一根线**：
能力被撤了，依赖它的插件**不会自己知道**——
它会继续挂在路由上，继续宣称自己可用，
直到某天有人真的去调它，才撞上一个莫名其妙的 403。

本模块补上这根线：**依赖表变化 → 主动重算 → 状态机转移**。

★ 处置策略由插件自己声明（`deactivation`）
-------------------------------------------
| deactivation | 依赖丧失时 | 谁来决定 |
|---|---|---|
| `manual`（默认） | 什么都不做，保持 ACTIVE | 向后兼容：老插件没声明这个字段，行为不变 |
| `auto` | 内核**主动**转 PAUSED | 插件自己声明「我依赖它，它没了就该停」 |

★ 为什么默认值是 manual 而不是 auto
-----------------------------------
如果默认 auto，那么**升级内核之后，所有老插件都会在依赖丢失时被停掉**——
而它们甚至不知道自己有这个声明。这是「内核升级 ⇒ 行为突变」的经典陷阱。
所以默认 manual，插件要新行为就**显式写进 manifest**。

★ 但 manual 不是「什么都不管」
----------------------------
manual 的插件在依赖丧失时保持 ACTIVE，但**依赖表里会记下它处于「不满足」状态**，
调用它的能力时会被运行时校验拦下（403）。
这跟升级前的行为一致（`core/deps.py` 的运行时校验），
所以 manual 不会引入新的破坏，只是把「原来也有」这件事显式化了。

★ 为什么不用 `deactivation: auto` 的插件必须由内核而不是插件自己转阶段
--------------------------------------------------------------------
因为**插件自己无法可靠地知道自己该转到哪个阶段**：
它只知道「我依赖的能力没了」，但「我现在是不是 ACTIVE」「我该不该先摘路由」
这些是内核的全局信息。
让插件自己转阶段，就会出现「插件说 PAUSED 但路由还挂着」这类不一致。
⇒ 阶段转移**只能**由内核发起，这是第三章「阶段唯一入口」原则的延伸。
"""
from __future__ import annotations

from typing import Any

from core.logging import get_logger
from core.plugins.phase import Phase, PhaseReason

log = get_logger("kernel.coeffect")


def apply_notify_outcome(outcome: Any, registry: Any) -> dict[str, list[str]]:
    """把 `DependencyTable.notify()` 的结果落到状态机上。

    ★ 顺序不能乱：**先摘路由，再转阶段**。
      不变量是「阶段非 ACTIVE/PAUSED 时路由必须摘掉」——
      先转阶段再摘，中间那一瞬会违反不变量（对外可见的中间态）；
      先摘后转，中间那一瞬只是「阶段说 ACTIVE 但路由已摘」，
      那是**更弱**的偏差（对外不可达），且窗口极短。
      （与 `manager.disable()` 同一纪律。）

    ★ 为什么「恢复」也要走内核：插件不能自己宣布「我现在又好了」——
      它不知道自己的路由挂没挂、effect 撤了没有。
      内核统一做，才有一致的次序。

    ★ `manual` 的插件落在 `neutral` 里，**不做任何转移**——
      它保持 ACTIVE，行为与升级前一致。
    """
    result: dict[str, list[str]] = {
        "deactivated": [],
        "reactivated": [],
        "neutral": [],
    }

    deactivated = list(getattr(outcome, "deactivated", []) or [])
    activated = list(getattr(outcome, "activated", []) or [])
    neutral = list(getattr(outcome, "neutral", []) or [])

    for plugin_id in deactivated:
        try:
            _deactivate(plugin_id, registry)
            result["deactivated"].append(plugin_id)
        except Exception as exc:  # noqa: BLE001 —— 状态不一致不该中断整轮重算
            log.error(
                "依赖丧失转 PAUSED 失败（已记，继续处理其余插件）",
                extra={"module": plugin_id, "error": str(exc)},
            )

    for plugin_id in activated:
        try:
            _reactivate(plugin_id, registry)
            result["reactivated"].append(plugin_id)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "依赖恢复转回 ACTIVE 失败（已记，继续处理其余插件）",
                extra={"module": plugin_id, "error": str(exc)},
            )

    result["neutral"] = neutral
    return result


def _deactivate(plugin_id: str, registry: Any) -> None:
    """依赖丧失 ⇒ 摘路由 + 转 PAUSED。"""
    from core.plugins.manager import _withdraw_provides
    from core.plugins.state_store import read_phase

    manager = _get_manager(registry)
    info = manager._require(plugin_id)  # noqa: SLF001 —— 同包内的受控访问

    # ★ 幂等护栏 + 作用域护栏。
    #
    #   状态机把「自己 → 自己」判为**状态图上无此边**（不是非法，是不存在），
    #   而 `transition()` 把它当**空操作**——不抛错、不改状态、不写审计。
    #   但这里仍要提前判：否则会白跑一次 `_withdraw_provides`，
    #   并且让日志看起来像「发生了多次依赖丧失」（审计噪音）。
    #
    # ★★ 第二个护栏更关键：**只有当前 ACTIVE 的插件才该被自动暂停**。
    #   依赖表里登记了 requires 的插件，可能压根没启用（阶段 PENDING）——
    #   它本来就没在服务，「暂停」它没有任何意义，
    #   却会把它的阶段从一个真实状态改成另一个（把 PENDING 改成 PAUSED），
    #   让人以为「它曾经服务过、后来被依赖拖停了」。
    current = read_phase(plugin_id)
    if current != Phase.ACTIVE.value:
        return

    if info.id in registry.mounted():
        registry.unmount(info.id)
    _withdraw_provides(plugin_id)
    _transition(plugin_id, Phase.PAUSED, PhaseReason.DEPENDENCY_LOST)


def _reactivate(plugin_id: str, registry: Any) -> None:
    """依赖恢复 ⇒ 转回 ACTIVE。"""
    from core.plugins.manager import _register_dependencies
    from core.plugins.state_store import read_phase

    manager = _get_manager(registry)
    info = manager._require(plugin_id)  # noqa: SLF001

    current = read_phase(plugin_id)
    if current == Phase.ACTIVE.value:
        # 已经 ACTIVE：不必再转，但要**把 provides 补回来**——
        # 依赖丧失时我们撤掉了它的能力，恢复时必须重新提供，
        # 否则它「是 ACTIVE 但什么都不提供」，比 PAUSED 还糟
        # （外部看不出它坏了，而它确实是坏的）。
        _register_dependencies(info)
        return

    # ★★ 自动恢复的**唯一合法前提**：上一次暂停是**依赖丧失**造成的。
    #
    #   阶段上「手动停的」与「依赖拖停的」长得一模一样（都是 PAUSED），
    #   不看原因就分不出来。而如果不管原因，会出现一件很糟的事：
    #   **人手动停掉的插件会被系统自动拉起来**——
    #   「手动停用」形同失效，而用户完全不知道（他明明点过停）。
    #
    #   所以这里读 `phase_reason`：只有 `dependency-lost` 才自动恢复。
    #   `manual`（人主动停）保持 PAUSED——要恢复，人自己再点一次启用。
    from core.plugins.state_store import read_phase_reason

    reason = read_phase_reason(plugin_id)
    if reason != PhaseReason.DEPENDENCY_LOST.value:
        log.info(
            "依赖已恢复，但该插件上次不是因依赖丧失而暂停的，保持 PAUSED（等人工启用）",
            extra={"module": plugin_id, "phase_reason": reason},
        )
        return

    # ★ 恢复时先重新挂路由，再转 ACTIVE。
    #   顺序与「先摘后转」镜像：不变量是「阶段 ACTIVE 时路由必须在」——
    #   先转阶段再挂，中间那一瞬会违反不变量；
    #   先挂后转，中间那一瞬只是「路由已挂但阶段还写着 PAUSED」，
    #   那是**更弱**的偏差（对外更严格），窗口极短。
    if info.id not in registry.mounted():
        from core.plugins.discover import mount_plugin

        mount_plugin(info, registry)
    _register_dependencies(info)
    _transition(plugin_id, Phase.ACTIVE, PhaseReason.DEPENDENCY_SATISFIED)


def _get_manager(registry: Any) -> Any:
    """拿到 manager（registry 上挂着，或用默认单例）。"""
    mgr = getattr(registry, "manager", None)
    if mgr is not None:
        return mgr
    from core.plugins.manager import get_plugin_manager

    return get_plugin_manager()


def _transition(plugin_id: str, to: Phase, reason: PhaseReason) -> None:
    """阶段转移（复用 manager 里的那个，行为一致：失败记日志不抛）。"""
    from core.plugins.manager import _transition as _mgr_transition

    _mgr_transition(plugin_id, to, reason)


__all__ = ["apply_notify_outcome"]
