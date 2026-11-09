"""插件生命周期状态机（Phase Machine）。

★ 为什么需要它
--------------
现状：`plugin_state` 只有一个 `enabled` 布尔，它在插件被启用时置 true、
被禁用时置 false。问题**不只是「只有两个状态」，更是「这个状态并不被记录」**。

现在的加载流程是四步串行的：**跑迁移 → 挂路由 → 订阅事件 → 执行 onEnable**。
这四步之间没有任何地方写下「我现在走到第几步了」——状态只存在于执行流的
当下，执行流一断，状态就消失了。这就是「隐式」的含义。

隐式状态带来四个具体的故障（不是理论推演，是现有代码的真实风险）：

  1. **部分提交** —— 插件在第三步失败，前两步的效果（表建好、路由挂上）
     都还在，但没有任何记录说「它现在处于半成品状态」。重启后系统读到
     `enabled=true`，按「正常插件」对待，于是路由被挂两次、订阅被注册两次。
  2. **孤儿路由** —— 禁用流程是「先摘路由，再跑 on_disable」。若摘路由成功、
     on_disable 抛异常，插件就卡住了：数据还在、状态还是启用，但路由已经
     摘掉。此时重新启用会出现「路由挂上了，但 effect 栈里有两份」的错位。
  3. **永远不自愈也永远不失败** —— 加载失败的插件没有任何标记，于是每次
     进程重启都重试一次，每次都重复一遍上面的部分提交。它既不会被标为
     失败（没人注意到），也不会好转（失败原因通常是配置错误）。
  4. **无法暂停** —— 想临时停掉一个插件，唯一的手段是卸载——而卸载会清
     数据。于是「暂时不用」和「永久删除」被迫共用同一个动作。

这四个问题的共同根源是同一个：**状态没有被显式记录，所以系统不知道
自己在哪一步**。补上「状态」这个信息，四个问题会同时消解。

★ 七个阶段
----------
| 阶段 | 含义 | 怎么进入 | 怎么离开 |
|---|---|---|---|
| `PENDING` | 已发现，等待前置条件 | 发现阶段结束 | 条件满足→LOADING；出错→FAILED；不启用→TERMINATED |
| `LOADING` | 正在装载 | 前置条件满足 | 全成功→ACTIVE；任一步失败→FAILED |
| `ACTIVE` | 正常工作 | 装载全部成功 | 暂停→PAUSED；出错→FAILED；卸载→UNLOADING |
| `PAUSED` | 数据保留，停止对外服务 | 从 ACTIVE 暂停 | 恢复→LOADING；卸载→UNLOADING |
| `FAILED` | 出错停住，保留现场 | 装载失败 / 运行时致命错误 | 重置→PENDING；重试→LOADING；放弃→TERMINATED |
| `UNLOADING` | 正在撤销 effect | 从 ACTIVE/PAUSED 卸载 | 撤销完成→TERMINATED |
| `TERMINATED` | 已卸载，资源回收 | 卸载完成 | **无出口**（终态） |

★ 为什么恰好七个（不多不少）
---------------------------
每多一个阶段，合法转移表的规模就扩大一圈（n 个阶段有 n² 个组合，
七态是 49 个），而这 49 个组合大多数需要人工判断合法性、写成代码、
写测试覆盖。七态是在「能表达所有需要区分的情形」和「转移表仍然可以
人工穷举校验」之间的平衡点。

刻意多加阶段（比如把 LOADING 拆成「跑迁移」「挂路由」）会让转移表膨胀到
无法维护，而收益只是诊断粒度更细 —— 那个需求可以用日志解决。

★ 为什么必须有 PAUSED
---------------------
这是七个状态里最容易被质疑的一个（「直接卸载不就行了」），也是最不能省的。
没有 PAUSED，「临时停用」只能靠卸载实现，代价是数据丢失。而实际使用中
「临时停用」的场景远多于「彻底删除」——插件出问题想先关掉观察一下、
某个功能暂时不想用但以后还会用、AI 在试验某个插件而想保留回退余地。

而且 PAUSED 的成本比卸载低得多：卸载要撤全部 effect（包括迁移回滚），
恢复时要重跑迁移；PAUSED 只撤运行时 effect、保留数据 effect，恢复时
**不需要重跑迁移**。

★ 为什么必须有 FAILED
---------------------
因为「出错了」和「还等着」是两种完全不同的处境，处理方式相反：PENDING
只需要等（条件满足了自然会走），FAILED 需要有人去看（等一万年也不会自己好）。
如果两者混在一个状态里，启动时就没有办法区别对待。

★ 为什么 FAILED 有回到 PENDING 的出口，而 TERMINATED 没有
-----------------------------------------------------------
这不是随意的。FAILED 意味着「这次尝试失败了」，但插件本身还在、代码还在、
数据还在，所以「再试一次」是合理的；而 TERMINATED 意味着资源已经全部回收
（包括数据表），要再使用必须走一次完整的安装流程（重新注册、重新建表、
重新分配 id），那是一次**新的生命周期**，不是同一个状态机里的转移。

★ 转移校验必须放在「唯一入口函数」里
------------------------------------
有三种放法，只有一种是安全的：

  1. **放在调用方** —— 最坏。调用点会散落在 manager、activator、以及将来的
     AI 改造入口里，每新增一个调用点就多一个漏判的机会。
  2. **放在阶段字段的 setter 里** —— 看起来集中了，但 Python 的属性赋值很容易
     被绕过（直接改 `__dict__`、或用 ORM 的批量 update），而且 setter 拿不到
     「为什么转移」这个上下文，没法写审计。
  3. **做成唯一入口函数** —— 正解。所有转移都必须调用 `transition()`，
     函数内部做三件事：校验合法性、改状态、写审计。

所以本模块**导出唯一的 `transition()`**，且 G16 门禁守着「除本文件外
全仓不得出现 `.phase =` 赋值」。
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from core.errors import AppError
from core.logging import get_logger

log = get_logger("kernel.phase")


class Phase(StrEnum):
    """插件生命周期的七个阶段。

    ★ 为什么用 `StrEnum` 而不是 `str, Enum`：
      `str, Enum` 的 `str(Phase.ACTIVE)` 是 `'Phase.ACTIVE'`（类名.成员名），
      而 `StrEnum` 是 `'ACTIVE'`——**枚举的字符串值本身就是它的标识**。
      阶段值会直接进审计记录、进日志、进 API 响应，
      用 `str, Enum` 会让审计里出现 `Phase.ACTIVE` 这种「带前缀的阶段名」，
      与数据库里存的裸值 `'ACTIVE'` 不一致——
      同一件事在两处长得不一样，是查 bug 时的噩梦。
      （`StrEnum` 需要 Python 3.11+，本项目 requires-python >= 3.11 ✓）
    """

    PENDING = "PENDING"
    LOADING = "LOADING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    UNLOADING = "UNLOADING"
    TERMINATED = "TERMINATED"


class IllegalTransition(AppError):
    """非法阶段转移。"""

    status_code = 409
    title = "Conflict"

    def __init__(self, plugin_id: str, frm: str, to: str) -> None:
        super().__init__(
            f"插件「{plugin_id}」不能从 {frm} 转移到 {to}",
            extra={"plugin_id": plugin_id, "from": frm, "to": to},
        )
        self.plugin_id = plugin_id
        self.frm = frm
        self.to = to


class PhaseReason(StrEnum):
    """阶段变化的原因（必须枚举化，不能用自由文本）。

    ★ 为什么必须枚举而不是自由文本：自由文本没法做聚合分析（「这个月
    有多少次是依赖导致的暂停」），也没法做门禁（「AI 提交的转移不能带
    manual 这个理由」）。
    """

    MANUAL = "manual"
    DEPENDENCY_LOST = "dependency-lost"
    DEPENDENCY_SATISFIED = "dependency-satisfied"
    STARTUP_RECONCILE = "startup-reconcile"
    STARTUP = "startup"
    LOAD_ERROR = "load-error"
    AI_REQUEST = "ai-request"
    UNINSTALL = "uninstall"
    RUNTIME_ERROR = "runtime-error"


#: 合法转移表。行是起点，列是终点。
#:
#: ★ 三个最需要机器强制的禁止转移：
#:   1. `LOADING → PAUSED` —— 加载中间状态无法被安全地暂停（可能迁移执行了
#:      一半、路由挂了一半、订阅了一半），暂停后「该恢复哪些」无法回答。
#:      正确做法是让加载要么跑完、要么失败，然后对终态执行暂停。
#:   2. `TERMINATED → 任意` —— 终态。资源已回收（含数据表已删）。「重新启用」
#:      是一次**新的安装**，不是同一个状态机里的转移。写成转移会让「id 一生
#:      只用一次」这条约束失效。
#:   3. `ACTIVE → LOADING` —— 这对应「热重载」的自然想法，但旧的 effect 还没
#:      撤、新的就开始登记，路由会挂两次、订阅会注册两次，且无法区分哪些是
#:      新的、哪些是旧的。正确的重载路径是四步：
#:      `ACTIVE → UNLOADING → PENDING → LOADING → ACTIVE`。
LEGAL: dict[Phase, frozenset[Phase]] = {
    Phase.PENDING: frozenset({Phase.LOADING, Phase.FAILED, Phase.TERMINATED}),
    Phase.LOADING: frozenset({Phase.ACTIVE, Phase.FAILED, Phase.TERMINATED}),
    Phase.ACTIVE: frozenset({Phase.PAUSED, Phase.FAILED, Phase.UNLOADING}),
    Phase.PAUSED: frozenset(
        {Phase.LOADING, Phase.ACTIVE, Phase.FAILED, Phase.UNLOADING}
    ),
    Phase.FAILED: frozenset({Phase.PENDING, Phase.LOADING, Phase.TERMINATED}),
    Phase.UNLOADING: frozenset({Phase.TERMINATED}),
    Phase.TERMINATED: frozenset(),
}

#: 阶段 → 该阶段**允许**出现在路由表里的插件集合的补集。
#: 换句话说：只有 ACTIVE 与 PAUSED 的插件可以挂着路由。
#:
#: ★ 为什么要包含 PAUSED：PAUSED 的语义是「数据保留、但对外停止服务」。
#:   如果只把阶段标成 PAUSED 却不摘路由，就会出现「阶段说『暂停』，行为却
#:   在『服务』」的自相矛盾状态——运维以为它停了、实际还在工作，排查会被
#:   引向完全错误的方向；依赖判断也会失效（别的插件判断「这个能力现在可用吗」
#:   时依据的是阶段，而阶段和实际行为对不上）。
#:
#: ★ 所以「摘路由」这个动作**不属于「卸载」，而属于「阶段转移」**——它必须挂在
#:   转移的统一入口里，而不是让各个调用点自己记得摘。否则一定会有一条路径
#:   漏掉，最后以「孤儿路由」的形式暴露。
MOUNTABLE_PHASES: frozenset[Phase] = frozenset({Phase.ACTIVE, Phase.PAUSED})


#: ★ 一处**有意保留的差异**（技术报告 §3.9.1 与现有实现的分歧）
#:
#: 技术报告设想 `plugin_state.enabled` 是 `phase ∈ {ACTIVE, PAUSED}` 的派生属性
#: （PAUSED 仍算「已启用」——人有意停的，随时可恢复，且不丢数据）。
#:
#: 而**现有实现**在 disable 时写 `enabled=False`，前端与既有测试都依赖它
#: （「已禁用」是一个用户可见的状态）。
#:
#: ⇒ 本轮**不改动现有语义**：`enabled` 继续表示「对外是否可用」
#:   （PAUSED → False），`phase` 表达更细的阶段。两者在
#:   ACTIVE / PENDING / FAILED 上一致，唯一分歧是 PAUSED。
#:
#: ⇒ 将来若要改成技术报告的派生语义，**必须同步改前端 + MCP 工具面 +
#:   既有断言**——那是一次独立的、有明确收益的改造，不该顺手做掉。
#:   （这个差异记在这里，是为了将来有人看到「PAUSED 但 enabled=False」
#:     时不会当成 bug 去"修"——它是有意的。）
ENABLED_DERIVATION_DIVERGES_AT = Phase.PAUSED


def is_legal(frm: Phase | str, to: Phase | str) -> bool:
    """判断一次转移是否在合法转移表里。

    ★ 对角线（自己 → 自己）返回 **False**——状态图上本来就没有自环。
      但 `transition()` 把「转到当前阶段」当**幂等空操作**处理：
      不抛错、不改状态、不写审计。

      这两件事不矛盾：「不合法」说的是「状态图上没有这条边」，
      「空操作」说的是「调用它不产生任何效果」。
      ★ 所以 `is_legal` 与 `LEGAL` 严格一致——**单一真相**，不搞两套语义。
    """
    try:
        src = Phase(frm)
        dst = Phase(to)
    except ValueError:
        return False
    return dst in LEGAL[src]


class PhaseTransitionHook:
    """阶段转移时需要连带做的事。

    ★ 为什么不把「摘路由」「回放 effect」直接写在 `transition()` 里：
      那会让状态机认识路由表和 effect 栈 —— 而「阶段」是内核原语，
      「路由表」和「effect 栈」也是内核原语，三者本可平级。
      用钩子让**调用方**决定连带动什么，状态机只管「能不能变」。

      这也让状态机可以被独立测试：不需要先造一个 app。
    """

    def on_transition(
        self,
        plugin_id: str,
        frm: Phase,
        to: Phase,
        *,
        reason: PhaseReason,
    ) -> None:
        """转移完成后调用。抛异常会被记录但**不改变已完成的转移**。

        ★ 为什么钩子抛异常不回滚转移：阶段已经改完、审计已经写完，
          这两件事同事务保证了「状态和留痕一致」。若因为钩子失败而回滚状态，
          反而会出现「审计写了状态没变」的不一致。钩子的失败属于
          「连带动作没做完」，记日志并暴露出来即可。
        """


class PhaseMachine:
    """阶段状态机。

    ★ 状态存哪儿：**不在内存里**。
      `plugin_state` 表是唯一真相（`phase` / `last_known_phase` /
      `phase_changed_at` / `phase_reason`）。本类只做**判定与编排**，
      实际的读写由注入的读写器完成——这样它既不 import db（分层不破），
      也能被单元测试。

    ★ `last_known_phase` 的精确语义：它记的**不是**「上次退出时的状态」，
      而是**「最近一次阶段变化【之前】的状态」**——保存的是**转移的起点**。

      为什么这样设计：这样才能精确表达「它正要从 A 走到 B，但没走完」。
      进程崩在 LOADING 中途时，两个字段的值是 `phase=LOADING`、
      `last_known_phase=PENDING`，两者组合完整地描述了当时发生的事。
      如果 `last_known_phase` 记的是「退出时的状态」，它就会等于 `phase`
      （都是 LOADING），反而什么信息都没有。
    """

    def __init__(
        self,
        *,
        read: Callable[[str], Any] | None = None,
        write: Callable[[str, dict[str, Any]], None] | None = None,
        audit: Callable[[str, str, dict[str, Any]], None] | None = None,
        hook: PhaseTransitionHook | None = None,
    ) -> None:
        self._read = read
        self._write = write
        self._audit = audit
        self._hook = hook
        self._lock = threading.RLock()

    # ── 阶段判定 ────────────────────────────────────────────────────

    def phase_of(self, plugin_id: str) -> Phase:
        """读某插件当前阶段（无记录时视为 PENDING）。"""
        if self._read is None:
            return Phase.PENDING
        row = self._read(plugin_id)
        if not row:
            return Phase.PENDING
        raw = row.get("phase") if isinstance(row, dict) else getattr(row, "phase", None)
        if not raw:
            return Phase.PENDING
        try:
            return Phase(raw)
        except ValueError:
            # ★ 库里出现了不认识的阶段 —— 不猜、不静默当 PENDING。
            #   当成 PENDING 会让一个状态异常的插件被当成「刚发现」而重新加载。
            log.error(
                "plugin_state 里有不认识的阶段值（拒绝猜测）",
                extra={"plugin_id": plugin_id, "phase": raw},
            )
            raise AppError(
                f"插件「{plugin_id}」的阶段值 {raw!r} 不在合法阶段集合内"
            ) from None

    # ── 唯一入口 ────────────────────────────────────────────────────

    def transition(
        self,
        plugin_id: str,
        to: Phase | str,
        *,
        reason: PhaseReason | str = PhaseReason.MANUAL,
        actor: str = "kernel",
    ) -> None:
        """**唯一**的阶段转移入口。

        内部做三件事：校验合法性、改状态、写审计。
        ★ 审计与状态变更必须在**同一个事务**里（由 `write` 读法保证）——
          不允许出现「状态改了但没留痕」。一旦这两件事分开，就一定有路径能
          在崩溃时只完成一半，而「改了状态却查不出谁改的」正是最难排查的
          一类问题。
        """
        try:
            dst = Phase(to)
        except ValueError as exc:
            raise AppError(f"未知的阶段 {to!r}") from exc
        try:
            rsn = PhaseReason(reason)
        except ValueError as exc:
            raise AppError(f"未知的阶段原因 {reason!r}") from exc

        with self._lock:
            frm = self.phase_of(plugin_id)
            if dst is frm:
                # ★ 幂等保护：相同阶段不重复记。
                #   依赖变化会触发通知，通知会对每个活着的插件跑一遍满足性判定；
                #   同一个变化被重复通知时，若不做幂等，就会反复写审计、把日志
                #   刷爆，还会让「阶段变化次数」这个指标彻底失真。
                return
            if dst not in LEGAL[frm]:
                raise IllegalTransition(plugin_id, frm.value, dst.value)

            # ★ `last_known_phase` 在改 `phase` **之前**赋值。
            #   顺序不能反：它要记住的是「改之前是什么」，顺序颠倒了记下来的
            #   就成了新状态，启动对账会完全失效。
            payload: dict[str, Any] = {
                "phase": dst.value,
                "last_known_phase": frm.value,
                "phase_reason": rsn.value,
            }
            if self._write is not None:
                self._write(plugin_id, payload)
            if self._audit is not None:
                # ★ 审计与状态变更同事务 —— 由调用方的实现保证（见类 docstring）。
                self._audit(
                    plugin_id,
                    "phase.change",
                    {"from": frm.value, "to": dst.value, "reason": rsn.value, "actor": actor},
                )

        # 钩子在锁外调用：连带动作（摘路由、回放 effect）可能慢，
        # 不该把状态机整个锁住。
        if self._hook is not None:
            try:
                self._hook.on_transition(plugin_id, frm, dst, reason=rsn)
            except Exception as exc:  # noqa: BLE001 —— 钩子失败不回滚已完成的转移
                log.error(
                    "阶段转移的连带动作失败（转移已完成，未回滚）",
                    extra={
                        "plugin_id": plugin_id,
                        "from": frm.value,
                        "to": dst.value,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )

    # ── 启动对账 ────────────────────────────────────────────────────

    def reconcile_on_startup(
        self,
        states: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        """启动对账：把上次退出时的状态「对平」。

        ★ 这是整个状态机里**风险最高**的地方。
          正常的关闭是有序的——收请求、撤 effect、写终态、退出。
          但**崩溃是没有顺序的**：进程可能在任何一个中间步骤上消失，
          于是数据库里留下的就是一个「半截」的状态。

        六种遗留状态，处理方式各不相同：

        | 上次退出时 | 本次启动应做 | 为什么 |
        |---|---|---|
        | ACTIVE | 回退到 PENDING，走正常加载流程 | 代码可能更新了、依赖可能不再满足 |
        | PAUSED | 回退到 PENDING，走正常加载流程 | 但恢复后仍应回到 PAUSED 而非 ACTIVE |
        | LOADING | **先跑补偿 → 回到 PENDING → 再 LOADING** | ★ 最危险：可能留下部分 effect |
        | FAILED | 保持 FAILED，**不自动重试** | 失败原因确定，重试一百次结果相同 |
        | UNLOADING | **强制跑完卸载 → TERMINATED** | 卸载不能半途而废 |
        | TERMINATED | 保持不变 | 终态 |

        ★ 「遗留 LOADING」为什么最危险：这时数据库里的状态是「正在加载」，
          而进程已经死了。可能留下的残留包括：迁移执行了一半、路由挂了一半、
          事件订阅了一半。如果这次启动直接当作正常流程重新加载，就会出现
          「同一件事做两遍」——建表失败（表已存在）、路由重复挂载、订阅注册两次。

        ★ 遗留 FAILED 不自动重试的理由不是「懒得重试」，而是**重试在这个场景下
          没有意义且有害**：加载失败的原因绝大多数是确定性的，这类错误不会
          因为多试几次而改变；而每次重试都会走一遍「部分提交 → 失败」的流程，
          往数据库里留下新的残留。正确的做法是把它标出来、交给人工判断。

        返回值里专门列了「需要人工确认」的插件清单——如果只是「什么都不做、
        也不说」，这些插件会**静默地一直不工作**：使用者看不到、AI 看不到，
        直到某天有人发现「这个功能怎么没了」。
        """
        # ★ 判据的实现**只在 `core.plugins.reconcile` 一处**
        #   （这里委托过去，不复制第二份）——
        #   同一事实两处定义，迟早分叉，而分叉了没人会发现。
        from core.plugins.reconcile import plan_reconcile

        plan_obj = plan_reconcile(states)
        for row in states:
            last_known = row.get("last_known_phase")
            if last_known and last_known not in Phase.__members__:
                log.warning(
                    "plugin_state.last_known_phase 值不合法（对账按 phase 处理）",
                    extra={"plugin_id": row.get("id", ""), "last_known_phase": last_known},
                )
        plan: dict[str, list[str]] = {
            "reload": plan_obj.reload,
            "compensate": plan_obj.compensate,
            "finish_unload": plan_obj.finish_unload,
            "needs_human": plan_obj.needs_human,
            "keep": plan_obj.keep,
        }
        if plan["needs_human"]:
            log.warning(
                "以下插件上次加载失败，本次不自动重试，需人工确认",
                extra={"plugins": plan["needs_human"]},
            )
        return plan


__all__ = [
    "LEGAL",
    "MOUNTABLE_PHASES",
    "IllegalTransition",
    "Phase",
    "PhaseMachine",
    "PhaseReason",
    "PhaseTransitionHook",
    "is_legal",
]
