"""包提供的配置值 —— 分层落地、留痕、不夺走用户的值。

★ 这一层治什么
--------------
协议 §11 说：包可以给成员提供**配置值**（`config.<plugin-id>`），
而插件层有 `settingsSchema` —— 插件**定义**设置的结构，包**提供**值。

附录 C 把这两者的关系写得很清楚：

| 包层 | 插件层对应 | 关系 |
|---|---|---|
| `config.*` | `settingsSchema` | 插件**定义**设置的结构，包**提供**值 |

★ 但实现里这件事是**空的**
--------------------------
`packs/validate.py` 会**解析** `config`（检查它是对象、逐个成员是对象），
`expand_includes` 会**裁决归属**（同一个成员的 config 只能由一个包提供，B2-8），
但**装配器从头到尾没有读过它**。

⇒ 「包给了配置值」这件事，协议写了、校验器查了、**运行时没做**。
这正是「文档里写了规则而代码没实现 = 规则不存在」要防的那种裂缝。

★ 覆盖粒度：key 级，值的替换是整段
----------------------------------
协议 §19.3 的留痕示例是：

```jsonc
"overridden": [
  { "member": "finance", "path": "config.finance.ledger", "layer": "local.my-pack" }
]
```

`path` 落在**单个 key** 上（`config.finance.ledger`），
不是落在 `config.finance` 这一整段上。

而 §11.1 说「配置覆盖只能是**整段替换**，不做深合并」——
这两句合起来才是完整语义：

| 说的什么 | 指什么 |
|---|---|
| 粒度是 key | 留痕按 key 记；一个 key 一个裁决 |
| 值的替换是整段 | 包给 `ledger` 就得给**完整的**值，不能只给一半再与旧值深合并 |

★ 由此得出关键推论：包**没声明**的 key，包**不许动**
--------------------------------------------------------
「整段替换，不做深合并」的全部意义就是「每一项的值永远只有一个来源」。
若包没声明某个 key，却把该 key 的旧值留着不动，
那这个值就同时有「包声明过」与「包没声明」两个解释——归因又断了。

⇒ 所以：包给的 key ⇒ 包管；包没给的 key ⇒ **包不碰**（哪怕上一个版本给过）。

★ 那「包升级后删掉的配置项」怎么办
------------------------------
答：**不删**。因为「删键」这件事在插件层没有对应能力
（`write_settings` 只做 upsert，没有删除入口），
而 §19.2 的装载流程里也没有「清理不再声明的键」这一步。

⇒ 明确记为**协议缺口**，不自己发明补救：
  · 包删掉的 key ⇒ 旧值**留在**设置里（由用户自己改，或插件自己改）；
  · 留痕里记一条，说明「这个键现在没有包管着」。
  ⇒ 这条写进「待补清单」，不假装已实现。

★ 层序：包层 → 用户覆盖层
-----------------------
协议 §16.1 给了三层：基包 → 派生包 overrides → 用户覆盖层。

但在 `config` 上，**中间那层不存在**：
B2-8 规定「同一个成员的 `config` 只能由一个包提供」，
两个包都想写同一成员的配置 → **报错**，不按装载顺序裁决。

⇒ 所以 `config` 上只有两层：**包层**（唯一那个包）与**用户覆盖层**。
  「派生包的 overrides」覆盖的是**包级字段**，不是 `config`
  （附录 A 术语表明说：「覆盖（`overrides`）：派生包对**包级字段**的覆盖；
  与 `config`（成员配置值）不同层次」）。

★ 用户覆盖层在哪
-----------------
在**用户自己改过的那一层**——`plugin_setting` 表里那些**不是包写的**行。

⇒ 由此得出本层的核心判据：**一个设置项归谁，看「上一次是谁写的」。**

★ 为什么需要「上一次是谁写的」这份记录
------------------------------------
`plugin_setting` 表只有 `(plugin_id, key, value_json)`——
**它不记来源**。于是包与用户写进去的值混成一份，
用户改过之后无法再区分「这个值是包给的还是我给的」。

⇒ 包层在自己的数据目录里记一份「**我写过哪些键、写成什么**」：
  · 键在记录里、且当前值与记录一致 ⇒ 仍是包的值 ⇒ 包可以更新它；
  · 键在记录里、但当前值与记录**不一致** ⇒ 用户改过 ⇒ **包不许再动**；
  · 键不在记录里、但当前有值 ⇒ 是用户（或另一个包）写的 ⇒ **包不许动**；
  · 键不在记录里、当前也没值 ⇒ 没人写过 ⇒ **包补上默认值**。

★ 这四条判据就是「用户覆盖层在上、包层在下」的**可实现形式**。
  它同时满足了 §16.1 的第 2 条：包层写的每一项，用户随时能改掉它。

★ 为什么不写进 `plugin_setting` 当「来源列」
----------------------------------------
加一列意味着改**内核表**（alembic 迁移），而这一列的语义是**包层的事**
（只有包会「以包的身份」写设置）。⇒ 放包层自己的数据目录：
`data/packs/<pack_id>/config.applied.json`。

★ 为什么不落库 `config.<id>.enabled`
---------------------------------
协议 §11 把 `config.<plugin-id>.enabled` 单列（成员的启用状态）。
但**启用状态归用户调整层管**（§21.4 规则一），而协议 §19.2 步骤 10 明写：
装载**不写**用户调整层。

⇒ 所以它**不落库**。它的作用是「这个包希望这个成员是开/关的」：
  · `enabled: false` ⇒ 本次装载**不启用**该成员
    （除非用户自己明确启用过——用户层在上）；
  · `enabled: true` ⇒ 无特殊含义（默认就是启用），只在报告里留痕。

★ 为什么配置落地放在「全部成员启用成功之后」
----------------------------------------
协议 §19.2 第 7 步是**提交点**（此前全部只读）。
若把写配置放在提交点之前 → 预检失败时会留下写过的设置；
若放在 enable 途中 → enable 失败要回滚设置（而「删除某项设置」
在插件层没有对应能力，回滚不干净）。

⇒ 放在**全部成员启用成功之后**：
  · enable 全成功 ⇒ 才写配置，写了就不需要回滚；
  · 单项配置写不进去（插件的 settingsSchema 不接受）⇒ **记为该项跳过**，
    **不让整个包失败**（配置不合意不该让场景不可用——与 §12 第 4 条
    「成员降级，不是整包失败」同一条纪律）。

★ 写配置失败为什么不回滚
----------------------
配置值是**逐项**的：一项不合意不影响其余项。
且此时成员已启用、配置本身不是「能不能用」的前提。
⇒ 失败逐项记留痕，不抛、不回滚、不影响包的成立。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from packs.model import PackManifest

#: `config` 里那个表示「启用状态」的保留键。
#:
#: ★ 它不是插件设置项，而是**成员的启用状态**（协议 §11）——
#:   而启用状态归用户调整层管（§21.4 规则一）。
#: ⇒ 不写进 `plugin_setting`（那是「设置」的语义），
#:   只影响「本次要不要启用这个成员」。
ENABLED_KEY = "enabled"

#: 裁决动作。
#:
#: ★ 为什么分这五个而不是三个：协议的留痕要求（§19.3 `overridden`）问的是
#:   「**我看到的值是哪个包给的**」，
#:   而「用户的值赢了」与「包的值生效」是**两种相反的归因**——
#:   混成一个动作，报告就会说反。
ACTION_WRITE = "write"
"""没人写过 ⇒ 包补上默认值（要写）。"""

ACTION_UPDATE = "update"
"""是包自己写的那个值，但包这次给了新值 ⇒ 覆盖（要写）。"""

ACTION_SAME = "same"
"""当前值就是包自己写的那个值，包这次给的值也一样 ⇒ 无变化（不用写，但算「包的值生效」）。"""

ACTION_KEEP = "keep"
"""当前值**不属于**这个包（用户改过 / 别的来源）⇒ 包不许动（用户的值赢）。"""

ACTION_SKIP = "skip"
"""写不进去 / 不该写（记原因）。"""


def _json_of(value: Any) -> str:
    """把值压成可比较的字符串。

    ★ 为什么要压成字符串而不是直接比 Python 对象：
      数据库里存的是 `value_json`（**字符串**），
      而包清单里的是 Python 对象（`True` / `1` / `"x"`）。
      不统一成同一种表示，就会出现「明明是同一个值，却被判成不同」
      ⇒ 报告里满屏假覆盖，用户会以为包在乱改他的设置。
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class ConfigDecision:
    """一个设置项的落地裁决。"""

    member_id: str
    key: str
    action: str
    """`write` / `update` / `same` / `keep` / `skip`（见上面的动作常量）。"""

    value: Any = None
    """包这次给的值。"""

    current: str | None = None
    """当前生效值的 JSON 文本（留痕用：回答「原来是什么」）。"""

    reason: str | None = None
    """为什么这么判（留痕用）。"""


