"""`plugin_state` 的读写封装——状态机与数据库之间的**唯一接口**。

★ 为什么要有这一层
------------------
`PhaseMachine` 本身**不 import db**：它的读写与审计都是注入的。这样它能被
独立测试（不建 app、不连库），也说明「阶段判定」这件事不依赖任何存储细节。

但真实系统里，状态必须落库，而且**「改状态」与「写审计」必须在同一个事务里**
——否则会出现「状态改了但没留痕」，而「改了状态却查不出谁改的」正是最难
排查的一类问题。

这一层就是把那个「同事务」的保证落到代码上：
`apply_transition()` 一次开一个 session，在其中改状态、写审计，然后 commit。

★ 为什么不让 PhaseMachine 直接用 session
-----------------------------------------
如果状态机拿着一个 session 到处传，调用点就散落了（manager、activator、
将来的 AI 改造入口……），而「同事务」这件事要求**一次转移一个 session**。
让状态机保持无状态、把 session 收敛在这一层，转移的边界才是清楚的。
"""
from __future__ import annotations

import json
from typing import Any

from core.logging import get_logger
from core.plugins.phase import Phase, PhaseReason

log = get_logger("kernel.phase.store")

#: 审计动作名。与技术报告 §3.9.2 的 `action="phase.change"` 一致。
AUDIT_ACTION = "phase.change"


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def read_phase_reason(plugin_id: str) -> str | None:
    """读上一次阶段变化的原因（判定「这次暂停是不是依赖造成的」要用）。

    ★ 为什么需要它：自动恢复**只该恢复「因依赖丧失而停」的插件**。
      人手动停的（`phase_reason=manual`）若被自动恢复，
      那「手动停用」就形同失效——人刚点停，系统又把它拉起来。
      而这两种暂停在阶段上**长得一模一样**（都是 PAUSED），
      不看原因就分不出来。
    """
    from core.deps import db_session  # noqa: PLC0415
    from db.models.system import PluginState

    with db_session() as db:
        st = db.get(PluginState, plugin_id)
        return st.phase_reason if st else None


def read_phase(plugin_id: str) -> str | None:
    """读某插件的阶段（无行 → None）。"""
    from core.deps import db_session  # noqa: PLC0415 —— 延迟导入，避免 import 期连库
    from db.models.system import PluginState

    with db_session() as db:
        st = db.get(PluginState, plugin_id)
        return st.phase if st is not None else None


def read_states() -> list[dict[str, Any]]:
    """读所有插件的状态行（启动对账用）。"""
    from sqlmodel import select

    from core.deps import db_session  # noqa: PLC0415
    from db.models.system import PluginState

    with db_session() as db:
        rows = db.exec(select(PluginState)).all()
        return [
            {
                "id": r.id,
                "phase": r.phase,
                "last_known_phase": r.last_known_phase,
                "phase_reason": r.phase_reason,
                "was_satisfied": r.was_satisfied,
                "enabled": r.enabled,
            }
            for r in rows
        ]


def apply_transition(
    plugin_id: str,
    to: Phase,
    *,
    reason: PhaseReason,
    actor: str = "kernel",
    was_satisfied: bool | None = None,
) -> None:
    """改阶段 + 写审计，**同一个事务**。

    ★ `last_known_phase` 在改 `phase` **之前**赋值——
      它要记住的是「改之前是什么」，顺序颠倒了记下来的就成了新状态，
      启动对账会完全失效。

    ★ 审计与状态变更同事务：一旦这两件事分开，就一定有路径能在崩溃时
      只完成一半。
    """
    from core.deps import db_session  # noqa: PLC0415
    from db.models.system import AuditLog, PluginState

    with db_session() as db:
        st = db.get(PluginState, plugin_id)
        if st is None:
            # 状态行不存在：只有「已注册」的插件才有阶段。
            # 凭空建行会让 list_plugins 的「无状态行 ⇒ 按来源取默认」口径漂移。
            log.warning(
                "阶段转移被跳过：plugin_state 里没有这一行",
                extra={"plugin_id": plugin_id, "to": to.value},
            )
            return
        frm = st.phase
        if frm == to.value:
            return
        st.last_known_phase = frm
        st.phase = to.value
        st.phase_changed_at = _now()
        st.phase_reason = reason.value
        if was_satisfied is not None:
            st.was_satisfied = was_satisfied
        # ★ 审计与状态变更同一个 session ⇒ 同一个事务
        db.add(
            AuditLog(
                actor=actor[:64],
                action=AUDIT_ACTION,
                target=plugin_id[:200],
                payload_hash=json.dumps(
                    {"from": frm, "to": to.value, "reason": reason.value, "actor": actor},
                    ensure_ascii=False,
                )[:64],
            )
        )
        db.commit()


def _now() -> Any:
    from db.base import utcnow

    return utcnow()


def set_was_satisfied(plugin_id: str, value: bool) -> None:
    """单独更新满足性缓存（依赖通知用）。

    ★ 它与 `phase` 存在**同一行**——满足性判定和阶段是配套的，
      分开存会出现「阶段是 ACTIVE、但满足性记录是 False」这种自相矛盾的中间态。
    """
    from core.deps import db_session  # noqa: PLC0415
    from db.models.system import PluginState

    with db_session() as db:
        st = db.get(PluginState, plugin_id)
        if st is None:
            return
        st.was_satisfied = value
        db.commit()


__all__ = [
    "AUDIT_ACTION",
    "apply_transition",
    "read_phase",
    "read_states",
    "set_was_satisfied",
]
