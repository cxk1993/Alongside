"""内核洁癖检查（T14）——「一切皆插件」唯一可机器验证的判据。

## 判据

> 内核（`kernel/` 与 `core/`）**不认识任何业务**。
> 它不知道什么叫"日程""待办""笔记""理财""习惯"。

所以这两处**不得出现业务词汇**：

    apps/web/src/kernel/    （不含 plugins/ 与 slots/ —— 那是插件机制的落点，T14 的）
    services/api/core/      （不含 plugins/）

一旦出现，说明有人为了做一件具体的事去动了框架 ——
**按总纲，那是框架的 bug，不是需求的问题。**

## 只查代码，不查散文

朴素的 `grep -rnE "calendar|todo|..."` 会误报，实测踩到过四种：

1. 解释规则本身的**文档字符串**（`core/__init__.py` 里就写着这几个词）；
2. 注释里的英文 **`TODO`**（大写，跟"待办"业务无关）；
3. 参数说明里的**示例文案**（`如 calendar.*,todo.*`）；
4. ★ **脱敏必需的敏感字段名** `beecount_pass` —— 删掉它就是安全回退。

所以本检查器**先剥掉注释与文档字符串，只在剩下的代码（标识符与字符串字面量）里匹配**；
确有正当理由保留的，在该行写 `purity-ok: <理由>` 显式豁免（理由必填，便于日后复核）。

## 用法

    python scripts/check_kernel_purity.py          # 干净则 exit 0，脏则 exit 1
    python scripts/check_kernel_purity.py -v       # 同时打印扫描范围

`make lint` 会在本文件存在时自动调用它（见 tools/task.py）。
"""
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from collections.abc import Iterator
from pathlib import Path

# 业务词汇（小写匹配）。★ 新增业务插件时**不要**往这里加词 ——
# 加词等于承认"内核该认识某个业务"，方向反了。
BUSINESS_WORDS = (
    "calendar",
    "todo",
    "notes",
    "finance",
    "habit",
    "beecount",
    "obsidian",
)

SCAN_ROOTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("apps/web/src/kernel", ("plugins", "slots", "node_modules", "dist")),
    ("services/api/core", ("plugins", "__pycache__")),
)

#: 按 scope 分组的扫描目标（2026-11-09 新增）。
#:
#: ★ 为什么要分：`verify` 里这道门禁是**阻塞**的，而实测后端内核是干净的、
#:   前端内核有 13 处已知债务（`TimeWidget` / `Dock` / `DesktopSidebar` 里硬编码了
#:   业务模块名）。前端那部分是「真相源没搬完」的后果，要重构，不是本期能顺手清掉的。
#:   ⇒ 拆成两道：后端**阻塞**（地基必须干净），前端**如实报告、暂不阻塞**。
#:   这不是把红灯涂绿——前端债务写进了 `docs/specs/microkernel-and-packs.md`，
#:   且报告每次都打出来，谁都能看见。
SCOPES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "backend": (("services/api/core", ("plugins", "__pycache__")),),
    "frontend": (("apps/web/src/kernel", ("plugins", "slots", "node_modules", "dist")),),
    "all": SCAN_ROOTS,
}

#: 测试文件里出现的业务词多半是 **DOM testid / 断言字面量**，不是「内核认识业务」。
#: 实测 42 处误报全部来自这里（`getByTestId("today-sum-calendar-auth")` 之类）。
#: ⇒ 默认跳过测试文件；要连测试一起查请显式加 `--include-tests`。
TEST_FILE_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", ".test.js", ".test.jsx")

PY_SUFFIXES = {".py"}
JS_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

ESCAPE_MARK = "purity-ok"

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _blank(spans: list[tuple[int, int, int]], lines: list[str]) -> list[str]:
    """把若干 (lineno, start_col, end_col) 区段替换成空格（保留行号与列号）。"""
    out = list(lines)
    for lineno, start, end in spans:
        if 1 <= lineno <= len(out):
            row = out[lineno - 1]
            start = max(0, min(start, len(row)))
            end = max(start, min(end, len(row)))
            out[lineno - 1] = row[:start] + " " * (end - start) + row[end:]
    return out


def _strip_python(source: str, lines: list[str]) -> list[str]:
    """剥掉注释与文档字符串，返回等长的代码视图。"""
    spans: list[tuple[int, int, int]] = []

    # 1) 注释
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                spans.append((tok.start[0], tok.start[1], tok.end[1]))
    except (tokenize.TokenError, IndentationError):
        pass

    # 2) 文档字符串（模块 / 类 / 函数的第一条字符串语句）
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list) or not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                start, end = first.value.lineno, first.value.end_lineno or first.value.lineno
                spans.append((start, first.value.col_offset, first.value.end_col_offset or 0))
                for mid in range(start + 1, end):
                    spans.append((mid, 0, len(lines[mid - 1])))
    except SyntaxError:
        pass

    return _blank(spans, lines)