@dataclass
class ConfigPlan:
    """一次装载的配置落地计划 + 留痕。"""

    pack_id: str = ""
    """留痕里的 `layer` 用它（§19.3 的示例是**包 id**）。"""

    decisions: list[ConfigDecision] = field(default_factory=list)
    disable_hints: list[str] = field(default_factory=list)
    """包里声明 `enabled: false` 的成员（本次不启用，除非用户明确启用过）。"""

    @property
    def writes(self) -> list[ConfigDecision]:
        """真正要写出去的项。"""
        return [d for d in self.decisions if d.action in (ACTION_WRITE, ACTION_UPDATE)]

    @property
    def overridden(self) -> list[dict[str, str]]:
        """覆盖留痕（协议 §19.3 的 `overridden`）。

        形状：`{member, path, layer, current}`。

        ★ `layer` 记的是**哪个包给的**——§19.3 的示例是
          `{"member": "finance", "path": "config.finance.ledger", "layer": "local.my-pack"}`，
          `local.my-pack` 是**包 id**。⇒ 填包 id，不填成员 id。
        ★ `path` 落在**单个 key** 上（`config.finance.ledger`）——
          因为 §19.3 的示例已经定死了留痕的粒度；
          而 §11.1 的「整段替换」说的是**值的替换方式**，不是留痕粒度。
        ★ 收录范围是「**包的值生效**」的项（含无变化的 `same`）——
          §19.3 的硬要求是「用户必须能看出『我看到的值是哪个包给的』」，
          所以「还是包给的那个值」也必须能看出来。
        """
        out: list[dict[str, str]] = []
        for d in self.decisions:
            if d.action not in (ACTION_WRITE, ACTION_UPDATE, ACTION_SAME):
                continue
            out.append(
                {
                    "member": d.member_id,
                    "path": f"config.{d.member_id}.{d.key}",
                    "layer": self.pack_id,
                    "current": d.current or "",
                }
            )
        return out

    @property
    def kept(self) -> list[dict[str, str]]:
        """**用户的值赢了**的项（包没动它）。

        ★ 为什么单列一份而不塞进 `overridden`：
          `overridden` 回答「这个值是哪个包给的」，
          这一份回答「这个值**不是**包给的，是谁留着的」。
          两份合起来才答得全「这个值哪来的」。
        """
        out: list[dict[str, str]] = []
        for d in self.decisions:
            if d.action != ACTION_KEEP:
                continue
            out.append(
                {
                    "member": d.member_id,
                    "path": f"config.{d.member_id}.{d.key}",
                    "layer": "user",
                    "current": d.current or "",
                }
            )
        return out

    @property
    def skipped(self) -> list[dict[str, str]]:
        """没能落地的项（含原因）。"""
        return [
            {
                "member": d.member_id,
                "path": f"config.{d.member_id}.{d.key}",
                "reason": d.reason or "没能落地",
            }
            for d in self.decisions
            if d.action == ACTION_SKIP
        ]


