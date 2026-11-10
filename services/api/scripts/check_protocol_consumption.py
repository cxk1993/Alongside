#!/usr/bin/env python3
"""G13 · 协议字段的**运行时消费**门禁。

★ 它回答的问题不是「协议里有没有这个字段」，而是
  「内核有没有代码**按这个字段行动**」。
  两者的差距就是「看起来做完了」和「真的做完了」的差距。

★ 三种状态必须分清
------------------
| 状态 | 判据 | 危害 |
|---|---|---|
| 被消费 | 校验器之外的运行时代码读它，并且会改变结果 / 拦下启动 | —— |
| 半实现（`KNOWN_FORMAT_ONLY`） | **只有校验器**读它，只为判格式 | ★ 看起来像完成，人不会去补 |
| 机制未建（`KNOWN_UNCONSUMED`） | 没有任何代码读它 | 看得见是空的，不会被误判 |

★ 为什么「只判格式」不算消费
----------------------------
`validate.py` 读 `sideEffects` 只是为了在 manifest 不合规时**拒绝安装**——
那是「这个声明写得对不对」，不是「这个声明被遵守了」。
把它算成消费，就等于把「门禁存在」当成「机制存在」。

★ 两张登记表**不许过期**
------------------------
- 表里的字段一旦真的被行为路径消费 ⇒ 报红，逼着把这行删掉；
- Schema 里没人读、又没登记的字段 ⇒ 报红，逼着登记。
过期豁免会让门禁失去发现新问题的能力（只会一直绿）。

★ 自证的三条防线
----------------
1. 登记表与扫描结果**双向**比对（多登记、少登记、错档位都报红）；
2. 扫描目标是**路径常量**（`tools/paths.py`），扫不到目录会明确报「0 文件」并失败——
   一个扫了零个文件的门禁，永远绿，等于没上线；
3. `--list` 打印每个消费点的 `文件:行号(接收者)`，人可以复核启发式判得对不对。

用法：
    python services/api/scripts/check_protocol_consumption.py
    python services/api/scripts/check_protocol_consumption.py --json
    python services/api/scripts/check_protocol_consumption.py --list
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# ── 路径来自 tools/paths.py（唯一真相源）──
_TOOLS_DIR = Path(__file__).resolve().parents[3] / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from paths import API_PACKS, API_ROOT, CONTRACTS, CORE, MODULES, WEB_SRC, rel  # noqa: E402

SCHEMA_PATH = CONTRACTS / "plugin.schema.json"

#: 扫描范围（后端）：**运行时代码**。测试、迁移目录都不算消费现场。
SCAN_ROOTS: tuple[Path, ...] = (
    CORE,
    API_PACKS,
    MODULES,
    API_ROOT / "db",
)

#: 扫描范围（前端）：manifest 只有到 UI 才会被真正用上。
#: ★ 只扫 `kernel/**` 与 `apps/**`——`shared/**` 是通用组件，不读 manifest。
WEB_SCAN_ROOTS: tuple[Path, ...] = (
    WEB_SRC / "kernel",
    WEB_SRC / "apps",
)

#: 不参与消费判定的目录名（测试与被缓存文件）。
_EXCLUDED_DIRS = frozenset(
    {"__pycache__", "tests", "tests_packs", "tests_protocol", "migrations", ".venv"}
)

#: ★ 「只判格式」的唯一现场：manifest 校验器。
#:   跨插件校验（`crosscheck.py`）**不算**这里——它的违规会拦下启动，
#:   属于「改变运行结果」，因此算行为消费。
FORMAT_ONLY_FILES = frozenset(
    {
        rel(CORE / "plugins" / "validate.py"),
        # `core/manifest.py` 的模型层校验同理：它只回答「声明自身是否自洽」
        # （tabs 的 entry 唯一性、absorbs 不能吸收自己……），不据此改变运行时行为。
        rel(CORE / "manifest.py"),
    }
)

#: 接收者名里出现这些词，就认为读的是 manifest（而不是别的 dict）。
_MANIFEST_RECEIVERS = frozenset(
    {"raw", "m", "mf", "man", "manifest", "meta", "manifest_data", "plugin", "member"}
)

#: ★ 包层文件里的 `raw` / `data` 是**包清单**（pack.json），不是插件 manifest。
#:   同一个字段名（如 author / description）在两份契约里含义不同，
#:   不区分就会把「包的作者」当成「插件的 author 被消费了」——那是假绿。
_PACK_LAYER_GENERIC_RECEIVERS = frozenset({"raw", "data", "value", "item"})

# ─────────────────────────── 登记表 ───────────────────────────

#: ② 半实现：字段有、校验器读它，但运行时不按它行动。
#:   每条必须写「缺什么行为」——写不出缺什么，说明它其实被消费了，该摘掉。
KNOWN_FORMAT_ONLY: dict[str, str] = {
    "absorbs": (
        "模型层只校验「不能吸收自己 / 不与 tabs[].entry 重复」，"
        "但容器吸收（把子模块收进自己窗口）未实现"
    ),
    "concurrency": (
        "校验器查 exclusive / optimisticLock，但 plugin_state 无版本号，"
        "并发写仍后写覆盖先写"
    ),
    "execution": "校验器查 approval 约束，但沙箱等级与审批策略未在执行路径生效",
    "origin": (
        "校验器查 signed / installedBy，但插件来源审计未落库"
        "（todo 模块的 origin 是同名不同义，已排除）"
    ),
    "schedule": "校验器查选择器互斥，但定时任务模块不读它（现有定时器是模块内自建）",
    "sideEffects": "校验器查 checkpoint 约束，但 external: true 的插件不会真做检查点",
    "track": "校验器查「有 migrations 必须 persistent」，但持久/临时双轨未实现",
}

#: ③ 机制未建：连校验器都不读。
KNOWN_UNCONSUMED: dict[str, str] = {
    "author": "仅用于展示，暂无消费点（低优先）",
    "emits": "事件总线要按 manifest 声明驱动，而不是模块自己 publish",
    "extensions": "留给未来的扩展位，放行不报不拒（暂不实现）",
    "lifecycle": (
        "钩子由插件目录下的 api/lifecycle.py 驱动，manifest.lifecycle 声明本身未被读取"
    ),
    "outputPolicy": "大结果要按策略整形后再进上下文，不能直接塞",
}

# ─────────────────────────── 扫描 ───────────────────────────


def _iter_py(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    for p in sorted(root.rglob("*.py")):
        if any(part in _EXCLUDED_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def _receiver_is_manifest(node: ast.expr) -> bool:
    """这个表达式读的是不是一份 plugin manifest。"""
    try:
        text = ast.unparse(node)
    except Exception:  # noqa: BLE001 —— unparse 失败就当作不是（宁可少算，不可多算）
        return False
    low = text.lower()
    if "manifest" in low:
        return True
    return low in _MANIFEST_RECEIVERS


class _ReadSiteFinder(ast.NodeVisitor):
    """收集「按字段名读 manifest」的现场。

    三种形态：
      · `manifest.get("field")`（dict 形态，discover 之后到处都是）
      · `manifest["field"]`（少数）
      · `manifest.field`（Manifest 模型形态）
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sites: list[tuple[str, int, str, str]] = []  # (field, lineno, kind, receiver)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and _receiver_is_manifest(func.value)
        ):
            self.sites.append((node.args[0].value, node.lineno, "get", ast.unparse(func.value)))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        sl = node.slice
        if (
            isinstance(sl, ast.Constant)
            and isinstance(sl.value, str)
            and _receiver_is_manifest(node.value)
        ):
            self.sites.append((sl.value, node.lineno, "[]", ast.unparse(node.value)))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # 模型形态：manifest.concurrency（类体里的字段声明会被 AnnAssign 覆盖掉，
        # 不会走到这里；这里只收「读」）
        if _receiver_is_manifest(node.value):
            self.sites.append((node.attr, node.lineno, ".", ast.unparse(node.value)))
        self.generic_visit(node)


