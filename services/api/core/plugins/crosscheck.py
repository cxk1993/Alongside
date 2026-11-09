# ── ③ 跨插件层 ──
"""跨插件一致性校验（协议报告 §19 第③层）。

★ 与 core/plugins/validate.py 的分工：
  validate.py  →  ①②：单插件的形状与内部一致性（不需要知道别的插件）
  crosscheck.py →  ③：跨插件一致性（必须等全部插件发现完才能判）

★ 为什么必须单独一层、且必须等全量：
  「这个能力有没有人提供」「这个能力是不是被两个人提供了」这类问题，
  答案可能出现在扫描顺序的后面。边发现边校验会漏判。

★ 与 core/manifest.py 的旧闸门的关系：
  旧闸门（_gate_dependency_declarations）已实现①有人 provides、③无循环硬依赖、
  ④软硬互斥。本模块补上它缺的、并按协议收紧的：
    - 规则 12：provides 的每个能力必须有人 defines（三角色的严格模式）
    - 规则 13：同一能力被多个插件 provides（**不阻断启动**，见下）
  旧的①③④继续沿用，本模块不重复实现。

★★ 规则 13 的口径（2026-10-28 修订，取代旧口径）：
  旧口径是「同一能力被两个插件 provides ⇒ 硬报错、启动失败」。
  修订后**不再阻断启动**，改为「登记为待裁决冲突」。

  为什么改（旧口径会死锁）：
    真实启动顺序是「发现插件 → 跑本模块 → 激活插件 → 包层装配」。
    而「谁提供这个能力、选谁」这个决定，只有**包层**能做
    （包是唯一知道「包里所有插件」的实体）。
    ⇒ 旧口径等于让内核在包层说话之前就把系统判死，
      报错还指向一个「尚未被读到」的字段。

  三条判据必须分清（这是本模块最重要的一段）：
  | 情形 | 处置 | 理由 |
  |---|---|---|
  | 同一能力被多个插件 provides | **登记待裁决，不阻断** | 内核对「多 Provider」没有立场；有包就由包裁决 |
  | 某能力没有任何 provider，但有人 requires 它 | **阻断** | 这是真缺能力，没有第二种解释 |
  | 一个能力被两个插件 defines | **阻断** | 能力缝二义，无人能裁决 |

  ⇒ 本模块**只返回违规列表**（阻断性的那些）；
    「待裁决冲突」走 `provider_conflicts()` 单独出口，不混进违规列表。
"""
from __future__ import annotations

import logging
from typing import Any

from core.errors import ManifestError

log = logging.getLogger("kernel.manifest")


def _defines_of(manifest: dict[str, Any]) -> list[str]:
    return [str(c) for c in (manifest.get("defines") or [])]


def _provides_of(manifest: dict[str, Any]) -> list[str]:
    return [str(c) for c in (manifest.get("provides") or [])]


