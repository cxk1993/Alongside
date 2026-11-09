#!/usr/bin/env python3
"""《Alongside 插件协议 · 契约》生成器 —— 从 `contracts/plugin.schema.json` 生成字段规格。

★ 为什么要有这个脚本
--------------------
「协议有 36 个字段」这句话，人写下来就开始过期：
字段加了没人改文档、字段删了文档还留着、必填集变了没人知道。
⇒ 字段表、必填集、分组，全部从 schema **实数**，一个数都不写死。

★ 确定性
--------
- 不写生成时间；
- 字段顺序 = schema 里 `properties` 的书写顺序（JSON 对象有序，Python 3.7+ 保序）；
- 分组顺序写死在 `GROUPS` 里；schema 里出现分组表没覆盖的字段 → 落到「未分组」，
  **不丢**（丢字段比分组难看严重得多）；
- 换行归一为 `\\n`，末尾恰好一个换行。

★ 分组表是「说明」，不是「判据」
--------------------------------
判据永远是 schema 本身：某个字段属于哪一组，只影响阅读顺序，
不影响任何校验。所以分组表漏了字段时，脚本会把它放进「未分组」并照常输出，
而不是报错——**文档要如实反映 schema，不是反过来要求 schema 迁就文档**。

用法：
    python3 tools/gen_protocol_doc.py                     # 打到 stdout
    python3 tools/gen_protocol_doc.py --out docs/x.md     # 写文件
    python3 tools/gen_protocol_doc.py --check docs/x.md   # 只核对，不一致 exit 1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ★ 路径来自 tools/paths.py（唯一真相源）
_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from paths import CONTRACTS, rel  # noqa: E402

SCHEMA_PATH = CONTRACTS / "plugin.schema.json"

#: 字段分组（**阅读顺序**，不是判据）。顺序写死 ⇒ 输出确定。
#: 未列出的字段会进「未分组」一节，不会被丢掉。
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "身份与版本",
        ("id", "name", "version", "kind", "minKernel", "kernelApi"),
    ),
    (
        "展示、入口与 API",
        ("icon", "description", "author", "window", "entry", "api", "tabs", "absorbs"),
    ),
    (
        "能力缝（定义 / 提供 / 依赖）",
        ("defines", "provides", "requires", "optionalDependencies"),
    ),
    (
        "扩展点与事件",
        ("slots", "emits", "consumes", "activates_on"),
    ),
    (
        "权限、风险与执行",
        ("permissions", "riskLevel", "execution"),
    ),
    (
        "副作用、状态与迁移",
        ("sideEffects", "track", "migrations"),
    ),
    (
        "来源与并发",
        ("origin", "concurrency"),
    ),
    (
        "调度与输出",
        ("schedule", "outputPolicy"),
    ),
    (
        "设置与生命周期",
        ("settingsSchema", "lifecycle", "deactivation"),
    ),
    (
        "扩展位",
        ("extensions",),
    ),
)

UNGROUPED = "未分组"


# ───────────────────────── 取值小工具 ─────────────────────────


def cell(text: Any) -> str:
    """表格单元格转义：`|` 会截断表格，换行会截断行。"""
    s = "" if text is None else str(text)
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    s = s.replace("|", "\\|")
    return " ".join(s.split())


def type_of(spec: dict[str, Any]) -> str:
    """渲染字段类型：type 可能是字符串、字符串数组，或压根没有（用 oneOf/anyOf）。"""
    t = spec.get("type")
    if isinstance(t, str):
        return t
    if isinstance(t, list):
        return "|".join(str(x) for x in t)
    for key in ("oneOf", "anyOf", "allOf"):
        if isinstance(spec.get(key), list):
            return f"{key}×{len(spec[key])}"
    if "const" in spec:
        return f"const {spec['const']}"
    return "—"


def constraints(spec: dict[str, Any]) -> str:
    """把约束压成一行：枚举 / 正则 / 长度 / 数值 / 数组元素 / 子字段必填。"""
    parts: list[str] = []

    enum = spec.get("enum")
    if isinstance(enum, list):
        shown = [str(x) for x in enum[:5]]
        more = f" …(+{len(enum) - 5})" if len(enum) > 5 else ""
        parts.append("enum: " + " / ".join(shown) + more)

    pattern = spec.get("pattern")
    if isinstance(pattern, str):
        parts.append(f"pattern: {pattern}")

    for key, label in (
        ("minLength", "minLength"),
        ("maxLength", "maxLength"),
        ("minimum", "min"),
        ("maximum", "max"),
    ):
        if key in spec:
            parts.append(f"{label}={spec[key]}")

    items = spec.get("items")
    if isinstance(items, dict):
        it = type_of(items)
        if isinstance(items.get("enum"), list):
            it += "[" + " / ".join(str(x) for x in items["enum"][:4]) + "]"
        parts.append(f"array<{it}>")

    sub_required = spec.get("required")
    if isinstance(sub_required, list) and sub_required:
        parts.append("需: " + ", ".join(str(x) for x in sub_required))

    # 联合类型（permissions 这类「旧数组 / 新对象」双格式）：把分支摊开，
    # 否则类型列只剩一个 `oneOf×2`，读的人还得回去翻 schema。
    for key in ("oneOf", "anyOf"):
        branches = spec.get(key)
        if isinstance(branches, list) and branches:
            descs: list[str] = []
            for branch in branches:
                if not isinstance(branch, dict):
                    descs.append(json.dumps(branch, ensure_ascii=False, sort_keys=True))
                    continue
                d = type_of(branch)
                sub = branch.get("properties")
                if isinstance(sub, dict) and sub:
                    d += f"({len(sub)} 字段)"
                req = branch.get("required")
                if isinstance(req, list) and req:
                    d += "需 " + ",".join(str(x) for x in req)
                descs.append(d)
            parts.append(f"{key}: " + " ｜ ".join(descs))

    if spec.get("additionalProperties") is False:
        parts.append("不许额外字段")

    return "；".join(parts) if parts else "—"


def summary_of(spec: dict[str, Any], limit: int = 80) -> str:
    """说明列：取 description 的第一句，超长截断（截断点加 …）。"""
    desc = spec.get("description")
    if not isinstance(desc, str) or not desc.strip():
        return "—"
    first = desc.strip().split("\n")[0].strip()
    for sep in ("。", "；"):
        idx = first.find(sep)
        if 0 < idx < limit:
            first = first[: idx + 1]
            break
    if len(first) > limit:
        first = first[:limit].rstrip() + "…"
    return first


# ───────────────────────── 条件必填 ─────────────────────────


def describe_condition(node: Any) -> str:
    """把 `if` 子句翻成人话；认不出来就退回紧凑 JSON（不猜）。"""
    if not isinstance(node, dict):
        return json.dumps(node, ensure_ascii=False, sort_keys=True)
    props = node.get("properties")
    if not isinstance(props, dict):
        return json.dumps(node, ensure_ascii=False, sort_keys=True)
    bits: list[str] = []
    for name, cond in props.items():
        if not isinstance(cond, dict):
            bits.append(f"{name} = {json.dumps(cond, ensure_ascii=False)}")
            continue
        if "const" in cond:
            bits.append(f"{name} = {cond['const']}")
        elif isinstance(cond.get("not"), dict) and "const" in cond["not"]:
            bits.append(f"{name} ≠ {cond['not']['const']}")
        elif isinstance(cond.get("enum"), list):
            bits.append(f"{name} ∈ {{{', '.join(str(x) for x in cond['enum'])}}}")
        else:
            bits.append(f"{name} 满足 {json.dumps(cond, ensure_ascii=False, sort_keys=True)}")
    return " 且 ".join(bits)


def conditional_required(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """抽出 `allOf` 里的条件必填（if → then.required）。"""
    out: list[dict[str, Any]] = []
    for entry in schema.get("allOf") or []:
        if not isinstance(entry, dict):
            continue
        then = entry.get("then")
        if not isinstance(then, dict) or not isinstance(then.get("required"), list):
            continue
        out.append(
            {
                "when": describe_condition(entry.get("if")),
                "required": [str(x) for x in then["required"]],
                "description": entry.get("description", ""),
            }
        )
    return out


# ───────────────────────── 摘要（机器可读） ─────────────────────────


def field_summary(name: str, spec: dict[str, Any], required: bool) -> dict[str, Any]:
    """单个字段的机器可读摘要——只放判据，不放散文。"""
    item: dict[str, Any] = {
        "name": name,
        "type": type_of(spec),
        "required": required,
    }
    for key in ("enum", "pattern", "minLength", "maxLength", "minimum", "maximum"):
        if key in spec:
            item[key] = spec[key]
    items = spec.get("items")
    if isinstance(items, dict):
        item["items_type"] = type_of(items)
        if isinstance(items.get("enum"), list):
            item["items_enum"] = items["enum"]
    sub_required = spec.get("required")
    if isinstance(sub_required, list) and sub_required:
        item["required_sub"] = sub_required
    sub_props = spec.get("properties")
    if isinstance(sub_props, dict) and sub_props:
        item["properties"] = list(sub_props.keys())
    for key in ("oneOf", "anyOf"):
        if isinstance(spec.get(key), list):
            item[f"{key}_count"] = len(spec[key])
    return item


def build_summary(schema: dict[str, Any], schema_lines: int) -> dict[str, Any]:
    props: dict[str, Any] = schema.get("properties") or {}
    required = [str(x) for x in (schema.get("required") or [])]
    required_set = set(required)

    grouped: list[dict[str, Any]] = []
    covered: set[str] = set()
    for group_name, names in GROUPS:
        present = [n for n in names if n in props]
        covered.update(present)
        if present:
            grouped.append({"name": group_name, "fields": present})
    leftover = [n for n in props if n not in covered]
    if leftover:
        grouped.append({"name": UNGROUPED, "fields": leftover})

    return {
        "source": rel(SCHEMA_PATH),
        "schema_lines": schema_lines,
        "title": schema.get("title", ""),
        "field_count": len(props),
        "required_count": len(required),
        "required": required,
        "conditional_required": conditional_required(schema),
        "additionalProperties": schema.get("additionalProperties"),
        "patternProperties": sorted((schema.get("patternProperties") or {}).keys()),
        "groups": grouped,
        "fields": [
            field_summary(name, spec, name in required_set)
            for name, spec in props.items()
            if isinstance(spec, dict)
        ],
    }


# ───────────────────────── 渲染 ─────────────────────────


def render() -> str:
    raw = SCHEMA_PATH.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    schema: dict[str, Any] = json.loads(raw)
    schema_lines = len(raw.splitlines())

    props: dict[str, Any] = schema.get("properties") or {}
    required = [str(x) for x in (schema.get("required") or [])]
    required_set = set(required)
    summary = build_summary(schema, schema_lines)

    out: list[str] = []
    add = out.append

    add("<!-- 本文件由 tools/gen_protocol_doc.py 从真实 schema 生成，请勿手改。 -->")
    add("<!-- 改协议请改 contracts/plugin.schema.json，然后重跑生成脚本。 -->")
    add("")
    add("# Alongside 插件协议 · 契约")
    add("")
    add("> **本文档是契约本身。**")
    add(">")
    add(f"> 真相源：`{rel(SCHEMA_PATH)}`（{schema_lines} 行，{len(props)} 个顶层字段）")
    add(">")
    add("> 协议的**语义与规则**在《Alongside 插件协议技术报告 v1.1》里；")
    add("> 执行体（`validate.py` / `crosscheck.py`）在《Alongside 插件协议代码》里；")
    add("> 本文档只回答一件事：**契约现在到底规定了哪些字段、哪些必填**。")
    add(">")
    add("> 文档与 schema 冲突时，**以 schema 为准**，并重跑本脚本。")
    add(">")
    add("> ★ 本文档**不写生成时间**：时间戳会让同一份 schema 生成出不同字节，")
    add("> `--check` 就永远对不上。字段数、必填数全部**实数**，不写死。")
    add("")
    add("---")
    add("")
    add("## 1 · 契约元信息")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    add(f"| 标题 | {cell(schema.get('title', '—'))} |")
    add(f"| `$schema` | `{cell(schema.get('$schema', '—'))}` |")
    add(f"| `$id` | `{cell(schema.get('$id', '—'))}` |")
    add(f"| 顶层类型 | `{cell(schema.get('type', '—'))}` |")
    add(f"| 顶层字段数 | **{len(props)}** |")
    add(f"| 必填字段数 | **{len(required)}** |")
    add(f"| 额外字段 | {'禁止（`additionalProperties: false`）' if schema.get('additionalProperties') is False else '允许'} |")
    pp = sorted((schema.get("patternProperties") or {}).keys())
    if pp:
        add(f"| 放行模式 | {'、'.join(f'`{cell(p)}`' for p in pp)} |")
    add("")
    desc = schema.get("description")
    if isinstance(desc, str) and desc.strip():
        add(f"> {cell(desc)}")
        add("")

    # ── 2 · 必填集 ──
    add("---")
    add("")
    add("## 2 · 必填集")
    add("")
    add(f"`required` 共 **{len(required)}** 项（读自 schema，不是手抄）：")
    add("")
    add("```")
    for name in required:
        add(name)
    add("```")
    add("")
    cond = summary["conditional_required"]
    if cond:
        add("**条件必填**（`allOf` 里的 `if → then.required`）：")
        add("")
        add("| 条件 | 追加必填 | 说明 |")
        add("|---|---|---|")
        for item in cond:
            req = "、".join(f"`{n}`" for n in item["required"])
            add(f"| {cell(item['when'])} | {req} | {cell(item['description'] or '—')} |")
        add("")
        add("> ★ 条件必填**不进** `required` 数组：它只在条件成立时生效，")
        add("> 所以上面第 2 节的必填集与这里的追加项要分开看。")
        add("")

    # ── 3 · 字段分组表 ──
    add("---")
    add("")
    add("## 3 · 字段分组表")
    add("")
    add(f"全部 **{len(props)}** 个顶层字段，按阅读顺序分组。")
    add("**分组只是阅读顺序，不是判据**——判据永远是 schema 本身。")
    add("")
    add("| 组 | 字段数 | 字段 |")
    add("|---|---:|---|")
    for group in summary["groups"]:
        names = "、".join(f"`{n}`" for n in group["fields"])
        add(f"| **{cell(group['name'])}** | {len(group['fields'])} | {names} |")
    add(f"| **合计** | **{len(props)}** | |")
    add("")

    for idx, group in enumerate(summary["groups"], start=1):
        add(f"### 3.{idx} {group['name']}（{len(group['fields'])} 个字段）")
        add("")
        add("| 字段 | 类型 | 必填 | 约束 | 说明 |")
        add("|---|:--:|:--:|---|---|")
        for name in group["fields"]:
            spec = props[name]
            mark = "✓" if name in required_set else ""
            add(
                f"| `{name}` | `{cell(type_of(spec))}` | {mark} | "
                f"{cell(constraints(spec))} | {cell(summary_of(spec))} |"
            )
        add("")

    # ── 4 · 机器可读摘要 ──
    add("---")
    add("")
    add("## 4 · 机器可读摘要")
    add("")
    add("给门禁/脚本读的那一份。字段顺序 = schema 书写顺序；")
    add("`field_count` / `required_count` 由 schema 实数得出。")
    add("")
    add("````json")
    add(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=False))
    add("````")
    add("")

    # ── 5 · 契约原文 ──
    add("---")
    add("")
    add("## 5 · 契约原文")
    add("")
    add(f"`{rel(SCHEMA_PATH)}` 逐字嵌入（{schema_lines} 行）。")
    add("与上文任何表格冲突时，**以这段原文为准**。")
    add("")
    add("````json")
    add(raw.rstrip("\n"))
    add("````")
    add("")
    add("---")
    add("")
    add("## 6 · 生成信息")
    add("")
    add("```bash")
    add("# 重新生成（改 schema 后必须重跑）")
    add("python3 tools/gen_protocol_doc.py --out <目标路径>.md")
    add("")
    add("# 只核对，不写盘（应当输出「与 schema 一致」）")
    add("python3 tools/gen_protocol_doc.py --check <目标路径>.md")
    add("```")
    add("")
    add("- 生成器：`tools/gen_protocol_doc.py`")
    add(f"- 真相源：`{rel(SCHEMA_PATH)}`")
    add(f"- 顶层字段数：{len(props)}　必填字段数：{len(required)}　条件必填：{len(cond)}")
    add("- 默认输出：stdout（**不会**自动写进任何笔记目录，必须显式 `--out`）")

    return "\n".join(out).rstrip("\n") + "\n"


def first_diff_line(expected: str, actual: str) -> tuple[int, str, str]:
    exp = expected.split("\n")
    act = actual.split("\n")
    for i in range(max(len(exp), len(act))):
        e = exp[i] if i < len(exp) else "<文件已结束>"
        a = act[i] if i < len(act) else "<文件已结束>"
        if e != a:
            return i + 1, e, a
    return 0, "", ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="从 contracts/plugin.schema.json 生成插件协议契约文档",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--out", metavar="PATH", help="写入指定路径（默认打到 stdout）")
    mode.add_argument("--check", metavar="PATH", help="与现有文件比对，不一致 exit 1")
    args = ap.parse_args(argv)

    text = render()

    if args.check:
        target = Path(args.check)
        if not target.exists():
            print(f"✗ 待核对文件不存在：{target}", file=sys.stderr)
            return 1
        actual = target.read_bytes().decode("utf-8", errors="replace")
        if actual == text:
            print(f"✓ 与 schema 一致：{target}（{len(text.splitlines())} 行）")
            return 0
        line, exp, act = first_diff_line(text, actual)
        print(f"✗ 与 schema 不一致：{target}", file=sys.stderr)
        print(f"  第一处差异在第 {line} 行", file=sys.stderr)
        print(f"  期望：{exp[:200]}", file=sys.stderr)
        print(f"  实际：{act[:200]}", file=sys.stderr)
        print("  ⇒ 重跑：python3 tools/gen_protocol_doc.py --out <该路径>", file=sys.stderr)
        return 1

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print(f"✓ 已写入 {target}（{len(text.splitlines())} 行）")
        return 0

    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
