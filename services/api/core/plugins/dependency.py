"""依赖表与「满足性驱动」的通知机制（Reactive Coeffect 的可实现部分）。

★ 为什么需要它
--------------
现在的做法是在**启动时校验一次**：读每个插件的 `requires`，检查「声明的这些
能力有没有人提供」。校验通过，插件就进入 ACTIVE。

问题在于**校验之后的事情没人管**。启动校验只是一张快照，它保证不了
「此后一直成立」：

| 场景 | 现状 | 应该的行为 |
|---|---|---|
| A 依赖 B 的能力，启动时 B 在 | 校验通过，A 进入 ACTIVE | 同左，但之后 B 若消失，要能自动处理 A |
| **B 被禁用** | A **还是 ACTIVE**，有调用打过来时报 403 | **A 自动进入 PAUSED** |
| **B 重新启用** | A **什么都不发生**（仍然 403） | **A 自动恢复 ACTIVE** |

第二、三行说明现在的依赖声明是一份**静态规格**——只在启动那一刻有意义。
而它应该是**活性规格**：一份持续有效的声明，每次依赖状况变化都重新判定，
并据此驱动插件的激活与停用。

★ 三个基础定义
--------------
**定义一 · 依赖规格** `𝒟Σ ≔ 𝖲𝖾𝗍(K)` —— 就是一个**集合**（manifest 的 `requires`）。
选「集合」而不是「列表」的原因：依赖关系没有顺序，只有「有」和「没有」两种
状态；用集合能直接表达这个语义，而且集合的包含判断正是满足性判定需要的
基本操作。

**定义二 · 满足判定** `σ ⊨ d ≔ ∀k ∈ d. k ∈ dom(σ)` —— 声明的每一个依赖，
都存在于依赖表中。两个重要性质：**可判定**（遍历一遍 `d` 逐项查表，结果确定）
与**全称量词**（任何一个依赖缺失，整个判定就是不满足）。这个严格性是有意的：
`requires` 是硬依赖，一个都不能少。

（软依赖 `optionalDependencies` 不参与这个判定——它回答的是「激活之后能用什么」，
不影响「能不能激活」。）

**定义三 · 依赖变化的三分类** —— 见 `notify()` 的实现。

★ 核心机制：依赖表本身是一个 effect
------------------------------------
依赖表要记的是「哪个能力由谁提供」。这件事看起来只是维护一个字典，但它有
一个容易被忽略的性质：**装入和撤除这两个动作，都必须主动通知别人**。

原因是满足性判定是双向的。一个能力从无变有时，依赖它的插件要从「等」变成
「活」；从有变无时，要从「活」变成「停」。只处理一个方向，系统就变成单向导通。

★ 通知为什么用「幂等重算」而不是「精确追踪」
--------------------------------------------
**直觉上「更聪明」的做法**：当某个能力变化时，先算出「到底哪些插件的依赖与此
相关」，然后只通知它们。这需要维护一张反向索引（能力 → 依赖它的插件集合），
并在每次插件装卸时同步更新。

这条路能走，但代价是：反向索引本身就是一份新状态，它也有「不一致」的可能；
而且它必须和依赖表严格同步，任何一处漏更新，就会导致「该通知的没通知」——
而这类 bug 极难发现（系统的行为在多数情况下正常，只在某个特定顺序下出错）。

**采用的做法是反过来的：放弃精确性，换幂等性。** 不管变化的 key 是什么、
和谁相关，一律把所有活着的插件重算一遍，然后按 (before, after) 两个布尔值的
组合分类处置：

| before | after | 处置 |
|:--:|:--:|---|
| 否 | 是 | **激活**（`dependency-satisfied`） |
| 是 | 否 | **停用**（`dependency-lost`） |
| 是 | 是 | neutral，什么都不做 |
| 否 | 否 | neutral，什么都不做 |

这个做法成立，全靠一点：**重算必须是幂等的**。因为幂等，所以「多通知了不相关
的插件」不会造成任何副作用；因为幂等，所以「漏算了相关性」也不需要担心。

这就完成了一次关键的降级：**把「精确依赖追踪」这个图论问题，降级成了
「全量重算」这个循环问题**。代价是每次变化都要 O(n) 跑一遍所有插件（在
「一个人、几十个插件」的规模下完全可以忽略），收益是：**再也不需要维护一张
可能不一致的反向索引**。

★ 代价要说清
------------
重算是幂等的，所以「重算」无害。**但重算有时间成本**——幂等性换来的是正确性
与实现简单，不是零开销。
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from core.kernel import EffectKind, Kernel, get_kernel
from core.logging import get_logger

log = get_logger("kernel.dependency")


@dataclass
class PluginRequirements:
    """某个插件的依赖声明与满足性缓存。

    ★ `was_satisfied` 必须**落库**（存 `plugin_state` 表），不能只在内存里。
      如果只在内存里，进程重启后 `was_satisfied` 一律重新初始化为 `False`。
      那么每一个当前满足依赖的插件，都会在第一次通知时被判定为
      「不满足 → 满足」，触发一次**伪激活**——它会走一遍真实的加载流程、
      重建 effect、写一堆审计记录。

      更糟的是时机：这些伪激活恰好发生在启动阶段，会和启动对账叠在一起，
      让本来就需要小心处理的启动流程变得更难对账。
    """

    plugin_id: str
    requires: frozenset[str] = frozenset()
    optional: frozenset[str] = frozenset()
    was_satisfied: bool = False
    # 依赖丧失时的行为：`manual`（默认，向后兼容）/ `auto`（自动暂停）。
    deactivation: str = "manual"

    def satisfied_by(self, provided: frozenset[str]) -> bool:
        """满足性判定：`requires ⊆ provided`。

        ★ 只看 `requires`，不看 `optional` —— 软依赖回答的是「激活之后能用
          什么」，它缺了只是降级，不是「活不下去」，所以不该影响阶段。
        """
        return self.requires <= provided


@dataclass
class NotifyOutcome:
    """一次通知的结果（供测试与诊断）。"""

    activated: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    neutral: list[str] = field(default_factory=list)

    @property
    def changed(self) -> list[str]:
        return [*self.activated, *self.deactivated]


class DependencyTable:
    """依赖表：记「哪个能力由谁提供」。

    ★ 它本身就是一个 effect：装入和撤除都走 `kernel.effect()`，
      所以依赖表的变更**自动带逆**、自动在插件卸载时回滚、自动触发通知。
      如果它不走 effect 原语，就会出现「插件卸载了、依赖表里还留着它的能力名」
      这种残留——而这类残留最难查，因为它会让别的插件误判「这个能力还能用」。
    """

    def __init__(self, kernel: Kernel | None = None) -> None:
        self._kernel = kernel if kernel is not None else get_kernel()
        # 能力名 -> 提供它的插件 id 集合
        self._table: dict[str, set[str]] = {}
        self._lock = threading.RLock()
        # 通知钩子：依赖表变化时调用（由内核注入，负责驱动状态机）
        self._notify_hook: Callable[[list[str]], Any] | None = None
        # 插件的依赖声明登记处
        self._requirements: dict[str, PluginRequirements] = {}

    # ── 通知钩子 ────────────────────────────────────────────────────

    def set_notify_hook(self, hook: Callable[[list[str]], Any] | None) -> None:
        """注入「依赖表变了 → 该干什么」的钩子（内核注入，负责驱动状态机）。"""
        self._notify_hook = hook

    # ── 依赖声明登记 ────────────────────────────────────────────────

    def register_requirements(
        self,
        plugin_id: str,
        requires: list[str] | frozenset[str] | None = None,
        optional: list[str] | frozenset[str] | None = None,
        *,
        deactivation: str = "manual",
        was_satisfied: bool | None = None,
    ) -> PluginRequirements:
        """登记某插件的依赖声明（`requires` / `optionalDependencies`）。

        ★ `was_satisfied` 不给时按**当前事实**算（`requires ⊆ 当前已提供`），
          不给就硬编码 False 会造成「第一次 notify 误判成恢复」——
          那会让手动 disable 失效（详见下面构造处的注释）。
        """
        req = PluginRequirements(
            plugin_id=plugin_id,
            requires=frozenset(requires or ()),
            optional=frozenset(optional or ()),
            # ★ `was_satisfied` 必须按**当前事实**初始化，不能硬编码 False。
            #   硬编码 False 的后果：第一次 notify() 会把这个插件判成
            #   「从依赖丧失到依赖恢复」，于是把**刚被人手动 disable 的插件**
            #   又转回 ACTIVE——手动停用形同失效。
            #   （本轮实测踩到：disable 后 phase 停在 ACTIVE 不动，
            #     根因就是这里。）
            was_satisfied=(
                was_satisfied
                if was_satisfied is not None
                else frozenset(requires or ()) <= self.keys()
            ),
            deactivation=deactivation,
        )
        with self._lock:
            self._requirements[plugin_id] = req
        return req

    def requirements(self, plugin_id: str) -> PluginRequirements | None:
        with self._lock:
            return self._requirements.get(plugin_id)

    def all_requirements(self) -> list[PluginRequirements]:
        with self._lock:
            return list(self._requirements.values())

    def unregister_requirements(self, plugin_id: str) -> None:
        with self._lock:
            self._requirements.pop(plugin_id, None)

    # ── 依赖表本体 ──────────────────────────────────────────────────

    def keys(self) -> frozenset[str]:
        """当前**有人提供**的能力集合。

        ★ 判定「满足」只看这个集合的成员资格。所以空集合必须清掉——
          否则表里会留下 `{"todo.read": set()}`，它**存在但没人提供**，
          于是出现**假满足**：判定说「依赖齐了」、插件被激活，实际一调用就 403。
        """
        with self._lock:
            return frozenset(self._table.keys())

    def providers(self, capability: str) -> set[str]:
        with self._lock:
            return set(self._table.get(capability, ()))

    def providers_of(self, provider_id: str) -> list[str]:
        """某插件提供的全部能力名（撤除时用）。"""
        with self._lock:
            return sorted(
                cap for cap, holders in self._table.items() if provider_id in holders
            )

    def provide(self, capability: str, provider_id: str) -> None:
        """声明「provider_id 提供 capability」。

        ★ 走 `kernel.effect()`：变更自动带逆，插件卸载时自动撤除。
        """
        with self._kernel.effect(
            f"provide:{capability}", kind=EffectKind.RUNTIME, plugin_id=provider_id
        ) as eff:
            # ★ `undo` 传的是**闭包本身**，不是「调用 _withdraw 的 lambda」。
            #   写成 `undo=lambda: self._withdraw(...)` 的话，那个 lambda 只会
            #   **调用** `_withdraw` 并把它返回的闭包丢掉 —— 撤销什么也没做，
            #   而依赖表里的能力名会永久残留。
            #   （技术报告 §5.9.1 的示例代码就是这个写法，是报告里的一个笔误。）
            eff.add(
                do=lambda: self._table.setdefault(capability, set()).add(provider_id),
                undo=self._withdraw(capability, provider_id),
            )

    def _withdraw(self, capability: str, provider_id: str) -> Callable[[], None]:
        """撤除一个提供者（这是 effect 的逆，**撤除也要通知**）。"""

        def undo() -> None:
            with self._lock:
                holders = self._table.get(capability)
                if holders is not None:
                    holders.discard(provider_id)
                    # ★ 空集合必须清掉 —— 否则会有「假满足」
                    if not holders:
                        self._table.pop(capability, None)
            # ★ 撤除也通知：能力从「有」变「无」，依赖它的插件要能自动降级
            self._notify([capability])

        return undo

    def withdraw(self, capability: str, provider_id: str) -> None:
        """主动撤除（不走 effect 逆路径）——供测试与异常处置用。"""
        self._withdraw(capability, provider_id)()

    def _notify(self, changed: list[str]) -> None:
        if self._notify_hook is None:
            return
        try:
            self._notify_hook(changed)
        except Exception as exc:  # noqa: BLE001 —— 通知失败不该破坏依赖表本身
            log.error(
                "依赖变化通知失败（依赖表已变更，未回滚）",
                extra={"changed": changed, "error": f"{type(exc).__name__}: {exc}"},
            )

    # ── 通知：幂等重算 ──────────────────────────────────────────────

    def notify(self, changed_keys: list[str] | None = None) -> NotifyOutcome:
        """依赖表变化后的**幂等重算**。

        ★ 不管变化的 key 是什么、到底和谁相关，一律把每个活着的插件都重算一遍。
          然后按「之前满足 / 现在满足」两个布尔值的组合分类处置。

        ★ 幂等的**实质是「状态变更相同」，不是「返回的列表相同」**：
          通知 1 次 ⇒ 某个插件激活一次；通知 3 次 ⇒ 它也只激活**一次**，
          后两次因为 `was_satisfied` 已是 True 而落进 neutral 分支。
          若不做这个保护，系统会被无意义的转移刷满、状态机被虚假转移淹没。

        ★ `was_satisfied = after` 这一行必须写在**循环末尾、所有分支之外**。
          不能在各个分支里分别写 —— 因为四个分支里两个是「什么都不做」
          （neutral），如果只在有动作的分支里更新，neutral 情况下这个值就永远
          停在旧值上，下一次通知又会被误判成「状态翻转」。
        """
        provided = self.keys()
        outcome = NotifyOutcome()
        for req in self.all_requirements():
            before = req.was_satisfied
            after = req.satisfied_by(provided)
            if before and not after:
                # 依赖丧失
                if req.deactivation == "auto":
                    outcome.deactivated.append(req.plugin_id)
                else:
                    # manual（默认）：保持 ACTIVE，被调用时报 403 —— 向后兼容
                    outcome.neutral.append(req.plugin_id)
            elif not before and after:
                outcome.activated.append(req.plugin_id)
            else:
                outcome.neutral.append(req.plugin_id)
            # ★ 循环末尾统一更新（分支之外）
            req.was_satisfied = after
        return outcome

    def __contains__(self, capability: object) -> bool:
        """`cap in table` ⇔ 「有人提供这个能力」。

        ★ 空集合已被清掉，所以这里的成员资格就是「真的有人提供」，
          不会出现「key 在表里但没人的假满足」。
        """
        return isinstance(capability, str) and capability in self.keys()

    def __iter__(self) -> Iterator[str]:
        """遍历当前**有人提供**的能力名。"""
        return iter(sorted(self.keys()))

    def snapshot(self) -> dict[str, list[str]]:
        """依赖表快照（诊断用）。"""
        with self._lock:
            return {cap: sorted(ids) for cap, ids in sorted(self._table.items())}

    def clear(self) -> None:
        """清空依赖表（仅供测试）。"""
        with self._lock:
            self._table.clear()
            self._requirements.clear()


_default_table: DependencyTable | None = None
_default_lock = threading.Lock()


def get_dependency_table() -> DependencyTable:
    """取进程级默认依赖表（单例）。"""
    global _default_table
    if _default_table is None:
        with _default_lock:
            if _default_table is None:
                _default_table = DependencyTable()
    return _default_table


def reset_dependency_table() -> None:
    """重置默认依赖表（仅供测试）。"""
    global _default_table
    with _default_lock:
        _default_table = None


__all__ = [
    "DependencyTable",
    "NotifyOutcome",
    "PluginRequirements",
    "get_dependency_table",
    "reset_dependency_table",
]