def scan(schema_fields: list[str]) -> tuple[dict[str, list[str]], dict[str, list[str]], int]:
    """返回 (行为消费点, 仅格式消费点, 扫描文件数)。"""
    wanted = set(schema_fields)
    behaviour: dict[str, list[str]] = {f: [] for f in schema_fields}
    format_only: dict[str, list[str]] = {f: [] for f in schema_fields}
    scanned = 0

    for root in SCAN_ROOTS:
        for path in _iter_py(root):
            scanned += 1
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            except OSError:
                # 并发跑测时临时模块会被创建/删除（rglob 列到、读时已没了）。
                # 少扫一个瞬时文件不影响结论；把它当致命错误反而让门禁假红。
                continue
            finder = _ReadSiteFinder(path)
            finder.visit(tree)
            where = rel(path)
            in_pack_layer = API_PACKS in path.parents
            for field, _line, _kind, recv in finder.sites:
                if field not in wanted:
                    continue
                if in_pack_layer and recv.split(".")[-1] in _PACK_LAYER_GENERIC_RECEIVERS:
                    continue
                bucket = format_only if where in FORMAT_ONLY_FILES else behaviour
                if where not in bucket[field]:
                    bucket[field].append(where)

    web_hits, web_scanned = scan_web(schema_fields)
    for field, files in web_hits.items():
        for f in files:
            if f not in behaviour[field]:
                behaviour[field].append(f)

    return behaviour, format_only, scanned + web_scanned


