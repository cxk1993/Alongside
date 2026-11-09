# ── 协议与调研产物的对齐判据 ──
#!/usr/bin/env python3
"""协议 ↔ 调研产物 对齐性核查器。

★ 为什么需要这个：
  「协议已经覆盖了调研产物」这句话，人说会漏、会说错。
  本脚本把调研产物里的**机制名词**逐条映射到协议代码
  （Schema 字段 / 校验规则 / 运行时消费点），机器判定覆盖情况。

  覆盖判定分三档：
    ✓ 已体现   —— 协议代码里有明确对应（字段或规则）
    △ 部分     —— 有相关字段，但缺独立表达（弱体现）
    ✗ 缺失     —— 协议代码里完全没有对应 ★ 这是要补的

用法：
    python3 tools/check_research_alignment.py
    python3 tools/check_research_alignment.py --json
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# ★ 路径来自 tools/paths.py（唯一真相源）
_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from paths import REPO  # noqa: E402

SCHEMA_PATH = REPO / "contracts/plugin.schema.json"
VALIDATE_PATH = REPO / "services/api/core/plugins/validate.py"
CROSSCHECK_PATH = REPO / "services/api/core/plugins/crosscheck.py"


# ★ 调研产物 → 协议字段/规则的映射表
#   每条：机制（调研里的说法）→ 协议侧应当落到的字段/规则关键词
#   fields：Schema 里应当存在的字段名（任一命中即可）
#   rules：校验器/跨插件校验里应当出现的关键词（任一命中即可）
ALIGNMENT: list[dict[str, Any]] = [
    {
        "mechanism": "能力缝三角色：Service Definition",
        "source": "内核重构 / 插件协议调研",
        "fields": ["defines"],
        "rules": ["defines"],
        "why": "定义者与提供者分离——defines 声明「我定义这个能力」",
    },
    {
        "mechanism": "能力缝三角色：Service Provider",
        "source": "内核重构 / 插件协议调研",
        "fields": ["provides"],
        "rules": ["provides"],
        "why": "提供者——实现能力；规则 13 保证同一能力不被两个插件提供",
    },
    {
        "mechanism": "能力缝三角色：Consumer",
        "source": "内核重构 / 插件协议调研",
        "fields": ["requires", "optionalDependencies"],
        "rules": ["requires"],
        "why": "消费者——声明依赖；规则 2 保证依赖集不相交",
    },
    {
        "mechanism": "capability（能力）作为一等公民",
        "source": "论文调研（能力请求）",
        "fields": ["defines", "provides", "requires"],
        "rules": ["capability", "能力"],
        "why": "能力寻址而非进程寻址",
    },
    {
        "mechanism": "Phase Machine（插件生命周期状态机）",
        "source": "内核重构/PhaseMachine_插件生命周期状态机.md",
        "fields": ["lifecycle", "deactivation"],
        "rules": ["lifecycle"],
        "why": "生命周期钩子与停用策略是状态机的对外表达",
    },
    {
        "mechanism": "kernel.effect()（注册即带逆）",
        "source": "内核重构/kernel.effect_注册即带逆.md",
        "fields": ["sideEffects", "track", "execution"],
        "rules": ["sideEffects", "checkpoint"],
        "why": "副作用与检查点字段是 effect 原语的声明面",
    },
    {
        "mechanism": "Reactive Co-effect（依赖满足驱动）",
        "source": "内核重构/ReactiveCoeffect_依赖满足驱动.md",
        "fields": ["activates_on", "requires", "optionalDependencies"],
        "rules": ["activates_on"],
        "why": "激活条件与依赖共同构成 co-effect 的触发面",
    },
    {
        "mechanism": "拦截层（逐次拦截）",
        "source": "内核重构/内核边界与拦截层.md",
        "fields": ["permissions", "riskLevel", "execution"],
        "rules": ["permissions", "approval", "riskLevel"],
        "why": "权限/风险/执行模式是拦截层的判定依据",
    },
    {
        "mechanism": "Recover（补偿恢复）",
        "source": "内核重构/kernel.effect_注册即带逆.md",
        "fields": ["migrations", "sideEffects"],
        "rules": ["migrations", "concurrency"],
        "why": "迁移可逆性 + 独占执行是恢复语义的前提（规则 7/8）",
    },
    {
        "mechanism": "effect log（效果日志）",
        "source": "内核重构/kernel.effect_注册即带逆.md",
        "fields": ["track", "sideEffects"],
        "rules": ["track"],
        "why": "track 决定效果是否落日志、可否追溯",
    },
    {
        "mechanism": "身份域 L0~L3",
        "source": "内核重构/多AI并发改造的预设与防护.md",
        "fields": ["origin", "permissions", "concurrency"],
        "rules": ["origin", "installedBy", "signature"],
        "why": "插件来源、签名、权限即身份域的第一层表达",
    },
    {
        "mechanism": "Independence Condition（独立性条件）",
        "source": "论文调研（§4 组合语义）",
        "fields": ["slots", "provides"],
        "rules": ["slots"],
        "why": "扩展点插槽 + 能力独占是独立性约束的协议面",
    },
    {
        "mechanism": "扩展点 / slot（插件挂载位）",
        "source": "内核重构 / 插件协议调研",
        "fields": ["slots"],
        "rules": ["slots"],
        "why": "slot 白名单由 Schema enum 约束",
    },
    {
        "mechanism": "定时调度（schedule 六种选择器）",
        "source": "插件协议调研",
        "fields": ["schedule"],
        "rules": ["schedule"],
        "why": "调度是独立字段组",
    },
    {
        "mechanism": "输出策略（outputPolicy 溢出处理）",
        "source": "插件协议调研",
        "fields": ["outputPolicy"],
        "rules": [],
        "why": "输出裁剪与溢出策略",
    },
    {
        "mechanism": "并发控制（乐观锁 / 独占）",
        "source": "内核重构/多AI并发改造的预设与防护.md",
        "fields": ["concurrency"],
        "rules": ["exclusive", "optimisticLock", "parallelSafe"],
        "why": "规则 10/11 落在此处",
    },
    {
        "mechanism": "迁移（migrations）",
        "source": "插件协议调研",
        "fields": ["migrations"],
        "rules": ["migrations", "exclusive", "persistent"],
        "why": "规则 7/8 落在此处",
    },
    {
        "mechanism": "权限（permissions 声明式）",
        "source": "插件协议调研",
        "fields": ["permissions"],
        "rules": ["permissions", "approval"],
        "why": "声明式权限表，规则 3 关联审批",
    },
    {
        "mechanism": "设置（settingsSchema）",
        "source": "插件协议调研",
        "fields": ["settingsSchema", "settings"],
        "rules": [],
        "why": "插件自带设置的声明面",
    },
    {
        "mechanism": "API 契约（api.base / tools）",
        "source": "插件协议调研",
        "fields": ["api"],
        "rules": ["api"],
        "why": "对外 HTTP 契约面",
    },
    {
        "mechanism": "事件契约（emits / consumes）",
        "source": "插件协议调研",
        "fields": ["emits", "consumes"],
        "rules": [],
        "why": "事件发布与订阅的声明面",
    },
    {
        "mechanism": "版本兼容（minKernel / kernelApi）",
        "source": "插件协议调研",
        "fields": ["minKernel", "kernelApi"],
        "rules": ["kernelApi", "minKernel"],
        "why": "内核接口版本兼容判断",
    },
    {
        "mechanism": "插件来源与签名（origin）",
        "source": "插件协议调研 / 身份域",
        "fields": ["origin"],
        "rules": ["signed", "signature", "installedBy"],
        "why": "规则 5/6 落在此处",
    },
    {
        "mechanism": "风险分级（riskLevel）",
        "source": "插件协议调研",
        "fields": ["riskLevel"],
        "rules": ["riskLevel", "L0"],
        "why": "规则 4：第三方插件不得为 L0",
    },
]


def consumption_map() -> dict[str, list[str]]:
    """字段 → 真实消费点（跑 G13 消费门禁拿机器判定结果）。

    ★ G13 的 --json 输出结构是：
        {"consumers": {"<字段名>": ["<文件路径>", ...]}, ...}
      之前按 {"fields": [{...}]} 读，结果全部字段都判成「无消费方」——
      24/24 全报未消费，明显与 G13 自身的 28/35 矛盾。
      读错结构比没检查更危险：它会给出「全部待补」的错误结论。
    """
    script = REPO / "services/api/scripts/check_protocol_consumption.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--json"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    consumers = data.get("consumers", {})
    if not isinstance(consumers, dict):
        return {}
    return {k: v for k, v in consumers.items() if isinstance(v, list)}


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_fields(schema: dict[str, Any]) -> set[str]:
    return set(schema.get("properties", {}).keys())


def code_corpus() -> str:
    """协议代码语料：校验器 + 跨插件校验 + Schema 文本。"""
    parts = [
        VALIDATE_PATH.read_text(encoding="utf-8"),
        CROSSCHECK_PATH.read_text(encoding="utf-8"),
        SCHEMA_PATH.read_text(encoding="utf-8"),
    ]
    return "\n".join(parts)


def check() -> list[dict[str, Any]]:
    schema = load_schema()
    fields = schema_fields(schema)
    corpus = code_corpus()
    consumers = consumption_map()
    results: list[dict[str, Any]] = []

    for item in ALIGNMENT:
        want_fields = item["fields"]
        want_rules = item["rules"]

        hit_fields = [f for f in want_fields if f in fields]
        hit_rules = [r for r in want_rules if r in corpus]

        # 消费方：这些字段是否被运行时真读
        consumed_fields = [f for f in hit_fields if consumers.get(f)]
        unconsumed_fields = [f for f in hit_fields if not consumers.get(f)]

        # ★ 三档判定（加严版）：
        #   ✓ 协议完备 = 字段在 + 规则/实现在
        #     协议这一侧已经完整对齐调研产物。
        #   △ 协议侧缺一环（字段缺，或规则缺）
        #   ✗ 完全没有对应
        #
        # ★ 注意「有字段但没人消费」**不算**协议侧缺失——
        #   那是内核实现进度问题（协议先立、实现跟上），
        #   两者必须分开报告，不能混为一谈。
        if want_fields and not want_rules:
            status = "✓" if hit_fields else "✗"
        elif want_fields and want_rules:
            if hit_fields and hit_rules:
                status = "✓"
            elif hit_fields or hit_rules:
                status = "△"
            else:
                status = "✗"
        else:
            status = "✓" if hit_rules else "✗"

        results.append(
            {
                "mechanism": item["mechanism"],
                "source": item["source"],
                "status": status,
                "fields_hit": hit_fields,
                "fields_miss": [f for f in want_fields if f not in fields],
                "rules_hit": hit_rules,
                "rules_miss": [r for r in want_rules if r not in corpus],
                "consumed_fields": consumed_fields,
                "unconsumed_fields": unconsumed_fields,
                "why": item["why"],
            }
        )
    return results


def main() -> int:
    if "--json" in sys.argv:
        print(json.dumps(check(), ensure_ascii=False, indent=2))
        return 0

    results = check()
    full = [r for r in results if r["status"] == "✓"]
    part = [r for r in results if r["status"] == "△"]
    miss = [r for r in results if r["status"] == "✗"]

    # 有字段但运行时没人读的机制——这是**内核实现进度**，不是协议缺失
    pending_impl = [r for r in results if r["unconsumed_fields"]]

    print("=" * 72)
    print("协议 ↔ 调研产物 对齐性核查")
    print("=" * 72)
    print(f"调研机制总数：{len(results)}")
    print(f"✓ 协议侧已完备：{len(full)}    △ 协议侧缺一环：{len(part)}    ✗ 完全缺失：{len(miss)}")
    print()

    for r in results:
        mark = r["status"]
        print(f"{mark} {r['mechanism']}")
        if mark != "✓":
            if r["fields_miss"]:
                print(f"      缺字段：{r['fields_miss']}")
            if r["rules_miss"]:
                print(f"      缺规则/代码：{r['rules_miss']}")
    print()

    if part:
        print("△ 协议侧缺一环（要补）：")
        for r in part:
            print(f"   {r['mechanism']}")
        print()
    if miss:
        print("✗ 协议里完全没有对应 ★ 要补：")
        for r in miss:
            print(f"   {r['mechanism']}  ← {r['why']}")
        print()

    if pending_impl:
        print("─" * 72)
        print("内核实现待补（协议已立，运行时还没人读这些字段）：")
        for r in pending_impl:
            print(f"   {r['mechanism']}：{r['unconsumed_fields']}")
        print("   ↑ 这是实现进度，不是协议缺失——协议先立、内核跟上。")
        print()

    if miss or part:
        return 1
    print(f"✓ 全部 {len(results)} 项调研机制在协议侧都有完整对应（字段 + 规则）")
    if pending_impl:
        print(f"  另有 {len(pending_impl)} 项的字段尚无运行时消费方，属内核实现待补")
    return 0


if __name__ == "__main__":
    sys.exit(main())
