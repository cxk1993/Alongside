"""能力路由的层序解析与裁决（报告 §10.5 / §16.1）。

★★ 这是「多 Provider 谁生效」这件事的**唯一**裁定位置。

★ 为什么只有包层能做这件事（四层论证，缺一不可）：
  · 内核 —— 不能。「哪个插件提供账单读取」是业务判断，
    内核一旦知道，就必须随业务变化而改 ⇒ 内核不许有业务假设。
  · 插件 —— 不能。插件互不知情是整个体系的前提；
    让它选，等于要求它知道别人的 `provides`。
  · 用户 —— 不该。用户能选，但不该成为「系统能不能启动」的必要条件。
    每个多 Provider 部署都弹一次选择框，是把装配问题推给使用者。
  · **包 —— 能。包是唯一知道「包里所有插件」的实体**：
    装配前就握着完整成员清单与各自 `provides`。
  ⇒ 这个位置不是「包层顺便能做」，而是「除包层之外无人能做」。

★ 层序（§16.1，与其余包级字段同一套规则）：
    基包的 providerChoice
      ↓  派生包 overrides 可改
    用户覆盖层可改
      ↓  最后写入者胜
  ⇒ 「换一个包 = 换掉整套 Provider 选型 = 换掉产品行为」。

★ 三个边界（写死，防止将来被悄悄放宽）：
  1. **不改任何成员的 `provides` 声明**——它是装配期视图，红线一不受影响；
  2. **不引入「优先级」概念**——不做 `priority: 10`。
     优先级让「谁生效」取决于数值比较，而数值不可审计；
     显式选一个，答案唯一；
  3. **不做运行时切换**——第一期只解决「能不能启动」。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from core.plugins.crosscheck import provider_conflicts
from packs.model import PackManifest

log = logging.getLogger("kernel.packs.routing")


def resolve_provider_choice(
    chain: Sequence[PackManifest],
    *,
    user_choice: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """按层序算出最终的 `providerChoice`。

    返回 `(最终选定, 每项来自哪一层)`。
    两份对应表的键集相同。

    ★ 层序：`chain` 的顺序是「依赖在前、自己在后」（见 `expand_includes`），
      所以**后写入者胜**天然落在链尾 = 自己这一层。
      用户的覆盖层最后写（它优先级最高，§16.1）。
    """
    chosen: dict[str, str] = {}
    layer_of: dict[str, str] = {}

    for pack in chain:
        for cap, pid in pack.provider_choice.items():
            chosen[cap] = pid
            layer_of[cap] = pack.id

    if user_choice:
        for cap, pid in user_choice.items():
            chosen[cap] = pid
            layer_of[cap] = "user"

    return chosen, layer_of


def route_capabilities(
    chain: Sequence[PackManifest],
    plugins: Mapping[str, Mapping[str, Any]],
    *,
    user_choice: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """算出本次装配的能力路由结果。

    返回 `(已选定的路由, 待裁决的冲突)`，每项形如
    `{"capability": ..., "provider": ..., "layer": ...}` /
    `{"capability": ..., "candidates": [...]}`。

    ★ 判据的对象是**本包成员子集**，不是全量插件：
      跨插件校验（B4）在全量上跑过；这里要回答的是
      「**这个包**里，同一个能力有谁提供、选了谁」。
      两者口径不同：全量冲突可能是别的包的成员，包层无权代裁。
    """
    chosen, layer_of = resolve_provider_choice(chain, user_choice=user_choice)

    # 本包（含被包含的包）的全部成员 id
    member_ids: set[str] = set()
    for pack in chain:
        for member in pack.all_members:
            member_ids.add(member.id)

    # 只看本包成员各自 provides 什么
    member_manifests: list[dict[str, Any]] = []
    for member_id in sorted(member_ids):
        plugin = plugins.get(member_id)
        if plugin is None:
            continue
        member_manifests.append({"id": member_id, "provides": plugin.get("provides", []) or []})

    # 已选定的（层序算出的）
    selected: list[dict[str, Any]] = []
    for cap in sorted(chosen):
        selected.append(
            {"capability": cap, "provider": chosen[cap], "layer": layer_of.get(cap, "?")}
        )

    # 待裁决的：把已选定的排除掉，剩下的才是冲突
    conflicts = provider_conflicts(member_manifests, selection=chosen)
    unresolved = [
        {"capability": cap, "candidates": cands} for cap, cands in sorted(conflicts.items())
    ]

    return selected, unresolved