_JS_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_JS_LINE = re.compile(r"//[^\n]*")


def _strip_js(source: str, lines: list[str]) -> list[str]:
    """剥掉 // 与 /* */ 注释，返回等长的代码视图。"""
    spans: list[tuple[int, int, int]] = []
    for m in _JS_BLOCK.finditer(source):
        start_line = source.count("\n", 0, m.start()) + 1
        end_line = source.count("\n", 0, m.end()) + 1
        if start_line == end_line:
            col0 = m.start() - source.rfind("\n", 0, m.start()) - 1
            spans.append((start_line, col0, col0 + (m.end() - m.start())))
        else:
            col0 = m.start() - source.rfind("\n", 0, m.start()) - 1
            spans.append((start_line, col0, len(lines[start_line - 1])))
            for mid in range(start_line + 1, end_line):
                spans.append((mid, 0, len(lines[mid - 1])))
            col1 = m.end() - source.rfind("\n", 0, m.end()) - 1
            spans.append((end_line, 0, col1))
    for m in _JS_LINE.finditer(source):
        lineno = source.count("\n", 0, m.start()) + 1
        col0 = m.start() - source.rfind("\n", 0, m.start()) - 1
        spans.append((lineno, col0, col0 + (m.end() - m.start())))
    return _blank(spans, lines)


def _iter_files(root: Path, excluded: tuple[str, ...]) -> Iterator[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (PY_SUFFIXES | JS_SUFFIXES):
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in excluded for part in rel_parts[:-1]):
            continue
        yield path


def _scan(
    pairs: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    include_tests: bool = False,
    root_override: Path | None = None,
) -> tuple[list[tuple[Path, int, str, str]], int]:
    """扫描给定 (root_rel, excluded) 组合，返回 (violations, 扫描文件数)。

    ★ 2026-11-24（汐瑶）：从 main() 抽出来的 —— 为的是让 selfcheck 能对**临时目录**跑。
      门禁最危险的失效是「一直绿」，所以要能喂它干净与脏两种输入。
      `root_override` 让自检不碰真实仓库。
    """
    base = root_override or PROJECT_ROOT
    violations: list[tuple[Path, int, str, str]] = []
    scanned = 0
    for rel_root, excluded in pairs:
        root = base / rel_root
        for path in _iter_files(root, excluded):
            if not include_tests and path.name.endswith(TEST_FILE_SUFFIXES):
                continue
            scanned += 1
            source = path.read_text(encoding="utf-8", errors="replace")
            raw_lines = source.splitlines()
            code_lines = (
                _strip_python(source, raw_lines)
                if path.suffix in PY_SUFFIXES
                else _strip_js(source, raw_lines)
            )
            for idx, line in enumerate(code_lines, 1):
                low = line.lower()
                for word in BUSINESS_WORDS:
                    if word in low:
                        # 显式豁免：该行、或它上一行写了 `purity-ok: <理由>`
                        # （允许写在上一行是为了不撑爆行宽限制）
                        window = raw_lines[max(0, idx - 2) : idx]
                        if any(ESCAPE_MARK in w for w in window):
                            break
                        violations.append((path, idx, word, raw_lines[idx - 1].strip()))
                        break
    return violations, scanned



