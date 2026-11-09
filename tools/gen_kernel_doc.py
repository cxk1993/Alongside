#!/usr/bin/env python3
"""《Alongside 微内核 · 代码》生成器 —— 把 `services/api/core/` 的真实源码逐字嵌进 Markdown。

★ 为什么要有这个脚本
--------------------
「内核现在跑的是哪份代码」这个问题，人抄一遍就会过期。
过期的方式很隐蔽：文档还在，字还对，只是**不是现在这份代码**了。
⇒ 文档不手写，从代码生成；`--check` 一跑就知道有没有分叉。

★ 确定性（本脚本的第一约束）
----------------------------
同样输入必须得到**同样字节**，否则 `--check` 永远报差异，门禁就废了。
所以：
  - 不写生成时间（时间戳是确定性的头号杀手）；
  - 文件按「层 → 相对路径」固定排序，不依赖文件系统遍历顺序；
  - 换行统一成 `\\n`（Windows 检出是 CRLF，不归一就会跨平台漂移）；
  - 末尾恰好一个换行（`--check` 才不会因为多一个空行误报）。

★ 围栏长度
----------
`core/db_scope.py` 的 docstring 里**本身就有** ```python 代码块。
外层用三反引号会把文档截断成两半——所以围栏长度按内容里最长的反引号串 +1 动态算，
最少 4 个。这不是洁癖：三反引号在这里是**真的会坏**。

用法：
    python3 tools/gen_kernel_doc.py                      # 打到 stdout
    python3 tools/gen_kernel_doc.py --out docs/x.md      # 写文件
    python3 tools/gen_kernel_doc.py --check docs/x.md    # 只核对，不一致 exit 1
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

# ★ 路径来自 tools/paths.py（唯一真相源），不自己数 parents
_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from paths import CORE  # noqa: E402

#: 分层清单（唯一真相源）。用文件路径直接加载，绕开 `core/__init__.py`
#: ——那个包会拉 sqlmodel 等运行期依赖，生成文档不该需要它们。
LAYERS_PATH = CORE / "layers.py"

#: 层的固定输出顺序。**顺序写死**，不随字典/文件系统顺序变。
GROUP_ORDER: tuple[str, ...] = (
    "primitives",
    "policies",
    "conveniences",
    "assembler",
    "self",
    "markers",
    "unclassified",
)

#: 层的判据说明。★ 这是**说明文字**，不是判据本身——
#: 判据是 `layers.py` 里那份清单（哪个文件在哪一层），这里只负责把它讲清楚。
GROUP_JUDGEMENT: dict[str, str] = {
    "primitives": "没它，插件装不起来",
    "policies": "换一套，系统还能跑",
    "conveniences": "对「能不能装」无影响",
    "assembler": "只在启动跑一次，不算内核",
    "self": "它定义层，不被层定义",
    "markers": "包标记文件，无分层含义",
    "unclassified": "⚠ 清单里没有登记",
}

#: 分组标题后缀（只对前四层有意义）。
GROUP_NOTE: dict[str, str] = {
    "policies": "（**待下沉为插件**）",
    "conveniences": "（**待移出内核**）",
}


def load_layers():
    """按文件路径加载 `core/layers.py`，返回模块对象。"""
    spec = importlib.util.spec_from_file_location("_alongside_core_layers", LAYERS_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - 只在文件缺失时
        raise SystemExit(f"✗ 读不到分层清单：{LAYERS_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_source(path: Path) -> str:
    """读源码并归一换行（CRLF/CR → LF）。

    ★ 必须归一：同一份代码在 Windows 检出是 CRLF、在 Linux 是 LF，
      不归一的话「同样输入」在两个平台生成出不同字节，`--check` 会误报。
    """
    text = path.read_bytes().decode("utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def fence_for(content: str) -> str:
    """按内容里最长的反引号串算围栏长度（最少 4）。"""
    longest = 0
    run = 0
    for ch in content:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return "`" * max(4, longest + 1)


def collect() -> list[dict]:
    """扫 `core/**/*.py`，按固定顺序返回 [{rel, group, text, lines}]。"""
    layers = load_layers()
    rows: list[dict] = []
    for path in CORE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(CORE).as_posix()
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
        text = read_source(path)
        rows.append(
            {
                "rel": rel,
                "group": group,
                "text": text,
                "lines": len(text.splitlines()),
            }
        )
    # ★ 固定顺序：先按层序，层内按相对路径字典序。不用 sorted(key=...) 的默认
    #   字符串比较以外的任何东西——任何「按修改时间」之类的排序都会破坏确定性。
    rows.sort(key=lambda r: (GROUP_ORDER.index(r["group"]), r["rel"]))
    return rows


def group_label(layers, group: str) -> str:
    if group in ("primitives", "policies", "conveniences", "assembler"):
        return layers.LAYER_LABELS[group]
    return {
        "self": "分层清单自身 —— 它定义层，不被层定义",
        "markers": "包标记文件",
        "unclassified": "未登记文件",
    }[group]


def render() -> str:
    layers = load_layers()
    rows = collect()

    total_files = len(rows)
    total_lines = sum(r["lines"] for r in rows)

    # 分组统计（只统计实际存在的组，保持 GROUP_ORDER 顺序）
    stats: list[tuple[str, list[dict]]] = []
    for group in GROUP_ORDER:
        items = [r for r in rows if r["group"] == group]
        if items:
            stats.append((group, items))

    out: list[str] = []
    add = out.append

    add("<!-- 本文件由 tools/gen_kernel_doc.py 从真实代码生成，请勿手改。 -->")
    add("<!-- 改内核代码请改代码，然后重跑生成脚本。 -->")
    add("")
    add("# Alongside 微内核 · 代码")
    add("")
    add("> **本文档是代码。**")
    add(">")
    add(f"> 真相源：`services/api/core/`（{total_files} 个文件，{total_lines} 行）")
    add(">")
    add("> 设计的**为什么**在《Alongside 微内核技术报告 v1.1》里；")
    add("> 代码的**地图**（文件职责、调用链、铁律）在《微内核代码地图》里；")
    add("> 本文档只回答一件事：**内核现在到底跑的是哪份代码**。")
    add(">")
    add("> 文档与代码冲突时，**以代码为准**，并重跑本脚本。")
    add(">")
    add("> ★ 本文档**不写生成时间**：时间戳会让同一份代码生成出不同字节，")
    add("> `--check` 就永远对不上。确定性优先于「看起来新鲜」。")
    add("")
    add("---")
    add("")
    add("## 1 · 真相源清单")
    add("")
    add("内核的分层**只有一个真相源**：`core/layers.py`。")
    add("下面这张表的文件清单与分组，全部从它读出来——**本文档不另存一份**。")
    add("")
    add("| 层 | 判据 | 文件数 | 行数 |")
    add("|---|---|---:|---:|")
    for group, items in stats:
        label = group_label(layers, group)
        add(
            f"| **{label}** | {GROUP_JUDGEMENT[group]} | {len(items)} | "
            f"{sum(i['lines'] for i in items)} |"
        )
    add(f"| **合计** | | **{total_files}** | **{total_lines}** |")
    add("")
    add("> ★ **范围**：本文档内嵌**后端内核** `services/api/core/`。")
    add("> 前端内核 `apps/web/src/kernel/` **不内嵌**——")
    add("> 它是界面层的内核，与后端内核不是同一份契约；地图见《微内核代码地图》。")
    add("")
    add("**怎么读这张表**：`①组合原语` 才是「无特权内核」的准确含义——")
    add("内核里只剩这一类。`②策略` 与 `③便利` 现在**还住在 `core/` 里**，")
    add("但按设计它们分别**待下沉为插件**、**待移出内核**（技术报告 §2.1）。")
    add("")
    add("---")
    add("")
    add("## 2 · 内核代码本体")
    add("")
    add("**内核就是下面这些代码。**")
    add("")
    add("本章每一段都是仓库里的**真实源码**，逐字嵌入——不是转述、不是摘要、不是示意。")
    add("技术部拿到这份文档，不需要另外打开仓库，就能读到内核的全部代码。")
    add("")
    add("> 嵌入时只做一件事：把 CRLF 归一成 LF（否则同一份代码在两个平台")
    add("> 生成出不同字节）。源码内容一个字符都不改。")
    add("")

    for idx, (group, items) in enumerate(stats, start=1):
        label = group_label(layers, group)
        note = GROUP_NOTE.get(group, "")
        lines = sum(i["lines"] for i in items)
        add(f"### 2.{idx} {label}{note}")
        add("")
        add(f"**判据**：{GROUP_JUDGEMENT[group]}。共 {len(items)} 个文件，{lines} 行。")
        add("")
        if group == "unclassified":
            add("> ⚠ 这些文件在 `core/` 里存在，但 `core/layers.py` 的清单没有登记它们。")
            add("> 门禁会拦下新增未登记文件；这里如实列出，**不隐藏**。")
            add("")
        for row in items:
            fence = fence_for(row["text"])
            add(f"#### `core/{row['rel']}`（{row['lines']} 行）")
            add("")
            add(f"{fence}python")
            body = row["text"]
            if body.endswith("\n"):
                body = body[:-1]
            add(body)
            add(fence)
            add("")

    add("---")
    add("")
    add("## 3 · 与姊妹文档的分工")
    add("")
    add("| 文档 | 回答什么 | 内嵌代码？ |")
    add("|---|---|:--:|")
    add(
        "| 《Alongside 微内核技术报告 v1.1》 | 内核**为什么这么设计**"
        "（三层分类 · 能力缝 · Phase Machine · effect 原语 · 依赖驱动 · 拦截层 · "
        "AI 改造防护 · 门禁） | ✗ |"
    )
    add(
        "| 《Alongside 微内核代码地图》 | 代码的**地图**：什么在哪、怎么串、"
        "哪里是铁律、一次调用的完整路径 | ✗（**故意不嵌**） |"
    )
    add("| **本文档** | 内核**现在跑的是哪份代码** | **✓ 逐字嵌入** |")
    add("")
    add("三篇合起来才是内核层的完整图景：**设计 → 地图 → 代码**。")
    add("")
    add("---")
    add("")
    add("## 4 · 生成信息")
    add("")
    add("```bash")
    add("# 重新生成（改内核代码后必须重跑）")
    add("python3 tools/gen_kernel_doc.py --out <目标路径>.md")
    add("")
    add("# 只核对，不写盘（应当输出「与代码一致」）")
    add("python3 tools/gen_kernel_doc.py --check <目标路径>.md")
    add("```")
    add("")
    add("- 生成器：`tools/gen_kernel_doc.py`")
    add("- 分层真相源：`services/api/core/layers.py`")
    add(f"- 内嵌文件数：{total_files}　内嵌行数：{total_lines}")
    add("- 默认输出：stdout（**不会**自动写进任何笔记目录，必须显式 `--out`）")

    return "\n".join(out).rstrip("\n") + "\n"


def first_diff_line(expected: str, actual: str) -> tuple[int, str, str]:
    """返回 (行号, 期望行, 实际行)；完全一致返回 (0, "", "")。"""
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
        description="从 services/api/core/ 生成《Alongside 微内核 · 代码》",
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
        print("  ⇒ 重跑：python3 tools/gen_kernel_doc.py --out <该路径>", file=sys.stderr)
        return 1

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        # newline="" 防止 Python 在 Windows 上把 \n 再翻成 \r\n
        with target.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print(f"✓ 已写入 {target}（{len(text.splitlines())} 行）")
        return 0

    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
