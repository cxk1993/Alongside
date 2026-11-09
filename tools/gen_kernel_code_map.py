#!/usr/bin/env python3
"""《Alongside 微内核代码地图》生成器 —— 模块 → 文件 → 行数 → 公开符号。

★ 为什么要有这个脚本
--------------------
代码地图最容易烂的地方不是「写得不好」，是**「什么在哪」过期**：
文件拆了、函数改名了、行数涨了，地图还停在上个月。
⇒ 事实部分（文件、行数、公开符号）从 AST 提取，人只写解读。

★ 这里只出「事实」，不出「解读」
--------------------------------
本脚本产出的是地图的**事实层**：哪个模块、多少行、对外暴露什么。
「这个文件为什么这么设计」「哪条是铁律」属于解读，手写在笔记里，
由 `--check` 只盯事实层——解读改了不该让门禁红。

★ 确定性
--------
- 不写生成时间；
- 排序 = 层序 → 相对路径字典序（不依赖文件系统遍历顺序）；
- 公开符号按**源码出现顺序**（不是字母序：源码顺序本身就是信息）；
- 换行归一为 `\\n`，末尾恰好一个换行。

用法：
    python3 tools/gen_kernel_code_map.py                     # 打到 stdout
    python3 tools/gen_kernel_code_map.py --out docs/x.md     # 写文件
    python3 tools/gen_kernel_code_map.py --check docs/x.md   # 只核对，不一致 exit 1
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any

# ★ 路径来自 tools/paths.py（唯一真相源）
_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from paths import CORE  # noqa: E402

LAYERS_PATH = CORE / "layers.py"

GROUP_ORDER: tuple[str, ...] = (
    "primitives",
    "policies",
    "conveniences",
    "assembler",
    "self",
    "markers",
    "unclassified",
)

GROUP_JUDGEMENT: dict[str, str] = {
    "primitives": "没它，插件装不起来",
    "policies": "换一套，系统还能跑",
    "conveniences": "对「能不能装」无影响",
    "assembler": "只在启动跑一次，不算内核",
    "self": "它定义层，不被层定义",
    "markers": "包标记文件，无分层含义",
    "unclassified": "⚠ 清单里没有登记",
}


def load_layers():
    spec = importlib.util.spec_from_file_location("_alongside_core_layers", LAYERS_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise SystemExit(f"✗ 读不到分层清单：{LAYERS_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cell(text: Any) -> str:
    s = "" if text is None else str(text)
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return " ".join(s.replace("|", "\\|").split())


def module_name(rel: str) -> str:
    """`plugins/manager.py` → `core.plugins.manager`；`plugins/__init__.py` → `core.plugins`。"""
    parts = rel[: -len(".py")].split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["core", *parts]) if parts else "core"


def first_doc_line(node: ast.Module) -> str:
    doc = ast.get_docstring(node)
    if not doc:
        return ""
    for line in doc.strip().split("\n"):
        if line.strip():
            return line.strip()
    return ""


def public_symbols(tree: ast.Module) -> list[dict[str, Any]]:
    """模块级公开 def/class（名字不以 `_` 开头），保持源码顺序。

    ★ 只看**模块顶层**：方法属于类，不属于「这个模块对外暴露什么」。
      类顺带记公开方法数——那是读地图时最想知道的一件事。
    """
    out: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            out.append({"kind": kind, "name": node.name, "methods": 0})
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            methods = sum(
                1
                for sub in node.body
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not sub.name.startswith("_")
            )
            out.append({"kind": "class", "name": node.name, "methods": methods})
    return out


def collect() -> tuple[list[dict[str, Any]], list[str]]:
    """返回 (rows, errors)。errors 非空时调用方应当拒绝写盘。"""
    layers = load_layers()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for path in CORE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(CORE).as_posix()
        text = path.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        layer = layers.layer_of(rel)
        if layer == "unknown":
            if rel == layers.SELF:
                group = "self"
            elif path.name in layers.PACKAGE_MARKERS:
                group = "markers"
            else:
                group = "unclassified"
        else:
            group = layer

        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:
            # ★ 不吞：语法错误的文件如果被当成「0 个符号」写进地图，
            #   地图会安静地少一块，比直接失败危险得多。
            errors.append(f"{rel}:{exc.lineno}: {exc.msg}")
            continue

        rows.append(
            {
                "rel": rel,
                "module": module_name(rel),
                "group": group,
                "lines": len(text.splitlines()),
                "doc": first_doc_line(tree),
                "symbols": public_symbols(tree),
            }
        )

    rows.sort(key=lambda r: (GROUP_ORDER.index(r["group"]), r["rel"]))
    return rows, errors


def group_label(layers, group: str) -> str:
    if group in ("primitives", "policies", "conveniences", "assembler"):
        return layers.LAYER_LABELS[group]
    return {
        "self": "分层清单自身",
        "markers": "包标记文件",
        "unclassified": "未登记文件",
    }[group]


def render() -> str:
    layers = load_layers()
    rows, errors = collect()
    if errors:
        raise SystemExit("✗ AST 解析失败，拒绝生成（地图不能安静地少一块）：\n  " + "\n  ".join(errors))

    total_files = len(rows)
    total_lines = sum(r["lines"] for r in rows)
    plugin_files = [r for r in rows if r["rel"].startswith("plugins/")]
    plugin_lines = sum(r["lines"] for r in plugin_files)
    total_symbols = sum(len(r["symbols"]) for r in rows)

    stats: list[tuple[str, list[dict[str, Any]]]] = []
    for group in GROUP_ORDER:
        items = [r for r in rows if r["group"] == group]
        if items:
            stats.append((group, items))

    out: list[str] = []
    add = out.append

    add("<!-- 本文件由 tools/gen_kernel_code_map.py 从真实代码生成，请勿手改。 -->")
    add("<!-- 「事实」层由脚本生成；「解读」层请手写在笔记里，本脚本不覆盖。 -->")
    add("")
    add("# Alongside 微内核代码地图 · 事实层")
    add("")
    add("> 这篇记的是**代码本身**：什么在哪、多少行、对外暴露什么。")
    add(">")
    add(f"> 真相源：`services/api/core/`（{total_files} 个文件，{total_lines} 行）")
    add("> 分层真相源：`services/api/core/layers.py`")
    add(">")
    add("> 设计**为什么**这么定，见《微内核技术报告》；")
    add("> 代码**逐字全文**，见《微内核代码》；")
    add("> 本文档只回答：**哪个模块在哪、多大、暴露什么**。")
    add(">")
    add("> ★ 本文档**不写生成时间**：时间戳会破坏确定性，`--check` 就永远对不上。")
    add("")
    add("---")
    add("")
    add("## 1 · 规模快照")
    add("")
    add("| 部分 | 文件数 | 行数 | 公开符号 |")
    add("|---|---:|---:|---:|")
    add(f"| 后端内核 `core/` | {total_files} | {total_lines} | {total_symbols} |")
    add(f"| ├ 其中 `core/plugins/` | {len(plugin_files)} | {plugin_lines} | "
        f"{sum(len(r['symbols']) for r in plugin_files)} |")
    add(f"| **合计** | **{total_files}** | **{total_lines}** | **{total_symbols}** |")
    add("")
    add("> 前端内核 `apps/web/src/kernel/` **不在本脚本范围内**——")
    add("> 它是界面层的内核，与后端内核不是同一份契约。")
    add("")

    add("---")
    add("")
    add("## 2 · 分层清单（读自 `core/layers.py`，不是手抄）")
    add("")
    add("| 层 | 判据 | 文件数 | 行数 | 公开符号 |")
    add("|---|---|---:|---:|---:|")
    for group, items in stats:
        add(
            f"| **{group_label(layers, group)}** | {GROUP_JUDGEMENT[group]} | "
            f"{len(items)} | {sum(i['lines'] for i in items)} | "
            f"{sum(len(i['symbols']) for i in items)} |"
        )
    add(f"| **合计** | | **{total_files}** | **{total_lines}** | **{total_symbols}** |")
    add("")
    add("> ★ 分层判据只有一条：**换一套系统时，这段代码要不要跟着换**。")
    add("> 「换一套权限模型内核要不要改」——答案取决于权限判定写在哪一层。")
    add("")

    add("---")
    add("")
    add("## 3 · 逐模块一览")
    add("")
    add("| 模块 | 文件 | 行数 | 公开符号数 |")
    add("|---|---|---:|---:|")
    for row in rows:
        add(f"| `{row['module']}` | `core/{row['rel']}` | {row['lines']} | {len(row['symbols'])} |")
    add(f"| **合计** | **{total_files} 个文件** | **{total_lines}** | **{total_symbols}** |")
    add("")

    add("---")
    add("")
    add("## 4 · 公开符号明细")
    add("")
    add("只列**模块顶层**、名字不以 `_` 开头的 `def` / `class`（AST 提取，保持源码顺序）。")
    add("方法属于类，不算模块的公开面——类后面括号里是它的公开方法数。")
    add("")
    for idx, (group, items) in enumerate(stats, start=1):
        add(f"### 4.{idx} {group_label(layers, group)}")
        add("")
        for row in items:
            add(f"#### `core/{row['rel']}`（{row['lines']} 行）")
            add("")
            if row["doc"]:
                add(f"> {cell(row['doc'])}")
                add("")
            if not row["symbols"]:
                add("- （无模块级公开符号）")
                add("")
                continue
            for sym in row["symbols"]:
                if sym["kind"] == "class":
                    add(f"- `class {sym['name']}`（{sym['methods']} 个公开方法）")
                else:
                    add(f"- `{sym['kind']} {sym['name']}`")
            add("")

    unclassified = [r for r in rows if r["group"] == "unclassified"]
    add("---")
    add("")
    add("## 5 · 清单与代码的差距")
    add("")
    if unclassified:
        add(f"⚠ 有 **{len(unclassified)}** 个文件在 `core/` 里存在，但 `layers.py` 没登记：")
        add("")
        for row in unclassified:
            add(f"- `core/{row['rel']}`（{row['lines']} 行）")
        add("")
        add("> 门禁会拦下新增未登记文件；这里如实列出，**不隐藏**。")
    else:
        add("✓ `core/` 下每个文件都在 `layers.py` 的清单里（或属于包标记 / 清单自身）。")
    add("")

    add("---")
    add("")
    add("## 6 · 生成信息")
    add("")
    add("```bash")
    add("# 重新生成（改内核代码后必须重跑）")
    add("python3 tools/gen_kernel_code_map.py --out <目标路径>.md")
    add("")
    add("# 只核对，不写盘（应当输出「与代码一致」）")
    add("python3 tools/gen_kernel_code_map.py --check <目标路径>.md")
    add("```")
    add("")
    add("- 生成器：`tools/gen_kernel_code_map.py`")
    add("- 分层真相源：`services/api/core/layers.py`")
    add("- 符号提取：`ast`（标准库），只取模块级 `def` / `class`")
    add(f"- 文件数：{total_files}　行数：{total_lines}　公开符号：{total_symbols}")
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
        description="从 services/api/core/ 生成微内核代码地图（事实层）",
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
            print(f"✓ 与代码一致：{target}（{len(text.splitlines())} 行）")
            return 0
        line, exp, act = first_diff_line(text, actual)
        print(f"✗ 与代码不一致：{target}", file=sys.stderr)
        print(f"  第一处差异在第 {line} 行", file=sys.stderr)
        print(f"  期望：{exp[:200]}", file=sys.stderr)
        print(f"  实际：{act[:200]}", file=sys.stderr)
        print("  ⇒ 重跑：python3 tools/gen_kernel_code_map.py --out <该路径>", file=sys.stderr)
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