def selfcheck() -> int:
    """反向自证：**脏的必须红，干净的绝不能误红**。

    ★ 2026-11-24（汐瑶）：加这条的直接原因 —— 今天发现另一道门禁
      （`check_api_docs`）硬编码清单后虚报 100% 两个多月，**因为它一直是绿的**。
      门禁的失效有两种，第二种更坏：

      ① 瞎了：脏的也不报 ⇒ 守卫形同虚设；
      ② 误红：干净的也报 ⇒ 人开始绕过它（「反正这条总红」）⇒ 守卫被主动关掉。

    所以这里两条都钉。全部在临时目录里跑，**不碰真实仓库**。
    """
    import tempfile

    checks: list[tuple[str, bool]] = []
    pairs = (("core", ()),)

    with tempfile.TemporaryDirectory() as td:
        core = Path(td) / "core"
        core.mkdir(parents=True, exist_ok=True)

        # ── 基线：完全干净的文件，不许报 ────────────────────────
        (core / "clean.py").write_text(
            '"""内核只做框架。"""\n\n\ndef dispatch(name: str) -> str:\n    return name\n',
            encoding="utf-8",
        )
        v, n = _scan(pairs, root_override=Path(td))
        checks.append(("基线：干净文件不报（scanned=1）", len(v) == 0 and n == 1))

        # ── ① 瞎了？：代码里出现业务词，必须报 ─────────────────
        (core / "dirty.py").write_text(
            '"""模块文档，这里提到 calendar 是散文，不该报。"""\n\n'
            "def load():\n    return calendar_query()\n",
            encoding="utf-8",
        )
        v, _ = _scan(pairs, root_override=Path(td))
        hits = [x for x in v if x[0].name == "dirty.py"]
        checks.append(("① 代码里的业务词必须报红", len(hits) >= 1))
        checks.append(("① 命中词必须是 calendar", any(x[2] == "calendar" for x in hits)))

        # ── ② 误红？：只在 docstring / 注释里出现，不许报 ──────
        (core / "doconly.py").write_text(
            '"""这个文件的 docstring 提到 todo 和 notes，都是散文。"""\n\n'
            "# 下面这行注释也提到 finance，仍然是散文\n"
            "def handler():\n    return 1\n",
            encoding="utf-8",
        )
        v, _ = _scan(pairs, root_override=Path(td))
        checks.append(
            ("② 只在 docstring / 注释里的业务词不许报（误红会逼人绕过门禁）",
             not any(x[0].name == "doconly.py" for x in v))
        )

        # ── ③ 豁免：带 purity-ok 的行不许报 ───────────────────
        (core / "escaped.py").write_text(
            "# purity-ok: 脱敏必需的字段名，删掉它是安全回退\n"
            'beecount_pass = "x"\n',
            encoding="utf-8",
        )
        v, _ = _scan(pairs, root_override=Path(td))
        checks.append(
            ("③ 写 purity-ok 的行不许报（豁免机制必须真生效）",
             not any(x[0].name == "escaped.py" for x in v))
        )

        # ── ④ 测试文件默认跳过（实测 42 处误报全来自 testid） ──
        (core / "x.test.ts").write_text(
            'const id = "today-sum-calendar-auth";\n', encoding="utf-8"
        )
        v, n = _scan(pairs, root_override=Path(td))
        checks.append(
            ("④ 测试文件默认跳过（testid 里的业务词不算违规）",
             not any(x[0].name == "x.test.ts" for x in v))
        )
        v2, _ = _scan(pairs, root_override=Path(td), include_tests=True)
        checks.append(
            ("④ --include-tests 时测试文件要真被扫到",
             any(x[0].name == "x.test.ts" for x in v2))
        )

    print("kernel-purity 门禁自检（反向验证）")
    ok = True
    for name, passed in checks:
        print(f"  {'✓' if passed else '✗'} {name}")
        ok = ok and passed
    print()
    print("结论：" + ("✓ 自检通过 —— 脏的会红、干净的不会误红" if ok else "✗ 自检失败 —— 门禁本身坏了"))
    return 0 if ok else 1

def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--selfcheck" in args:
        return selfcheck()
    verbose = "-v" in args or "--verbose" in args
    warn_only = "--warn-only" in args
    include_tests = "--include-tests" in args
    scope = "all"
    for i, a in enumerate(args):
        if a == "--scope" and i + 1 < len(args):
            scope = args[i + 1]
    if scope not in SCOPES:
        print(f"[purity] 未知 scope：{scope!r}（可选 {'/'.join(SCOPES)}）")
        return 2

    if verbose:
        for rel_root, excluded in SCOPES[scope]:
            print(f"[purity] 扫描 {rel_root}（排除 {'/'.join(excluded)}）")
    violations, scanned = _scan(SCOPES[scope], include_tests=include_tests)

    if verbose:
        print(f"[purity] 共扫描 {scanned} 个代码文件")

    if not violations:
        print(f"[purity] 内核干净：{scanned} 个代码文件里没有业务词汇 ✓")
        return 0

    print(f"[purity] ✗ 发现 {len(violations)} 处业务词汇出现在内核代码里：")
    for path, idx, word, line in violations:
        rel = path.relative_to(PROJECT_ROOT)
        print(f"  {rel}:{idx}  命中 {word!r}")
        print(f"      {line[:120]}")
    print()
    print("内核不认识业务。为一件具体的事去动 framework —— 按总纲，那是框架的 bug。")
    print("正确做法：把它做成插件；需要新的扩展点就走 docs/rfc/ 提 RFC。")
    print(f"确有正当理由保留的，在该行写 `{ESCAPE_MARK}: <理由>` 显式豁免。")
    if warn_only:
        print()
        print("[purity] --warn-only：以上仅**报告**，不阻塞（债务已登记在 docs/specs）。")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
