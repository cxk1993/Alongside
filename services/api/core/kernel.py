"""内核原语：`kernel.effect()` —— 注册即带逆的统一施加原语。

★ 为什么需要它
--------------
现状是「谁注册谁自己写撤销」：挂路由的人写摘路由、订阅事件的人写退订。
这种做法有三个**必然**的漏洞（不是「可能发生」，是「一定会发生」）：

  1. **漏写** —— 新来的人根本不知道要写撤销。注册的 API 用起来太自然
     （`registry.mount(router)` 一行就够），撤销是「另外一件事」，
     没有任何东西提示他必须做。
  2. **写错顺序** —— 撤销必须**逆序**（后注册的可能依赖先注册的资源），
     但「逆序」这件事没有任何地方提示，写正序的人从代码上看不出问题。
  3. **写在不该写的位置** —— 最常见的写法是「注册成功之后再写撤销」：

         registry.mount(router)              # 注册
         disposers.append(registry.unmount)  # 写撤销

     如果 `mount` 抛异常，第二行**永远不会执行** —— 已挂了一半的路由
     没有任何机制能撤掉。这个插件从此是只「幽灵」：没装成功，但路由真实存在。

三个漏洞的共同根源是：**「撤销」和「注册」是两件分开的事，靠人的自觉保持配对。**
凡是靠自觉维持的配对，迟早会破。

★ 本原语的目标只有一个
----------------------
让「注册了一件事、却忘记写撤销」在**结构上不可能发生**。

★ 三条不可协商的规则
--------------------
| # | 规则 | 违反的后果 |
|:-:|---|---|
| 1 | **逆必须先于 effect 登记** | 中途失败时逆不在栈里，残留无法清理 |
| 2 | **异常时逆序回放** | 正序回放会撤到被依赖的资源，造成二次破坏 |
| 3 | **disposer 必须幂等** | 重复调用会二次破坏 |

★ 为什么要用上下文管理器而不是普通函数
--------------------------------------
普通函数（`kernel.register(thing, undo)`）只能处理「注册成功」这一种情况 ——
它没有机会感知「块内发生了异常」。而上下文管理器天然带
`try/except/else` 三个分支：

  - **块内正常结束（else）**：所有注册都成功了，把这些逆挂到当前插件的
    disposer 栈上，等卸载/暂停时统一回放。
  - **块内抛异常（except）**：注册到一半失败了，**立刻**把**已经生效**的
    部分逆序撤回来 —— 不动还没执行的部分，也不必等整个插件卸载。

这个区别在加载阶段很重要：插件加载到一半失败时，我们希望它「当场回到
干净状态」，而不是「带着半截 effect 躺在 FAILED 状态里等人工处理」。
后者会让 FAILED 的语义变得复杂 —— 「FAILED 的插件到底有没有残留？」
这个问题就没法回答了。

★ 「逆序」在哪里发生
--------------------
`except` 分支里的 `reversed` 是「**就地回放**」：要立刻撤销，按逆序执行。
`else` 分支里**不翻转** —— 栈里存的始终是**施加顺序**（A→B→C），
真正回放时（阶段转移）才 `reversed` 一次，得到 C→B→A。

★ 一条容易踩的坑：翻转两次等于正序。
若在压栈时翻一次、回放时再翻一次，净效果是「按施加顺序撤销」——
那会撤到被依赖的资源，造成二次破坏。所以本实现**只在回放处翻一次**。
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum

from core.errors import AppError
from core.logging import get_logger

log = get_logger("kernel.effect")


class KernelError(AppError):
    """内核原语的用法错误。"""


class EffectKind(StrEnum):
    """effect 的两类。

    ★ 这个分类不是为了好看，而是 **PAUSED 阶段能否成立的前提**。

    PAUSED 的语义 = 「撤销运行期 effect、保留数据 effect」。要做到这一点，
    系统必须能区分「哪些东西属于服务、哪些属于数据」：

    | kind | ACTIVE → PAUSED 时 | 包含哪些 |
    |---|---|---|
    | `RUNTIME` | **撤销** | 定时任务、事件订阅、对外路由 |
    | `DATA` | **保留** | 迁移、表结构 |

    为什么不能一律全撤：全撤的话 PAUSED 就退化成「卸载再装」——
    恢复时要重跑迁移，成本高、风险大（迁移可能失败），那还不如直接卸载。
    为什么不能一律保留：全保留的话定时任务还在跑、路由还通着，
    与「暂停」的语义直接矛盾 —— 暂停就成了「假装暂停」。

    所以**声明 `kind` 就是在声明「暂停我时，该不该撤掉它」**。
    """

    RUNTIME = "runtime"
    DATA = "data"


@dataclass
class EffectEntry:
    """栈上的一条 effect：标签 + 种类 + 逆操作。"""

    label: str
    kind: EffectKind
    undo: Callable[[], None]
    # ★ 幂等护栏：同一个逆可能被多个路径调用（卸载时、暂停时、异常回滚时，
    #   这些路径可能重叠）。用一个「已回放」标记保证只真正执行一次。
    _replayed: bool = field(default=False, repr=False)





class _EffectHandle:
    """effect 块内的句柄：登记「做什么」与「怎么撤」。

    ★ `add()` 里那两行的顺序是全篇最关键的一个细节：

        self._disposers.append(undo)   # ← 先登记逆
        do()                            # ← 再施加

    如果反过来写（先 `do()`，成功之后再 `append(undo)`），会出现一个无法
    补救的漏洞：`do()` 执行到一半抛异常 —— 比如路由挂载成功了、但事件订阅
    失败 —— 这时 `do()` 整体抛出，后一行 `append(undo)` **永远不会执行**。
    结果就是「路由已经挂上了，但撤销路由的逆从来没被登记过」。

    先登记后施加，就彻底避开了这条路：只要 `add` 被调用过，逆就已经在栈里了，
    无论 `do()` 后面发生什么，撤销都跑得掉。
    """

    __slots__ = ("_disposers", "_kind", "_label")

    def __init__(self, label: str, kind: EffectKind) -> None:
        self._label = label
        self._kind = kind
        self._disposers: list[EffectEntry] = []

    @property
    def label(self) -> str:
        return self._label

    @property
    def kind(self) -> EffectKind:
        return self._kind

    def add(
        self,
        do: Callable[[], None],
        undo: Callable[[], None],
        *,
        kind: EffectKind | None = None,
    ) -> None:
        """登记一条 effect：施加 `do`，撤销 `undo`。

        ★ `do` 与 `undo` 任一为 `None` 直接报错 —— 不允许「只施加、不撤销」
        的调用存在。这条检查看起来多余，但它是「effect 必须成对」这条
        不变量的**最后一道兜底** —— 即使门禁没拦住，运行时也会拒绝。

        ★ `kind` 可逐条覆盖块级默认（本轮加）。
          为什么需要它：一次装载里的各步**生命周期不同**——
            - 路由挂载 / 能力提供 ⇒ RUNTIME（暂停时撤，停止对外服务）
            - 迁移 ⇒ DATA（暂停时保留，表留着等下次启用）
          若一个块只能一种 kind，就得拆成嵌套块，而嵌套块的逆序
          极易搞错（这轮实现时就在这里绕了一圈）。
          逐条声明 = 「每步自己说清自己属于哪一类」，没有顺序陷阱。

          ★ 不给时用块级默认（`self._kind`），保持既有调用不变。
        """
        if do is None or undo is None:
            raise KernelError("effect.add 的 do 与 undo 都不允许为 None")
        # ★ 逆先登记（见类 docstring）
        entry = EffectEntry(
            label=self._label,
            kind=kind if kind is not None else self._kind,
            undo=undo,
        )
        self._disposers.append(entry)
        do()

    def __len__(self) -> int:
        return len(self._disposers)


def _safe_call(undo: Callable[[], None], label: str) -> None:
    """执行一个逆，失败只记日志、绝不上抛。

    ★ 一个逆失败不应该阻止其余逆执行 —— 否则一个坏 effect 会把整个
    插件的清理过程卡死（而且原始异常会被掩盖）。
    """
    try:
        undo()
    except Exception as exc:  # noqa: BLE001 —— 撤销失败不许中断整条链
        log.warning(
            "effect 撤销失败（已记录，不中断其余撤销）",
            extra={"effect": label, "error": f"{type(exc).__name__}: {exc}"},
        )


@dataclass
class EffectStack:
    """某个插件的 effect 栈（按施加顺序排列，末尾是最后施加的）。"""

    plugin_id: str
    entries: list[EffectEntry] = field(default_factory=list)

    def replay(self, *, kinds: set[EffectKind] | None = None) -> None:
        """逆序回放已施加的逆。

        `kinds` 为 None 表示全撤；给了集合则只撤这些种类
        （PAUSED 只撤 RUNTIME 走这条路径）。

        ★ 必须逆序：effect 的施加顺序是 A→B→C，撤销就只能是 C→B→A。
        正序撤会出现「B 依赖 A 的资源、但 A 已经先撤了」这类问题。
        """
        # ★ 先把要撤的取出来并从栈里移除，再回放 —— 这样回放过程中若
        #   触发新的 effect 登记，不会被这一轮误撤。
        if kinds is None:
            victims = list(reversed(self.entries))
            self.entries.clear()
        else:
            victims = [e for e in reversed(self.entries) if e.kind in kinds]
            self.entries = [e for e in self.entries if e.kind not in kinds]
        for entry in victims:
            if entry._replayed:
                continue
            entry._replayed = True
            _safe_call(entry.undo, entry.label)

    def retain(self, kinds: set[EffectKind]) -> None:
        """只保留这些种类的 effect（其余从栈里摘掉，但**不执行**其逆）。"""
        self.entries = [e for e in self.entries if e.kind in kinds]

    def __len__(self) -> int:
        return len(self.entries)


class Kernel:
    """内核原语的持有者。

    ★ 本类**不认识任何业务插件**。它只提供两件事：
      1. `effect()` —— 注册即带逆的统一原语；
      2. 按插件 id 保存 effect 栈，供阶段转移时回放。

    「当前插件」用 contextvar 风格的**线程局部**上下文传递：
    插件在 `with kernel.effect(...)` 块内登记的一切，自动归到当前插件名下。
    """

    def __init__(self) -> None:
        self._stacks: dict[str, EffectStack] = {}
        self._lock = threading.RLock()
        # 当前正在装载的插件 id（effect 归属）。用 threading.local 而非
        # contextvar：effect 的登记发生在同步代码里，而插件的装载可能被
        # 委托到线程池（此时用 contextvar 反而会串味）。
        self._local = threading.local()

    # ── 当前插件上下文 ──────────────────────────────────────────────

    @property
    def current_plugin_id(self) -> str | None:
        return getattr(self._local, "plugin_id", None)

    def _set_current(self, plugin_id: str | None) -> None:
        self._local.plugin_id = plugin_id

    @contextmanager
    def plugin_scope(self, plugin_id: str) -> Iterator[None]:
        """声明「接下来的 effect 归属于 plugin_id」。

        ★ 这不是「阶段」——阶段由 `core/plugins/phase.py` 管。
          这里是「归属」，回答的是「这条逆该挂在谁名下」。
        """
        prev = self.current_plugin_id
        self._set_current(plugin_id)
        try:
            yield
        finally:
            self._set_current(prev)

    # ── effect 原语 ──────────────────────────────────────────────────

    @contextmanager
    def effect(
        self,
        label: str,
        *,
        kind: EffectKind = EffectKind.RUNTIME,
        plugin_id: str | None = None,
    ) -> Iterator[_EffectHandle]:
        """注册即带逆。所有 effect 必须通过它。

        参数
        ----
        label : 效果的标签（诊断用：撤销失败时日志里能看到是哪一个）
        kind  : RUNTIME（暂停时撤） / DATA（暂停时保留）
        plugin_id : 归属的插件；不给则取当前 scope 的插件

        ★ 块内抛异常 → 立刻逆序回放**已登记**的部分（只回到失败点）。
          正常退出 → 逆挂到该插件的 effect 栈，等卸载/暂停时统一回放。
        """
        owner = plugin_id if plugin_id is not None else self.current_plugin_id
        handle = _EffectHandle(label=label, kind=kind)
        try:
            yield handle
        except BaseException:
            # ★ 就地回放：块内失败 → 把本块已登记的部分逆序撤回来。
            #   不动还没执行的部分（它们根本没登记），也不必等整个插件卸载。
            for entry in reversed(handle._disposers):
                if entry._replayed:
                    continue
                entry._replayed = True
                _safe_call(entry.undo, entry.label)
            raise
        else:
            # ★ 正常退出：逆挂到该插件的栈上。
            #   栈里存的是**施加顺序**（A→B→C），回放时再 `reversed` 才是逆序。
            #   ★ 这里刻意**不**再翻转一次：翻转两次 = 正序 = 违反规则 2。
            #   （技术报告 §4.2 写的是「压栈时翻一次」，但同报告 §4.8.4 的
            #     回放写法是 `for ... in reversed(stack)` —— 两者相乘等于正序。
            #     §4.8.4 的语义与规则 2 一致，故以它为准。）
            if owner is not None and handle._disposers:
                with self._lock:
                    stack = self._stacks.setdefault(owner, EffectStack(plugin_id=owner))
                    stack.entries.extend(handle._disposers)

    # ── effect 栈的查询与回放（供阶段转移调用）────────────────────────

    def stack(self, plugin_id: str) -> EffectStack:
        """取（或建）某插件的 effect 栈。"""
        with self._lock:
            return self._stacks.setdefault(plugin_id, EffectStack(plugin_id=plugin_id))

    def effect_count(self, plugin_id: str) -> int:
        """该插件当前挂着的 effect 条数（诊断用）。"""
        with self._lock:
            stack = self._stacks.get(plugin_id)
            return len(stack.entries) if stack else 0

    def replay_all(self, plugin_id: str) -> None:
        """全撤某插件的 effect（UNLOADING / TERMINATED 用）。"""
        with self._lock:
            stack = self._stacks.get(plugin_id)
            if stack is None:
                return
            stack.replay(kinds=None)
            self._stacks.pop(plugin_id, None)

    def replay_runtime(self, plugin_id: str) -> None:
        """只撤 RUNTIME 类 effect（PAUSED 用），DATA 类留在栈上。

        ★ 要对栈做**持久化改写**（把已撤的 RUNTIME 从栈里摘掉）：
          恢复时只需要重建 RUNTIME，如果栈里还留着已经撤过的 RUNTIME 逆，
          恢复时会重复撤销或者撤错对象。
        """
        with self._lock:
            stack = self._stacks.get(plugin_id)
            if stack is None:
                return
            stack.replay(kinds={EffectKind.RUNTIME})

    def drop_stack(self, plugin_id: str) -> None:
        """丢弃某插件的 effect 栈（不执行逆）——仅供测试与诊断。"""
        with self._lock:
            self._stacks.pop(plugin_id, None)

    def stacks(self) -> dict[str, EffectStack]:
        """当前所有 effect 栈的快照（诊断用）。"""
        with self._lock:
            return dict(self._stacks)


_default_kernel: Kernel | None = None
_default_lock = threading.Lock()


def get_kernel() -> Kernel:
    """取进程级默认 Kernel（单例）。"""
    global _default_kernel
    if _default_kernel is None:
        with _default_lock:
            if _default_kernel is None:
                _default_kernel = Kernel()
    return _default_kernel


def reset_kernel() -> None:
    """重置默认 Kernel（仅供测试）。"""
    global _default_kernel
    with _default_lock:
        _default_kernel = None


__all__ = [
    "EffectEntry",
    "EffectKind",
    "EffectStack",
    "Kernel",
    "KernelError",
    "get_kernel",
    "reset_kernel",
]