#: 前端只认这三种写法：`manifest?.x` / `manifest.x` / `manifest["x"]`。
_WEB_PATTERNS = (
    r"\bmanifest\s*\??\.\s*({alt})\b",
    r"\bmanifest\s*\[\s*['\"]({alt})['\"]\s*\]",
    # `(manifest as { tabs?: X })?.tabs` —— 前端对未定型字段的常见写法
    r"\(\s*manifest\b[^)]*\)\s*\??\.\s*({alt})\b",
)

#: ★ 特例：`PluginInfo` 是「后端插件项」的类型，`p.icon` 读的就是 manifest 里的 icon
#:   （后端把 manifest 摊平进插件项再下发）。只在**同时出现 PluginInfo 的文件**里认它，
#:   避免把所有叫 `p` 的变量都当成 manifest。
_WEB_PLUGININFO_PATTERN = r"\bp\s*\??\.\s*({alt})\b"


def _is_web_source(path: Path) -> bool:
    if path.suffix.lower() not in {".ts", ".tsx"}:
        return False
    if ".test." in path.name or ".spec." in path.name:
        return False
    # ★ 映射层不算消费方：`PluginRegistry.manifestToModule` 的职责是把后端 manifest
    #   **搬进**前端类型（36 字段逐个透传）。它读每个字段是为了搬运，不是为了使用。
    #   若把它算作消费点，`absorbs` / `emits` / `lifecycle` 会被误判成「已消费」，
    #   从而让真正的「未建机制」从诚实登记表里消失 —— 那是把诚实性打掉。
    #   真正的消费方是拿字段做决策的地方：moduleGraph（tabs 推导吸收）、
    #   containerPages（按 tabs 渲染页签）、Dock（按 absorbs 折叠入口）。
    if path.name == "PluginRegistry.ts" and "plugins" in path.parts:
        return False
    return not any(part in {"node_modules", "__tests__", "dist"} for part in path.parts)


def scan_web(schema_fields: list[str]) -> tuple[dict[str, list[str]], int]:
    """前端消费点。

    ★ 为什么前端也算消费：manifest 里 `window` / `entry` / `tabs` 这类字段，
      内核只是原样下发，真正**按它行动**的是 UI（开窗、加载入口、渲染页签）。
      只扫后端会把它们判成「未消费」——那是把「内核没动作」误当成「没人用」。
    ★ 注释行先剔掉：文档里到处是 `manifest.entry = ...` 的例子，
      照字面扫会把说明当消费点（这正是「门禁很好看，其实什么也没验」）。
    """
    alt = "|".join(re.escape(f) for f in sorted(schema_fields, key=len, reverse=True))
    manifest_res = [re.compile(p.format(alt=alt)) for p in _WEB_PATTERNS]
    pi_re = re.compile(_WEB_PLUGININFO_PATTERN.format(alt=alt))

    hits: dict[str, list[str]] = {}
    scanned = 0

    for root in WEB_SCAN_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or not _is_web_source(path):
                continue
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            has_plugin_info = "PluginInfo" in text
            where = rel(path)
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("//", "*", "/*")):
                    continue
                found: set[str] = set()
                for rx in manifest_res:
                    found.update(rx.findall(line))
                if has_plugin_info:
                    found.update(pi_re.findall(line))
                for field in found:
                    if field not in schema_fields:
                        continue
                    bucket = hits.setdefault(field, [])
                    if where not in bucket:
                        bucket.append(where)

    return hits, scanned


# ─────────────────────────── 判定 ───────────────────────────