def plan_config(
    manifest: PackManifest,
    *,
    current_settings: dict[str, dict[str, Any]] | None = None,
    applied: dict[str, dict[str, Any]] | None = None,
) -> ConfigPlan:
    """算出「这个包的配置该怎么落地」——**只算不写**。

    `current_settings` —— 当前各成员已生效的设置（`{member_id: {key: value}}`）。
    `applied` —— 包层自己记的「我写过什么」（`{member_id: {key: value}}`）。

    ★ 为什么要「先算后写」：装载有提交点（协议 §19.2 第 7 步），
      提交点之前**不做任何写**。
    ★ 这是**纯函数**——不碰 IO、不碰数据库。
      所以它可以在提交点**之前**先算一次（拿 `enabled: false` 的提示），
      也可以在提交点**之后**再算一次（拿最新的当前值）。
    """
    current = current_settings or {}
    mine = applied or {}
    plan = ConfigPlan(pack_id=manifest.id)

    for member_id, values in manifest.config.items():
        if member_id not in manifest.declared_ids:
            plan.decisions.append(
                ConfigDecision(
                    member_id=member_id,
                    key="*",
                    action=ACTION_SKIP,
                    reason="成员不在包内（config 指向了未声明的成员）",
                )
            )
            continue

        existing = current.get(member_id) or {}
        owned = mine.get(member_id) or {}

        for key, value in values.items():
            if key == ENABLED_KEY:
                # ★ 启用状态不落 plugin_setting（归用户调整层管，§21.4 规则一）。
                #   它只表达「这个包希望这个成员是关的」。
                if isinstance(value, bool) and not value:
                    plan.disable_hints.append(member_id)
                continue

            if key in existing:
                if key in owned and _json_of(owned[key]) == _json_of(existing[key]):
                    # ★ 仍是包自己写的那个值 ⇒ 包可以更新它。
                    if _json_of(owned[key]) == _json_of(value):
                        plan.decisions.append(
                            ConfigDecision(
                                member_id=member_id,
                                key=key,
                                action=ACTION_SAME,
                                value=value,
                                current=_json_of(existing[key]),
                                reason="当前值就是包给的这个值，无变化",
                            )
                        )
                    else:
                        plan.decisions.append(
                            ConfigDecision(
                                member_id=member_id,
                                key=key,
                                action=ACTION_UPDATE,
                                value=value,
                                current=_json_of(existing[key]),
                            )
                        )
                    continue

                # ★ 当前值与包记录的不一致（或包从没写过）⇒ 值归用户/别的来源。
                #   包**不许动**它——只留痕（§16.1 第 3 条）。
                plan.decisions.append(
                    ConfigDecision(
                        member_id=member_id,
                        key=key,
                        action=ACTION_KEEP,
                        value=value,
                        current=_json_of(existing[key]),
                        reason="已有值不属于这个包——保留用户/其它来源的值",
                    )
                )
                continue

            # ★ 没人写过 ⇒ 包补上默认值。
            plan.decisions.append(
                ConfigDecision(
                    member_id=member_id,
                    key=key,
                    action=ACTION_WRITE,
                    value=value,
                )
            )

    return plan


