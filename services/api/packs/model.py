"""包层运行时模型（Pack model）。

★ 三条纪律（报告 §15.5 / §21.4 / §19.4）：
  1. 三样留档各管一件事：`pack.json` 原件 / `pack.lock.json` / `pack_state`。
  2. 用户调整是本地层，装载**只读不写**。
  3. 包不声明能力，只引用与收窄（I11）。

本模块只定义数据形状 —— 不含 IO、不含校验、不含状态转移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

PACK_FORMAT = 1
"""本实现支持的清单格式版本。与 kernelApi 是两个维度（报告 §9）。"""

MAX_INCLUDE_DEPTH = 3
"""嵌套包最大深度（B2-7 / I7）。"""


# ───────────────────────────── 错误 ─────────────────────────────


class PackError(Exception):
    """包层错误基类。`code` 是规则编号（如 `B1-2`），测试与上层据此分类。"""

    code: str = "PACK_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


# ───────────────────────────── 清单 ─────────────────────────────


@dataclass(frozen=True, slots=True)
class MemberRef:
    """成员引用 —— 指向一个插件。"""

    id: str
    version: str
    digest: str | None = None
    """期望摘要。**可选**：只在需要钉死时人写，日常由工具写进锁（§10.4）。"""


@dataclass(frozen=True, slots=True)
class PackRef:
    """被包含的子包（报告 §10.2 第 1 条「展开嵌套包」）。

    ★ 为什么叫 `includes` 而不是 `requires`：
      `requires` 在插件协议里已经表示「能力需求」，包层再用同一个词会造成
      「同名不同义」——这正是评审时指出的最大风险。包包含包，是**组合**关系：
      展开后成员并入、归属是集合、成员冲突一律报错。
    """

    id: str
    version: str


@dataclass(frozen=True, slots=True)
class SecretRef:
    key: str
    title: str
    sensitive: bool = False
    required: bool = False
    for_member: str | None = None


@dataclass(frozen=True, slots=True)
class PermissionBudget:
    """权限预算 —— **上限，不是授予**（红线二：只能收紧，不能放大）。"""

    object_level: str
    capabilities: tuple[str, ...] = ()
    approval_required: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PackManifest:
    pack_format: int
    id: str
    name: str
    version: str
    kind: str
    kernel_api: str
    required: tuple[MemberRef, ...] = ()
    optional: tuple[MemberRef, ...] = ()
    excluded: tuple[str, ...] = ()
    includes: tuple[PackRef, ...] = ()
    config: dict[str, dict[str, Any]] = field(default_factory=dict)
    secrets: tuple[SecretRef, ...] = ()
    permission_budget: PermissionBudget | None = None
    provider_choice: dict[str, str] = field(default_factory=dict)
    """多 Provider 的显式选定：能力名 → 本包成员内的提供者插件 id（报告 §10.5）。

    ★ 为什么这个字段在**包层**，而不是插件层或内核层：
      · 内核不许有业务假设（「哪个插件提供账单读取」是业务判断）；
      · 插件互不知情（让插件选等于要求它知道别人的 provides）；
      · 用户不该成为「系统能不能启动」的必要条件。
      ⇒ **包是唯一知道「包里所有插件」的实体**，这个位置只有它能填。

    ★ 键是**能力名**（带点），值是**成员插件 id**（单段）——两者形状不同，
      写错一边就会被 Schema 挡下。

    ★ 它是**能力级**的判据：只改「这个能力的路由可用性」，
      **不改插件状态**。落成「该插件不激活」是过度杀伤
      （一个插件提供 3 个能力、其中 1 个被选走，整插件停用 ⇒ 另 2 个也废了）。
    """
    entry: str | None = None
    license: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    """`pack.json` 的原始字典 —— 原件留档用（§15.5）。"""
    directory: Path | None = None

    @property
    def all_members(self) -> tuple[MemberRef, ...]:
        return self.required + self.optional

    @property
    def declared_ids(self) -> tuple[str, ...]:
        return tuple(m.id for m in self.all_members)


# ─────────────────────────── 装配快照（锁） ───────────────────────────


@dataclass(frozen=True, slots=True)
class LockedMember:
    """成员记录：**版本 + 内容摘要 + 归属**。

    ★ `digest` 可以是 `None` —— 表示「**这一份没算出摘要**」。
      这不是「摘要为空串」也不是「摘要是一串零」：
      那两种写法都会让「摘要一致」这个判断恒真，
      等于把「变了能发现」这个唯一价值架空。
      `None` 的含义是「没算出来」，是个**待办**，不是一个**结论**。

      什么情况下会没有摘要：插件目录不可达（路径错、权限不足）、
      或发现阶段没拿到目录。§15.4 的开发态豁免是另一回事——
      那是**主动**跳过校验并留痕，不是**被动**算不出来。

    ★ `from_packs` 是**归属集合**（§10.2 第 4 条）：
      同一个成员可能被多个包引入，归属是集合而非单值。
      记下来才能回答「这个成员是谁带进来的」——
      升级其中一个包时，不该把另一个包带来的同一个成员也拽着走。
    """

    id: str
    version: str
    digest: str | None = None
    from_packs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PackLock:
    """**基线锁**（`pack.lock.json`，住在**包目录**里）——「我装的是什么」。

    ★ 它是**对账的基准**，由显式命令 `alongside pack lock` 生成 / 更新，
      装载流程**只读它**（§15.2 规则 2：锁默认只读，只有显式命令才写）。

    ★ 为什么它住在**包目录**而不是数据目录：
      它是**作者声明的期望**，要跟着包一起分发。
      包作者在任意目录跑 `alongside pack lock <dir>`，
      产出的基线能被带走、被别人对账——若住在数据目录，
      别人的机器上根本没有这个文件，基线就无从谈起。

    ★ 「谁在什么时候改了基线」必须有唯一答案：
      显式命令是唯一的写入口（§15.2 规则 2）。

    ★ `self_digest` 是**包级摘要**：「这个包本身是什么」。
      它算的是**包目录的内容**（`pack.json` 原文、内嵌插件、README），
      不算成员插件的目录——成员的摘要是 `members[].digest` 的事。
      算的时候**必须排除 `pack.lock.json` 自己**：
      否则每写一次锁摘要就变一次 ⇒ 摘要恒等于现状 ⇒ 什么也不证明。

    ★ `kernel_api` 记的是「这份基线是在哪个内核接口下钉的」。
      内核接口变了，基线的含义可能也变了——
      不记它，就无法回答「这份锁还是不是当前内核认的那一份」。
    """

    pack_id: str
    pack_version: str
    generated_at: str
    members: tuple[LockedMember, ...] = ()
    self_digest: str | None = None
    kernel_api: str | None = None

    def by_id(self) -> dict[str, LockedMember]:
        return {m.id: m for m in self.members}


@dataclass(frozen=True, slots=True)
class AssemblySnapshot:
    """**装配快照**（`pack.assembly.json`，住在**数据目录**里）——「上次装成了什么」。

    ★ 与基线锁的区别，是本协议最容易被混淆的一处，所以写死在这里：

    | | 基线锁 | 装配快照 |
    |---|---|---|
    | 住在 | 包目录 | 数据目录 |
    | 回答 | 「**我**装的是什么」（作者声明） | 「这台机器**上次**装成了什么」 |
    | 谁写 | 显式命令 `pack lock` | 装载流程（成功后） |
    | 装载时 | **只读**（对账基准） | **重写**（记这次的结果） |
    | 跨机器 | 跟着包分发 | 各机不同 |

    ★ 为什么必须分成两个：
      若只有一个文件且装载时重写它，那「代码被改过 ⇒ 硬拒绝」永远不会触发
      ——每次装载都把它改成当前状态，基线就永远等于现状，§15.3 整条规则失效。
      ⇒ **对账基准不能被对账动作本身改写。**
    """

    pack_id: str
    pack_version: str
    generated_at: str
    members: tuple[LockedMember, ...] = ()
    self_digest: str | None = None
    kernel_api: str | None = None

    def by_id(self) -> dict[str, LockedMember]:
        return {m.id: m for m in self.members}


# ─────────────────────────── 用户本地调整 ───────────────────────────


class AdjustmentAction(StrEnum):
    ENABLE = "enable"
    DISABLE = "disable"
    ADD = "add"
    REMOVE = "remove"


@dataclass(frozen=True, slots=True)
class UserAdjustment:
    """一条用户调整。**不由装载流程写入**（§21.4 规则一）。"""

    member_id: str
    action: AdjustmentAction
    by: str
    at: str


@dataclass(slots=True)
class PackState:
    """`pack_state` —— 用户改了什么。装载只读它。"""

    pack_id: str
    adjustments: list[UserAdjustment] = field(default_factory=list)

    def decision(self, member_id: str) -> AdjustmentAction | None:
        """最后一条调整说了算（后来的覆盖先前的）。"""
        for adj in reversed(self.adjustments):
            if adj.member_id == member_id:
                return adj.action
        return None

    def touched_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for adj in self.adjustments:
            if adj.member_id not in seen:
                seen.append(adj.member_id)
        return tuple(seen)


# ─────────────────────────── 装载报告 ───────────────────────────


class LoadStatus(StrEnum):
    LOADED = "loaded"
    """本次启用成功。"""

    REUSED = "reused"
    """摘要一致且已 ACTIVE —— 差量装配，本次不动它。"""

    SKIPPED = "skipped"
    """可选成员未被选 / 被用户停用。"""

    DEGRADED = "degraded"
    """成员超出包权限预算 —— 该成员降级，不整包失败。"""

    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MemberOutcome:
    id: str
    status: LoadStatus
    version: str | None = None
    reason: str | None = None
    from_packs: tuple[str, ...] = ()
    """归属包是**集合**（§10.2 第 4 条）。"""


@dataclass(frozen=True, slots=True)
class LoadReport:
    pack_id: str
    status: str
    """`loaded` | `failed` | `aborted`。"""

    members: tuple[MemberOutcome, ...] = ()
    overridden: tuple[dict[str, Any], ...] = ()
    user_adjusted: tuple[str, ...] = ()
    new_members: tuple[str, ...] = ()
    """本次新增的成员（不在锁里）——§15.3 要求**显著标出**。"""
    summary_not_verified: tuple[str, ...] = ()
    """摘要**没校验**的成员（§19.3 的 `summaryNotVerified`）。

    ★ 什么算「没校验」：算不出实际摘要（插件目录不可达），
      或清单里没钉摘要、锁里也没有基线可比。
      这与「摘要不符」是**两件事**：不符是**发现了不一致**（硬拒绝），
      没校验是**没能力发现**（留痕放行）。
      ⇒ 一个是结论，一个是待办。
    """
    selected_providers: tuple[dict[str, Any], ...] = ()
    """能力路由留痕：能力名 → 选中的提供者插件 id（§19.3 的 `selectedProviders`）。

    ★ 它回答一个问题：**「我现在的账单数据，是哪个插件在提供」**。
      在有多 Provider 的系统里，这个问题必须随时能回答——
      否则「两个插件都能读账单」这件事对用户永远不可见。

    ★ 每条形如 `{"capability": ..., "provider": ..., "layer": ...}`；
      `layer` 标明这次选择来自哪一层（基包 / 派生包 / 用户层）。
    """
    provider_conflicts: tuple[dict[str, Any], ...] = ()
    """**待裁决**的多 Provider 冲突：能力名 → 候选提供者列表。

    ★ 与 `selected_providers` 是一对：有选定的记在 selected，
      没选定的记在这里。两者都非空 ⇒ 该能力装配后为 unavailable。

    ★ 这不是失败：系统照常起来，只是那个能力不可用。
      「不阻断启动」是刻意的——让内核在包层说话之前就把系统判死，
      等于让包层永远没机会裁决。
    """
    error: str | None = None
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "loaded"

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.members:
            counts[m.status.value] = counts.get(m.status.value, 0) + 1
        return counts