#: ★ 交叉验证自检（`tools/crosscheck_validator.py --selfcheck`）会**就地注入** bug 到
#:   `core/plugins/validate.py` 再还原。进程被 kill / 被 CI 超时打断时，注入会留在源码里
#:   （实测：`required` 判据反转、`const` 反转、`minLength` 反向）。那种状态下
#:   「校验器只判格式」的判定、乃至整个协议门禁的绿灯都不可信 —— 先说清楚，再报结论。
_VALIDATOR = CORE / "plugins" / "validate.py"
_INJECTED_MARKERS: tuple[tuple[str, str], ...] = (
    ('and False:', "有一处判据被写成 `and False`（注入残留）"),
    ('value == schema["const"]', "const 判据被反转（注入残留）"),
    ('len(value) > schema["minLength"]', "minLength 判据被反向（注入残留）"),
    # ★ 不把 `if field in value:` 当标记：props 遍历里那句是**正常代码**
    #   （`for field, sub in props.items(): if field in value:`）。
    #   注入态的那句长这样：`for field in required: if field in value:`——
    #   只差一个 not，单独一条子串分不开，宁可不查也不误报。
)


def _validator_corruption() -> list[str]:
    """内核校验器是否处于「被注入」状态。"""
    try:
        src = _VALIDATOR.read_text(encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        return [f"读不到内核校验器 {rel(_VALIDATOR)}：{exc}"]
    return [f"{rel(_VALIDATOR)}：{why}" for needle, why in _INJECTED_MARKERS if needle in src]


def evaluate() -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    fields = list(schema.get("properties", {}).keys())
    behaviour, format_only, scanned = scan(fields)

    violations: list[str] = []

    # ⓿ 先验「世界是否可信」：内核校验器处于注入残留态时，后面的结论一律不许当真。
    violations.extend(
        f"内核校验器状态不可信：{why} —— 先还原 validate.py（多半是交叉验证自检被打断），再跑本门禁"
        for why in _validator_corruption()
    )

    if scanned == 0:
        violations.append(
            f"扫描范围里一个 .py 都没找到（{', '.join(rel(r) for r in SCAN_ROOTS)}）——"
            "门禁扫了零个文件，它的「通过」没有意义"
        )
    for r in SCAN_ROOTS:
        if not r.is_dir():
            violations.append(f"扫描根不存在：{rel(r)}")

    consumed = [f for f in fields if behaviour[f]]
    only_format = [f for f in fields if not behaviour[f] and format_only[f]]
    unconsumed = [f for f in fields if not behaviour[f] and not format_only[f]]

    # ① 登记了但已被行为消费 ⇒ 过期豁免
    for f in sorted(KNOWN_FORMAT_ONLY):
        if f not in fields:
            violations.append(f"KNOWN_FORMAT_ONLY 里的 `{f}` 在 Schema 里已不存在（改名了？）")
        elif behaviour[f]:
            violations.append(
                f"`{f}` 已登记为「仅格式」，但已有行为消费点 {behaviour[f][:2]}"
                " ⇒ 请从登记表摘掉"
            )
        elif not format_only[f]:
            violations.append(
                f"`{f}` 登记为「仅格式」，但连校验器都不读它 ⇒ 应移到 KNOWN_UNCONSUMED"
            )

    for f in sorted(KNOWN_UNCONSUMED):
        if f not in fields:
            violations.append(f"KNOWN_UNCONSUMED 里的 `{f}` 在 Schema 里已不存在（改名了？）")
        elif behaviour[f]:
            violations.append(
                f"`{f}` 已登记为「未消费」，但已有行为消费点 {behaviour[f][:2]} ⇒ 请从登记表摘掉"
            )
        elif format_only[f]:
            violations.append(f"`{f}` 登记为「未消费」，但校验器在读它 ⇒ 应移到 KNOWN_FORMAT_ONLY")

    # ② 没登记又没消费 ⇒ 新洞
    for f in unconsumed:
        if f not in KNOWN_UNCONSUMED:
            violations.append(
                f"`{f}` 没有任何消费点，也没登记 ⇒ 要么补实现，"
                "要么登记进 KNOWN_UNCONSUMED（并写清缺什么）"
            )
    for f in only_format:
        if f not in KNOWN_FORMAT_ONLY:
            violations.append(
                f"`{f}` 只有校验器读它（只判格式），也没登记 ⇒ "
                "登记进 KNOWN_FORMAT_ONLY（并写清缺什么行为）"
            )

    return {
        "schema": rel(SCHEMA_PATH),
        "fieldCount": len(fields),
        "scannedFiles": scanned,
        "scanRoots": [rel(r) for r in SCAN_ROOTS],
        "consumers": {f: behaviour[f] for f in fields if behaviour[f]},
        "formatOnly": {f: format_only[f] for f in fields if not behaviour[f] and format_only[f]},
        "unconsumed": unconsumed,
        "knownFormatOnly": sorted(KNOWN_FORMAT_ONLY),
        "knownUnconsumed": sorted(KNOWN_UNCONSUMED),
        "counts": {
            "consumed": len(consumed),
            "formatOnly": len(only_format),
            "unconsumed": len(unconsumed),
        },
        "violations": violations,
        "ok": not violations,
    }


def _render(result: dict[str, Any], *, show_sites: bool) -> None:
    counts = result["counts"]
    print(
        f"协议字段消费门禁（G13）· {result['fieldCount']} 字段 / 扫 {result['scannedFiles']} 个文件"
    )
    print(
        f"  被消费 {counts['consumed']} · 仅格式 {counts['formatOnly']}"
        f" · 未消费 {counts['unconsumed']}"
    )
    print()

    for field, sites in sorted(result["formatOnly"].items()):
        print(f"  △ {field:16s} 仅格式  {sites[0]}")
    for field in result["unconsumed"]:
        print(f"  ✗ {field:16s} 未消费  —")

    if show_sites and result["consumers"]:
        print("\n消费点明细（可复核启发式判定）：")
        for field, sites in sorted(result["consumers"].items()):
            print(f"  ✓ {field}")
            for s in sites[:5]:
                print(f"      {s}")

    if result["violations"]:
        print("\n✗ 门禁不通过：")
        for v in result["violations"]:
            print(f"  · {v}")
    else:
        print("\n✓ 登记表与扫描结果一致（无过期豁免、无未登记的洞）")


def selfcheck() -> int:
    """反向自证：门禁必须**抓得住**被做坏的三种情况，否则它的绿灯毫无意义。

    ★ 为什么要有这个：门禁最危险的失效方式不是报错，是「一直绿」。
      扫了空目录、登记表过期、判据写反——三种都表现为绿灯。
      这里逐一注入，每一种都必须让门禁报红。
    """
    checks: list[tuple[str, bool]] = []

    base = evaluate()
    checks.append(("基线应通过", base["ok"]))

    # ① 摘掉一条登记 ⇒ 必须报「有洞没登记」
    victim = sorted(KNOWN_UNCONSUMED)[0] if KNOWN_UNCONSUMED else sorted(KNOWN_FORMAT_ONLY)[0]
    table = KNOWN_UNCONSUMED if victim in KNOWN_UNCONSUMED else KNOWN_FORMAT_ONLY
    saved = table.pop(victim)
    try:
        got = evaluate()
        checks.append(
            (
                f"摘掉 {victim} 的登记应报红",
                (not got["ok"]) and any(victim in v for v in got["violations"]),
            )
        )
    finally:
        table[victim] = saved

    # ② 登记一条根本不存在的字段 ⇒ 必须报「登记表过期」
    ghost = "__definitely_not_a_field__"
    KNOWN_UNCONSUMED[ghost] = "自检用"
    try:
        got = evaluate()
        checks.append(
            (
                f"登记幽灵字段 {ghost} 应报红",
                (not got["ok"]) and any(ghost in v for v in got["violations"]),
            )
        )
    finally:
        KNOWN_UNCONSUMED.pop(ghost, None)

    # ③ 扫描根指到空目录 ⇒ 必须报「扫了零个文件」
    global SCAN_ROOTS, WEB_SCAN_ROOTS
    saved_roots, saved_web = SCAN_ROOTS, WEB_SCAN_ROOTS
    SCAN_ROOTS, WEB_SCAN_ROOTS = (CONTRACTS / "__nope__",), ()
    try:
        got = evaluate()
        checks.append(
            ("空扫描根应报红", (not got["ok"]) and any("零个" in v for v in got["violations"]))
        )
    finally:
        SCAN_ROOTS, WEB_SCAN_ROOTS = saved_roots, saved_web

    print("门禁自检（反向验证）")
    for label, ok in checks:
        print(f"  {'✓' if ok else '✗'} {label}")
    passed = sum(1 for _l, ok in checks if ok)
    print(f"\n{passed}/{len(checks)} 通过")
    return 0 if passed == len(checks) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="协议字段的运行时消费门禁（G13）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--list", action="store_true", help="打印每个字段的消费点明细")
    parser.add_argument(
        "--selfcheck", action="store_true", help="反向自证：注入三种失效，必须都报红"
    )
    args = parser.parse_args(argv)

    if args.selfcheck:
        return selfcheck()

    result = evaluate()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _render(result, show_sites=args.list)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
