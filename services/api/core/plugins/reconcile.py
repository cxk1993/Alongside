"""启动对账：把上次退出时的状态「对平」。

★ 为什么这是整个状态机里**风险最高**的地方
--------------------------------------------
正常的关闭是有序的——收请求、撤 effect、写终态、退出。
但**崩溃是没有顺序的**：进程可能在任何一个中间步骤上消失，
于是数据库里留下的就是一个「半截」的状态。

★ 七种遗留状态，七种处置
-------------------------
| 上次退出时 | 本次启动应做 | 为什么 |
|---|---|---|
| ACTIVE | 回退到 PENDING，重新加载 | 不能假定「上次是 ACTIVE、这次也还是」——代码可能更新了、依赖可能不再满足 |
| PAUSED | 回退到 PENDING，重新加载 | 但恢复后仍应回到 PAUSED 而非 ACTIVE（PAUSED 是「人有意停的」） |
| LOADING | **先补偿 → 回 PENDING → 再 LOADING** | ★ 最危险：上次加载没完成，可能留下部分 effect |
| RELOADING | **同 LOADING 处置：补偿 → 回 PENDING → 再加载** | ★ 上次升级没完成。`RELOADING` 与 `UNLOADING` 处置**相反**，见下 |
| FAILED | 保持 FAILED，**不自动重试** | 失败原因通常是配置错误，重试一百次结果相同 |
| UNLOADING | **强制跑完卸载 → TERMINATED** | 卸载不能半途而废，残留的 effect 永远不会被清理 |
| TERMINATED | 保持不变 | 终态 |

★ 为什么 RELOADING 与 UNLOADING 必须分开处置（r8 加 RELOADING 的唯一理由）
---------------------------------------------------------------------------
这两个阶段在库里长得几乎一样（都是「正在撤 effect」），但**目标相反**：
`UNLOADING` 奔着删除去（数据表要回收），`RELOADING` 奔着回来去（数据必须保留）。

处置也必须相反：
  · 遗留 UNLOADING ⇒ 跑完卸载。它本来就是一次删除，停下才是错的。
  · 遗留 RELOADING ⇒ 补偿后重新加载。它本来就是一次升级，删掉才是错的。

如果省掉 RELOADING、让 `UNLOADING → PENDING` 走重载，那么「升级中途崩溃」
会被当成「卸载中途崩溃」→ 强制跑完卸载 → **数据表被删**。
一次升级变成一次数据丢失，而且用户是在睡醒之后才发现。这就是加态的代价换来的东西。

★ 「遗留 LOADING」的补偿到底撤什么 —— 一个必须讲清的风险判断
------------------------------------------------------------
技术报告说「先跑补偿」，但**补偿的内容有两个候选，危险程度完全不同**：

  1. **摘路由** —— 安全。路由是进程内状态，重启后本来就不存在；
     这一步是「万一有残留（同一进程内的热重载），把它摘掉」。
  2. **回滚迁移** —— **危险**。台账只记「哪些版本执行过」，
     **不记「上次执行到哪一步」**。于是差分回滚可能会把
     「上次已成功、且表里已有历史数据」的表给删掉。

  ⇒ 所以本实现的补偿**只做第 1 步（摘路由）**，**不回滚迁移**。
    回滚迁移留到「明确知道失败点」时再做（那需要 effect 台账，
    是技术报告 §4.7 说的「Track 的完整版」，本轮不做）。

  ⇒ 那「上次建了一半的表」怎么办？—— **不用管**：
    迁移台账是**幂等**的，重新走一遍 enable 时，已执行的版本会被跳过、
    未执行的那一步会接着做。所以「回到 PENDING 再加载」本身就是安全的。

★ 「遗留 FAILED 不自动重试」
---------------------------
理由不是「懒得重试」，而是**重试在这个场景下没有意义且有害**：
加载失败的原因绝大多数是确定性的（`kernelApi` 不兼容、迁移脚本有语法错误、
依赖的能力无人提供），这类错误不会因为多试几次而改变；
而每次重试都会走一遍「部分提交 → 失败」的流程，往数据库里留下新的残留。

★ 所以本模块**必须返回「需人工确认」的清单**——如果只是「什么都不做、
也不说」，这些插件会**静默地一直不工作**：使用者看不到、AI 看不到，
直到某天有人发现「这个功能怎么没了」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.logging import get_logger
from core.plugins.phase import Phase

log = get_logger("kernel.reconcile")


@dataclass
class ReconcilePlan:
    """一次启动对账的处置计划。"""

    #: 需要回退到 PENDING 并重新加载的插件
    reload: list[str] = field(default_factory=list)
    #: 遗留 LOADING：需要先做补偿（摘路由）再重新加载
    compensate: list[str] = field(default_factory=list)
    #: 遗留 RELOADING：升级中途崩了 —— 同 LOADING，补偿后回 PENDING 重新加载。
    #: ★ 绝不能进 finish_unload（那会删表）。
    reload_interrupted: list[str] = field(default_factory=list)
    #: 遗留 UNLOADING：需要强制跑完卸载
    finish_unload: list[str] = field(default_factory=list)
    #: 遗留 FAILED：不自动重试，**需人工确认**
    needs_human: list[str] = field(default_factory=list)
    #: TERMINATED：不动
    keep: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """人话摘要（进日志）。"""
        parts = [f"重新加载 {len(self.reload)} 个"]
        if self.compensate:
            parts.append(f"其中 {len(self.compensate)} 个需先补偿")
        if self.reload_interrupted:
            parts.append(f"其中 {len(self.reload_interrupted)} 个是升级中断（数据保留）")
        if self.finish_unload:
            parts.append(f"强制完成卸载 {len(self.finish_unload)} 个")
        if self.needs_human:
            parts.append(f"需人工确认 {len(self.needs_human)} 个")
        return "；".join(parts)


def plan_reconcile(states: list[dict[str, Any]]) -> ReconcilePlan:
    """按 `plugin_state` 的 `phase` 算出处置计划。

    ★ 判据必须读 **phase 与 last_known_phase 的组合**，不能只读 phase：
      只读 phase 会漏掉「上次崩在中间」这个信息
      （`phase=LOADING` + `last_known_phase=PENDING` 才是完整的描述）。
    """
    plan = ReconcilePlan()
    for row in states:
        plugin_id = row.get("id", "")
        phase = row.get("phase") or Phase.PENDING.value
        if phase == Phase.LOADING.value:
            # ★ 最危险的一个：上次崩在加载中途。
            #   顺序不能变——先补偿，再回 PENDING，再加载。
            #   （若先进 LOADING 再补补偿，新加载的 effect 会和残留的混在
            #     一起，那时再也分不清哪些是这次的、哪些是上次的。）
            plan.compensate.append(plugin_id)
            plan.reload.append(plugin_id)
        elif phase == Phase.RELOADING.value:
            # ★ 上次原地升级崩在中途。**数据一定还在**（reload 不回滚迁移），
            #   所以处置是「补偿 + 重新加载」，与 LOADING 相同。
            #   ★ 绝不能落到 finish_unload：那会跑完卸载、删表，
            #     把一次没做完的升级变成一次数据丢失。
            plan.reload_interrupted.append(plugin_id)
            plan.compensate.append(plugin_id)
            plan.reload.append(plugin_id)
        elif phase in (Phase.ACTIVE.value, Phase.PAUSED.value):
            plan.reload.append(plugin_id)
        elif phase == Phase.FAILED.value:
            plan.needs_human.append(plugin_id)
        elif phase == Phase.UNLOADING.value:
            plan.finish_unload.append(plugin_id)
        else:
            plan.keep.append(plugin_id)
    return plan


def report_needs_human(plan: ReconcilePlan) -> None:
    """把「需人工确认」的插件**点名报出来**。

    ★ 这一步不能省：如果只是「什么都不做、也不说」，这些插件会静默地
      一直不工作——使用者看不到、AI 看不到。
    """
    if not plan.needs_human:
        return
    log.error(
        "以下插件上次加载失败，本次不自动重试，需人工确认：%s",
        "、".join(plan.needs_human),
        extra={"plugins": plan.needs_human},
    )


__all__ = ["ReconcilePlan", "plan_reconcile", "report_needs_human"]