def crosscheck_manifests(
    manifests: list[dict[str, Any]],
    *,
    selection: dict[str, str] | None = None,
) -> list[str]:
    """跨插件一致性校验。返回违规描述列表（空列表 = 全部通过）。

    ★ 规则 12 / 13 的判定依赖「全局是否有插件写了 defines」：
      - 全系统**没有任何**插件写 defines → 兼容模式（provides 隐含 defines）
      - 一旦有任一插件写了 defines → 全系统切严格模式
    ★ 为什么是「全系统一次性切换」而不是「按插件决定」：
      如果允许 A 兼容模式、B 严格模式，那「B 的 provides 需不需要有人 defines」
      就有两个答案，校验结果取决于检查顺序 —— 不可接受。

    ★ `selection` 是**能力路由的显式选定**（能力名 → 选中的提供者插件 id）。
      它由**包层**提供（`pack.json` 的 `providerChoice`），
      因为包是唯一知道「包里所有插件」的实体。
      作用：让「多 Provider」这件事在**装配期**有答案，
      从而使规则 13 不必在启动期就判死。
      ★ 它**不改变**规则 13 的判定逻辑，只多一个「已被显式选定」的出口。
    """
    violations: list[str] = []

    # ── 收集 defines / provides ──
    definer_of: dict[str, str] = {}  # 能力 → 定义者插件 id
    providers_of: dict[str, list[str]] = {}  # 能力 → 提供者插件 id 列表
    strict_mode = False

    for mf in manifests:
        pid = str(mf.get("id") or "?")
        for cap in _defines_of(mf):
            strict_mode = True
            if cap in definer_of:
                violations.append(
                    f"能力 {cap!r} 被两个插件 defines（{definer_of[cap]} 与 {pid}）："
                    "一个能力最多一个 defines"
                )
            else:
                definer_of[cap] = pid
        for cap in _provides_of(mf):
            providers_of.setdefault(cap, []).append(pid)

    if not strict_mode:
        # 兼容模式：provides 隐含 defines
        for cap, pids in providers_of.items():
            if cap not in definer_of:
                definer_of[cap] = pids[0]
        return violations

    # ── 规则 12：provides 必须有人 defines（严格模式）──
    for cap, pids in sorted(providers_of.items()):
        if cap not in definer_of:
            violations.append(
                f"插件 {pids[0]} provides 了能力 {cap!r}，但没有任何插件 defines 它"
                "（严格模式：能力必须有明确的所有者）"
            )

    # ── 规则 13：同一能力被多个插件 provides（登记待裁决，不阻断启动）──
    #   ★ 与规则 12 的区别：12 是「能力没有主人」（阻断），
    #     13 是「能力有两个可能的主人」（登记，由包层裁决）。
    #   ★ 为什么不阻断：包层的 `providerChoice` 裁决发生在插件发现**之后**，
    #     在这里判死等于让内核在包层说话之前就把系统判死。
    #   ⇒ 判定实现在 `provider_conflicts()`。
    return violations


def provider_conflicts(
    manifests: list[dict[str, Any]],
    *,
    selection: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """多 Provider 冲突 —— **登记，不阻断**（规则 13 的新口径）。

    返回：能力名 → 该能力的全部提供者插件 id（已按 id 升序）。

    ★ 被 `selection` 显式选定的能力**不在返回里**：
      那个决定已经做出（由包层做），不算冲突。
    ★ 只有一个提供者的能力**不在返回里**：不是冲突。

    ★ 为什么单独一个出口而不是塞进 `crosscheck_manifests` 的违规列表：
      违规列表 = 「启动失败的理由」。而多 Provider 在没有包裁决时
      **不是启动失败的理由**（能力标 unavailable 而已）。
      两者混在一起 ⇒ 调用方无法区分「必须停」与「必须记」。
    """
    # 已被显式选定的能力：只保留选中的那一个作为「有效提供者」，
    # 其余不再构成冲突。
    chosen = dict(selection) if selection else {}

    providers_of: dict[str, list[str]] = {}
    for mf in manifests:
        pid = str(mf.get("id") or "?")
        for cap in _provides_of(mf):
            providers_of.setdefault(cap, []).append(pid)

    conflicts: dict[str, list[str]] = {}
    for cap, pids in sorted(providers_of.items()):
        if cap in chosen:
            # 已被选定：其余提供者本期不激活（能力级处置，不改插件状态）
            continue
        if len(pids) > 1:
            conflicts[cap] = sorted(pids)
    return conflicts


def assert_crosscheck(
    manifests: list[dict[str, Any]],
    *,
    selection: dict[str, str] | None = None,
) -> None:
    """跨插件一致性校验；有任何**阻断性**违规则抛 ManifestError（★ 列出全部违规）。

    ★ 多 Provider 不在阻断之列（见 `provider_conflicts`）。
    """
    violations = crosscheck_manifests(manifests, selection=selection)
    if violations:
        raise ManifestError(
            "跨插件一致性校验失败：\n  - " + "\n  - ".join(violations)
        )