def applied_after(
    plan: ConfigPlan,
    applied: dict[str, dict[str, Any]] | None = None,
    *,
    only: set[tuple[str, str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """写完之后，**包层自己的记录**应该长什么样。

    ★ 只有「包写的」才进这份记录。
      用户的值不进——它不是包能主张的东西。

    `only` —— 限定只记这些 **(成员, 键)** 的写入。
      为什么要它：写入是**按键**发生的（一次调用写一个键），
      「某个键写失败了」也是按**键**知道的——
      粒度一致。⇒ 只记真写进去的那些键，
      写失败的那个键不进记录（否则下次会以为「这个值还是包写的」，
      而实际上它压根没写进去）。
    """
    out: dict[str, dict[str, Any]] = {
        member: dict(values) for member, values in (applied or {}).items()
    }
    for d in plan.decisions:
        if d.action not in (ACTION_WRITE, ACTION_UPDATE):
            continue
        if only is not None and (d.member_id, d.key) not in only:
            continue
        out.setdefault(d.member_id, {})[d.key] = d.value
    return out


__all__ = [
    "ACTION_KEEP",
    "ACTION_SAME",
    "ACTION_SKIP",
    "ACTION_UPDATE",
    "ACTION_WRITE",
    "ENABLED_KEY",
    "ConfigDecision",
    "ConfigPlan",
    "applied_after",
    "plan_config",
]
