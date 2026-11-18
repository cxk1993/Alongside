#!/usr/bin/env python
"""源码卫生门禁 —— BOM 零容忍 + 十六进制色值只许住在 tokens.css。

★ 为什么需要这个文件
--------------------
两件都是「不会报错、只会慢慢变质」的事，靠人盯不住：

1. **UTF-8 BOM**。带 BOM 的 `.ts` 在 tsc / vitest 下照跑，所以没人会当场发现；
   但一旦有人按「文件首字节」或按行首做判据的工具链（AST 扫描、i18n 提取、
   `grep '^import'`）撞上它，表现是**静默跳过这个文件**——门禁绿着，文件没被读。
   本项目已有前科（PR#7 三个新文件带电，tsc/vitest 全绿）。
   ⇒ BOM 必须阻断：它唯一的「好处」是给 Windows 记事本看的，代价是判据失真。

2. **写死色值**。`tokens.css` 是颜色的唯一来源；组件里出现 `#5b8cff`，
   意味着换肤（皮肤插件 / 明暗主题）时这块颜色不跟着变，而且没人会去全仓库找它。
   存量已经有一批（见首次运行报告），所以这道规则**默认只报告**：
   一上来就 blocking 只会把人逼去加白名单，反而不如让人看着数字往下掉。

★ 两条纪律
----------
1. **逐文件、逐行流式**：机器常年吃紧（挖矿 / 25GB 内存），
   整树读进内存的写法在这里是不可接受的。只保留「发现」和「计数」。
2. **扫到零个文件必须报红**。门禁最常见的假绿是
   「根目录不存在 ⇒ 扫了 0 个文件 ⇒ 0 违规 ⇒ 退出码 0」。
   这里把扫到的文件数与每个根的落地情况都打出来，零文件直接退出码 1。

★ 判据（宁可漏报，不误报）
------------------------
BOM：文件**前 3 字节**等于 `EF BB BF`。只看文件头——行内出现 U+FEFF 是另一回事，不在此列。

hex：正则 `#[0-9a-fA-F]{6}\b`（`--include-short-hex` 时并入 `#[0-9a-fA-F]{3}\b`），
但下列上下文一律**不算**（这是「不误报」的全部退让，都计进 `suppressed` 供人核对）：
  · 注释内（JS/TS 的 `//` 行注释与 `/* */` 块注释；字符串里的 `//` 不当注释，
    所以 `"https://…"` 不会把后面半行吞掉；CSS 没有 `//` 行注释，
    因此 `url(//cdn/…)` 也不会被当成注释）；
  · 正则字面量上下文（整行出现 `= /`、`RegExp(`、`.test(`、`.match(`、`.exec(`、
    `.replace(/`、`.split(/`、`.search(` 之类标记时整行跳过）——
    `skin.ts` 里的 `HEX_RE = /^#[0-9a-f]{6}$/i` 属于这一类；
  · 紧跟量词（`#ffffff{6}`）、跟在 `[` 后面（字符类）；
  · `url(#…)` 里（那是 SVG 片段 id，不是颜色）。

`tokens.css`（按**文件名**，任意层级）与 `--allow` 给的 glob 免查 hex；
★ BOM 规则对它们**照样生效**——令牌文件带 BOM 一样会让工具静默跳文件。

★ 为什么不 import `tools/paths.py`
---------------------------------
本脚本要能对**任意目录**跑（`--root`，给作者在自己的工作区预检、给 CI 扫产物前的检查用）。
`paths.py` 的 `REPO` 是写死的发行结构，拿它当默认值可以，当唯一入口不行。
所以：默认根 = 本文件向上两级（`tools/gates/x.py` → 仓库根），`--root` 覆盖。

用法：
    python tools/gates/verify_source_hygiene.py
    python tools/gates/verify_source_hygiene.py --root path/to/tree --json
    python tools/gates/verify_source_hygiene.py --strict          # 写死色值也拦
    python tools/gates/verify_source_hygiene.py --include-tests   # 测试文件也查

退出码：
  0 = 无 BOM，且（未开 --strict 或没有写死色值）；
  1 = 有 BOM（blocking）/ --strict 下有写死色值 / 根目录不存在 / 扫到零个文件。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterator

#: 默认根：本文件在 `<repo>/tools/gates/` ⇒ parents[2] = 仓库根。
_DEFAULT_ROOT = Path(__file__).resolve().parents[2]

#: 扫的扩展名（小写比较）。
EXTS = frozenset({".ts", ".tsx", ".css"})

#: 在根下逐个子根扫：前端源码 + 每个第三方插件的 web 源码。
BASE_SUBPATH = Path("apps") / "web" / "src"
PLUGIN_SUBPATH = Path("web") / "src"

#: 这些目录不进去（`os.walk` 里原地剪掉，不产生遍历成本）。
PRUNE_DIRS = frozenset({"node_modules", "dist", ".vite", "coverage", "__pycache__", "__snapshots__"})

#: 允许写 hex 的文件（按文件名匹配，任意层级）。
HEX_ALLOW_BASENAMES = frozenset({"tokens.css"})

#: BOM 字节序与规则名（★ 分开命名：同名会让常量被字符串覆盖）。
BOM_BYTES = b"\xef\xbb\xbf"
BOM, HEX = "bom", "hex"

#: 六位色值；`(?<![\w&])` 避开 `&#123456;` 这类实体与 `foo#1a2b3c` 这类标识符尾部。
HEX6_RE = re.compile(r"(?<![\w&])#[0-9a-fA-F]{6}\b")
HEX3_RE = re.compile(r"(?<![\w&])#[0-9a-fA-F]{3}\b")

#: 正则字面量上下文标记：命中即整行不查 hex（宁可漏报）。
#: ★ 只留高置信标记：`= /`（赋值给正则）、`RegExp(`、`xxx.test(` / `.match(` / `.exec(` /
#:   `.replace(/` / `.split(/` / `.search(`。不要加 `(/` 这类泛标记——它会把
#:   `linear-gradient(...)` 之类整行放过，等于把真色值也漏掉。
REGEX_LINE_HINTS = (
    "RegExp(",
    ".test(",
    ".match(",
    ".exec(",
    ".replace(/",
    ".split(/",
    ".search(",
    "= /",
    "=/",
)

#: `url(#xxx)` —— SVG 片段 id。
URL_FRAGMENT_RE = re.compile(r"url\(\s*$", re.IGNORECASE)

#: 测试文件判据（默认排除：夹具里的颜色值是数据，不是设计令牌）。
TEST_NAME_RES = (re.compile(r"\.test\.[^./]+$"), re.compile(r"\.spec\.[^./]+$"))
TEST_DIRS = frozenset({"__tests__", "__mocks__"})


@dataclass(frozen=True)
class Finding:
    """一处问题。`line` 从 1 开始；`detail` 里带列号与原文片段。"""

    rule: str
    path: str
    line: int
    detail: str


@dataclass
class CommentState:
    """跨行的块注释状态（一个文件一份）。"""

    in_block: bool = False
    css: bool = False
    """CSS 没有 `//` 行注释：`url(//cdn/a.png)` 里的 `//` 不是注释开头。"""


@dataclass
class Counters:
    """扫描计数。门禁报告里要能看出「到底查了多少」。"""

    files: int = 0
    lines: int = 0
    skipped_tests: int = 0
    hex_exempt_files: list[str] = field(default_factory=list)
    suppressed_comment: int = 0
    suppressed_regex: int = 0
    suppressed_url: int = 0


# ─────────────────────────── 注释屏蔽 ───────────────────────────


def mask_comments(line: str, state: CommentState) -> str:
    """把注释区间替换成等长空格，返回屏蔽后的行（**下标与原文一一对应**）。

    ★ 为什么不直接丢整行：`const c = "#5b8cff"; // 主色` 这种行，
      丢掉就漏报；只屏蔽注释区间才对得上「这一行的代码里有没有色值」。

    ★ 为什么要跟踪字符串：`fetch("https://x")` 里的 `//` 不是注释开头。
      字符串内容不屏蔽（色值本来就住在字符串里）。
    """
    chars = list(line)
    i, n = 0, len(line)
    quote = ""
    while i < n:
        ch = line[i]
        if state.in_block:
            chars[i] = " "
            if ch == "*" and i + 1 < n and line[i + 1] == "/":
                chars[i + 1] = " "
                state.in_block = False
                i += 2
                continue
            i += 1
            continue
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = line[i + 1]
            if nxt == "*":
                chars[i] = chars[i + 1] = " "
                state.in_block = True
                i += 2
                continue
            if nxt == "/" and not state.css:
                for j in range(i, n):
                    chars[j] = " "
                break
        i += 1
    return "".join(chars)


# ─────────────────────────── hex 判定 ───────────────────────────


def hex_is_false_positive(masked: str, match: re.Match[str]) -> str:
    """判定一处 hex 命中是不是误报。返回原因（`""` = 真是色值）。

    只认三种上下文，且都能在屏蔽后的行上判：正则标记行 / 量词或字符类 / `url(#…)`。
    """
    start, end = match.start(), match.end()

    if any(hint in masked for hint in REGEX_LINE_HINTS):
        return "regex"
    if end < len(masked) and masked[end] == "{":
        return "regex"  # `#ffffff{6}` 是量词
    if start > 0 and masked[start - 1] == "[":
        return "regex"  # 字符类 `[#ffffff]`
    if URL_FRAGMENT_RE.search(masked[:start]):
        return "url"
    return ""


# ─────────────────────────── 单文件 ───────────────────────────


def is_test_file(path: Path) -> bool:
    if path.parent.name in TEST_DIRS:
        return True
    return any(rx.search(path.name) for rx in TEST_NAME_RES)


def scan_file(
    path: Path,
    label: str,
    counters: Counters,
    *,
    check_hex: bool,
    include_short_hex: bool,
) -> list[Finding]:
    """流式扫一个文件：先读 3 字节判 BOM，再逐行判 hex。**不整文件读入内存**。"""
    findings: list[Finding] = []
    state = CommentState(css=path.suffix.lower() == ".css")

    with path.open("rb") as fh:
        if fh.read(3) == BOM_BYTES:
            findings.append(
                Finding(
                    BOM,
                    label,
                    1,
                    "文件以 UTF-8 BOM（EF BB BF）开头——AST / 行首判据类工具会静默跳过此文件。",
                )
            )
        fh.seek(0)
        for lineno, raw in enumerate(fh, 1):
            counters.lines += 1
            if not check_hex:
                # 只判 BOM：不必解码、不必跑状态机。
                continue

            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            masked = mask_comments(line, state)

            counted = len(HEX6_RE.findall(line))
            if include_short_hex:
                counted += len(HEX3_RE.findall(line))

            matches: list[re.Match[str]] = list(HEX6_RE.finditer(masked))
            if include_short_hex:
                matches = sorted([*matches, *HEX3_RE.finditer(masked)], key=lambda m: m.start())

            # ★ 注释里的命中在 `masked` 里已经被抹成空格，所以差值就是「注释里的色值」。
            counters.suppressed_comment += max(counted - len(matches), 0)

            for match in matches:
                reason = hex_is_false_positive(masked, match)
                if reason == "regex":
                    counters.suppressed_regex += 1
                    continue
                if reason == "url":
                    counters.suppressed_url += 1
                    continue
                snippet = line.strip()
                if len(snippet) > 120:
                    snippet = snippet[:117] + "…"
                findings.append(
                    Finding(
                        HEX,
                        label,
                        lineno,
                        f"{match.group(0)} 写死在源码里（列 {match.start() + 1}）"
                        f"——颜色只该来自 tokens.css 的 var(--*)：{snippet}",
                    )
                )

    counters.files += 1
    return findings


# ─────────────────────────── 遍历 ───────────────────────────


def iter_scan_roots(root: Path) -> list[tuple[str, Path]]:
    """要扫的根：`apps/web/src` + 每个 `plugins/*/web/src`。缺失的也要列出来（不静默）。"""
    roots: list[tuple[str, Path]] = [(BASE_SUBPATH.as_posix(), root / BASE_SUBPATH)]
    plugins = root / "plugins"
    if plugins.is_dir():
        for child in sorted(plugins.iterdir()):
            if not child.is_dir():
                continue
            src = child / PLUGIN_SUBPATH
            if src.is_dir():
                roots.append((f"plugins/{child.name}/{PLUGIN_SUBPATH.as_posix()}", src))
    return roots


def iter_files(
    base: Path, counters: Counters, *, include_tests: bool
) -> Iterator[Path]:
    """在 `base` 下逐文件产出源码。原地剪枝 `PRUNE_DIRS`：不进 node_modules / dist。"""
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            d for d in dirnames if d not in PRUNE_DIRS and not d.startswith(".")
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix.lower() not in EXTS:
                continue
            if not include_tests and is_test_file(path):
                counters.skipped_tests += 1
                continue
            yield path


# ── 内置豁免：为什么这两处 hex 不是「样式写死」 ─────────────────────
#   豁免必须有理由，且理由写在这里而不是命令行的 --allow 里 —— 否则下一次
#   有人跑门禁时不带参数，就会把这两处重新报成问题，然后被当成噪音忽略。
BUILTIN_ALLOW: tuple[tuple[str, str], ...] = (
    (
        "apps/web/src/kernel/wallpaperApply.tsx",
        "取色器 defaultValue 的 JS 兜底：'读不到 --bg 才用它' 是刻意的降级路径，"
        "不是样式写死。CSS 侧颜色全部走 var()。",
    ),
    (
        "apps/web/src/shared/styles/skin.ts",
        "toHex6() 的 #000000 是算法中性值（颜色解析失败时的返回值），"
        "属于计算常量而非设计色值；该文件不产生任何 CSS。",
    ),
)


def is_hex_exempt(label: str, name: str, allow: tuple[str, ...]) -> bool:
    """某文件是否免查 hex。

    ★ 判据走**相对仓库根的 POSIX 路径**（label），不是裸文件名 —— 文件名会重名，
      而症状是「--allow 写了但没生效」——比报错更难发现。
    ★ 另外 HEX_ALLOW_BASENAMES 是按文件名的老判据（tokens.css 靠它免查），
      两者都要认：删了它 = 令牌文件自己被判成违规。
    """
    if name in HEX_ALLOW_BASENAMES:
        return True
    builtin = tuple(pat for pat, _why in BUILTIN_ALLOW)
    return any(fnmatch(label, pat) or fnmatch(name, pat) for pat in (*builtin, *allow))


# ─────────────────────────── 主流程 ───────────────────────────


def scan(
    root: Path,
    *,
    include_tests: bool = False,
    include_short_hex: bool = False,
    allow: tuple[str, ...] = (),
) -> dict[str, object]:
    counters = Counters()
    findings: list[Finding] = []
    root_reports: list[dict[str, object]] = []
    root = root.resolve()

    for label, base in iter_scan_roots(root):
        exists = base.is_dir()
        before = counters.files
        if exists:
            for path in iter_files(base, counters, include_tests=include_tests):
                rel_label = path.resolve().relative_to(root).as_posix()
                check_hex = not is_hex_exempt(rel_label, path.name, allow)
                if not check_hex:
                    counters.hex_exempt_files.append(rel_label)
                findings.extend(
                    scan_file(
                        path,
                        rel_label,
                        counters,
                        check_hex=check_hex,
                        include_short_hex=include_short_hex,
                    )
                )
        root_reports.append(
            {"path": label, "exists": exists, "files": counters.files - before}
        )

    bom = [f for f in findings if f.rule == BOM]
    hexes = [f for f in findings if f.rule == HEX]

    return {
        "root": root.as_posix(),
        "roots": root_reports,
        "scanned": {
            "files": counters.files,
            "lines": counters.lines,
            "test_files_skipped": counters.skipped_tests,
            "hex_exempt_files": sorted(set(counters.hex_exempt_files)),
        },
        "suppressed": {
            "comment": counters.suppressed_comment,
            "regex": counters.suppressed_regex,
            "url": counters.suppressed_url,
        },
        "bom": [{"path": f.path, "line": f.line, "detail": f.detail} for f in bom],
        "hex": [
            {"path": f.path, "line": f.line, "detail": f.detail} for f in hexes
        ],
        "hex_files": sorted({f.path for f in hexes}),
        "zero_scan": counters.files == 0,
        "missing_roots": [r["path"] for r in root_reports if not r["exists"]],
    }


def decide(result: dict[str, object], *, strict: bool) -> int:
    """算退出码。规则集中在这里，测试与 CLI 共用一份判据。"""
    if result["zero_scan"]:
        return 1
    if result["missing_roots"]:
        return 1
    if result["bom"]:
        return 1
    if strict and result["hex"]:
        return 1
    return 0


def render(result: dict[str, object], *, strict: bool, exit_code: int, limit: int) -> str:
    scanned = result["scanned"]
    suppressed = result["suppressed"]
    assert isinstance(scanned, dict) and isinstance(suppressed, dict)

    lines = ["源码卫生门禁（BOM 零容忍 / 十六进制色值集中在 tokens.css）", f"根：{result['root']}", "扫描根："]
    for item in result["roots"]:
        assert isinstance(item, dict)
        mark = "✓" if item["exists"] else "✗ 不存在"
        lines.append(f"  {item['path']} ({mark}) — {item['files']} 个文件")

    lines.append(
        f"扫到 {scanned['files']} 个文件 / {scanned['lines']} 行"
        f"（排除测试文件 {scanned['test_files_skipped']} 个；需查测试用 --include-tests）。"
    )
    exempt = scanned["hex_exempt_files"]
    if exempt:
        lines.append(f"hex 免查（令牌文件 / --allow）：{len(exempt)} 个 → {exempt[:5]}")
    if scanned["files"] == 0:
        lines.append("")
        lines.append("✗ 扫到 0 个文件 —— 这不是「通过」，是「没查到东西」。")
        lines.append("  ★ 门禁最常见的假绿就是这一行：根目录指错时它安静地返回 0。")
        lines.append("")

    bom = result["bom"]
    assert isinstance(bom, list)
    if bom:
        lines.append("")
        lines.append(f"✗ BOM {len(bom)} 个（blocking）：")
        for item in bom[:limit]:
            assert isinstance(item, dict)
            lines.append(f"  {item['path']}")
        if len(bom) > limit:
            lines.append(f"  …另有 {len(bom) - limit} 个，未逐条列出（--limit 可调）。")
    elif scanned["files"] == 0:
        # ★ 零样本不能画勾：那和「查过没问题」长得一模一样。
        lines.append("")
        lines.append("— BOM：无样本可查。")
    else:
        lines.append("")
        lines.append(f"✓ 无 BOM（{scanned['files']} 个文件全查过）。")

    hexes = result["hex"]
    assert isinstance(hexes, list)
    hex_files = result["hex_files"]
    assert isinstance(hex_files, list)
    lines.append("")
    if hexes:
        mode = "blocking（--strict）" if strict else "仅报告"
        lines.append(
            f"△ 写死色值 {len(hexes)} 处 / {len(hex_files)} 个文件（{mode}）："
        )
        for item in hexes[:limit]:
            assert isinstance(item, dict)
            lines.append(f"  {item['path']}:{item['line']}：{item['detail']}")
        if len(hexes) > limit:
            lines.append(f"  …另有 {len(hexes) - limit} 处，未逐条列出（--limit 可调）。")
    elif scanned["files"] == 0:
        lines.append("— 写死色值：无样本可查。")
    else:
        lines.append("✓ 没有写死的十六进制色值。")

    total_suppressed = suppressed["comment"] + suppressed["regex"] + suppressed["url"]
    if total_suppressed:
        lines.append("")
        lines.append(
            f"△ 判为误报而放过的 hex {total_suppressed} 处"
            f"（注释 {suppressed['comment']} / 正则上下文 {suppressed['regex']} / url() {suppressed['url']}）"
            "——宁可漏报不误报，此处即盲区，供人工核对。"
        )

    lines.append("")
    if exit_code == 0:
        lines.append("结论：✓ 通过" + ("（写死色值仅报告）" if hexes else ""))
    elif result["zero_scan"]:
        lines.append("结论：✗ 没扫到任何文件（判据不成立）")
    elif result["missing_roots"]:
        lines.append(f"结论：✗ 扫描根不存在 {result['missing_roots']}")
    elif bom:
        lines.append("结论：✗ 有 BOM（阻断）")
    else:
        lines.append("结论：✗ 有写死色值（--strict 生效，阻断）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="源码卫生门禁：UTF-8 BOM 零容忍（blocking）+ 组件内十六进制色值检查（默认只报告）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        default=str(_DEFAULT_ROOT),
        metavar="PATH",
        help="扫描根（默认：本脚本向上两级的仓库根）。可指向任意目录，便于预检。",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（给 CI / 别的脚本消费）")
    parser.add_argument(
        "--strict",
        action="store_true",
        default=True,
        help=(
            "写死色值也算失败。★ 2026-11-18 起**默认开启**：存量 139 处已清零"
            "（A 类删死兜底 85 / B 类修真 bug 11 / C 类映射语义色 43）。"
        ),
    )
    parser.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="只报告不阻断（临时排查存量时用；CI 与门禁清单必须走默认的 strict）。",
    )
    parser.add_argument(
        "--include-tests", action="store_true", help="连 *.test.* / *.spec.* / __tests__ 一起查"
    )
    parser.add_argument(
        "--include-short-hex",
        action="store_true",
        help="连三位缩写色值（#fff）一起查（默认只查六位，缩写与 id 混淆率高）。",
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="GLOB",
        help="额外的 hex 免查 glob（可重复）。BOM 规则不受影响。",
    )
    parser.add_argument("--limit", type=int, default=40, help="每类最多逐条列出多少处（默认 40）")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"✗ 扫描根不存在或不是目录：{root.as_posix()}", file=sys.stderr)
        print("  门禁不能在空目录上假装通过。", file=sys.stderr)
        return 1

    result = scan(
        root,
        include_tests=args.include_tests,
        include_short_hex=args.include_short_hex,
        allow=tuple(args.allow),
    )
    code = decide(result, strict=args.strict)

    if args.json:
        payload = {**result, "strict": args.strict, "exit_code": code, "ok": code == 0}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(result, strict=args.strict, exit_code=code, limit=args.limit))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
