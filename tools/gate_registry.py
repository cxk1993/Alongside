#!/usr/bin/env python
"""门禁清单 —— 「这个仓库里有哪些门禁、它们拦不拦、有没有人守着它们」。

★ 为什么需要这份清单
--------------------
门禁的价值取决于**有人知道它在跑**。仓库里 `tools/` 与 `services/api/scripts/`
混着三类脚本：门禁（跑完给退出码）、一次性探针（probe_*）、生成器（gen_* / create_*）。
它们长得都像「一个 Python 文件」，于是「哪些是必须一直绿的」只能靠人记。
记不住的结果有两种，都很难看：
  · 一道门禁坏了没人发现（它绿着，但扫的是空目录）；
  · 一道门禁还在跑，但没人知道它拦什么（于是有人绕过它，理由是「不知道会拦」）。

⇒ 清单把「有哪些门禁」变成可读、可自查的东西。

★ 三个字段各自回答什么
--------------------
| 字段 | 回答 |
|---|---|
| `blocking` | 发现问题时**会不会拦**（退出码非 0）。false = 只报告，不阻断。 |
| `has_regression_test` | 有没有测试守着**这道门禁自己**（不是它守的东西）。 |
| `path` | 脚本在哪。登记了但文件不存在 = 清单在说谎，必须拦。 |

★ 自查（本脚本每次运行都做）
--------------------------
扫 `tools/**/*.py` 与 `services/api/scripts/*.py`，用两条判据找「像门禁的脚本」：
  1. 文件名：`check_*` / `verify_*` / `crosscheck_*` / `predeploy_*` / `gate_*` /
     `guard_*` / `audit_*`，或以 `_check` / `_guard` / `_gate` / `_verify` 结尾；
  2. 内容：有 `sys.exit(...)` / `SystemExit(...)`，且出现「预检 / 门禁 / 守门 /
     BLOCKING / 退出码」这类门禁语义。
明确排除：`probe_*` / `gen_*` / `create_*` / `export_*` / `backup*` / `remote_*`
——它们是探针或生成器，不是门禁。

**未登记不一定是错**（可能是刚写的、或确实只是一次性脚本），但必须被列出来让人看一眼。
**登记了却找不到文件一定是错**——清单在说谎，退出码 1。

用法：
    python tools/gate_registry.py
    python tools/gate_registry.py --json

退出码：0 = 清单自洽（未登记项只提示）；1 = 有登记项指向不存在的文件 / 测试。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
_REPO = _TOOLS.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from paths import API_ROOT, REPO, rel  # noqa: E402


@dataclass(frozen=True)
class Gate:
    """一道门禁。"""

    id: str
    path: str
    description: str
    blocking: bool
    """发现问题时会不会拦（退出码非 0）。false = 只报告。"""

    has_regression_test: bool
    """有没有测试守着**这道门禁自己**。"""

    test_ref: str = ""
    """测试文件（相对仓库根）。`has_regression_test=True` 时必须有，且文件要存在。"""


#: 登记表。★ 新增门禁时在这里加一行——不加也能跑，但 `--json` 的自查会把它列成未登记。
GATES: tuple[Gate, ...] = (
    Gate(
        id="pack-invariants",
        path="tools/gates/verify_pack_invariants.py",
        description="包层不变量 I1~I13（协议 §6 的静态可判部分）+ 锁/装配契约交叉验证。",
        blocking=True,
        has_regression_test=False,
        test_ref="",
    ),
    Gate(
        id="contract-changelog",
        path="tools/check_contract_changelog.py",
        description="改了契约（expected.json）却没写 changelog ⇒ BLOCKING。",
        blocking=True,
        has_regression_test=False,
    ),
    Gate(
        id="api-docs",
        path="tools/check_api_docs.py",
        description="API / MCP 注释覆盖率；低于 --fail-under 时退出码非 0。",
        blocking=True,
        has_regression_test=False,
    ),
    Gate(
        id="research-alignment",
        path="tools/check_research_alignment.py",
        description="协议 ↔ 调研产物的机制名词覆盖核查。",
        blocking=True,
        has_regression_test=True,
        test_ref="services/api/tests_protocol/test_protocol_quality_gates.py",
    ),
    Gate(
        id="validator-crosscheck",
        path="tools/crosscheck_validator.py",
        description="自研 JSON Schema 校验器 vs 标准 jsonschema 的结论一致性。",
        blocking=True,
        has_regression_test=True,
        test_ref="services/api/tests_protocol/test_protocol_quality_gates.py",
    ),
    Gate(
        id="predeploy-guard",
        path="tools/predeploy_guard.py",
        description="部署前核对工作区：他人在制改动 / 构建新鲜度 / 契约一致性 ⇒ BLOCKING。",
        blocking=True,
        has_regression_test=False,
    ),
    Gate(
        id="deploy-preflight",
        path="tools/deploy_package_preflight.py",
        description="部署包完整性预检：变更文件清单 vs path→MD5 表，缺口即非 0。",
        blocking=True,
        has_regression_test=False,
    ),
    Gate(
        id="prod-health",
        path="tools/prod_health_check.py",
        description="生产健康自检（Push / SSE 入场券 / PWA / 包内容 / 首屏体积）。",
        blocking=True,
        has_regression_test=False,
    ),
    Gate(
        id="kernel-purity",
        path="services/api/scripts/check_kernel_purity.py",
        description="内核（core/ 与前端 kernel/）不得出现业务词汇——「一切皆插件」的机器判据。",
        blocking=True,
        has_regression_test=False,
    ),
    Gate(
        id="t18-criterion",
        path="services/api/scripts/verify_t18_criterion.py",
        description="T18 唯一判据：加工具 = 插件 manifest 多一行 provides（起真实服务验证）。",
        blocking=True,
        has_regression_test=False,
    ),
    Gate(
        id="protocol-consumption",
        path="services/api/scripts/check_protocol_consumption.py",
        description=(
            "G13 · 协议字段的运行时消费门禁：Schema 里的字段有没有代码按它行动。"
            "自带反向自证 --selfcheck（摘登记 / 幽灵字段 / 空扫描根，三种都必须报红）。"
        ),
        blocking=True,
        has_regression_test=False,
    ),
)

#: 文件名判据：前缀或后缀命中即视为「像门禁」。
_GATE_NAME_RE = re.compile(
    r"^(check|verify|crosscheck|predeploy|gate|guard|audit)_|_(check|guard|gate|verify)$"
)

#: 明确不是门禁的前缀（探针 / 生成器 / 运维）。
_NOT_GATE_PREFIXES = ("probe_", "gen_", "create_", "export_", "backup", "remote_")

#: 明确不是门禁的路径（相对仓库根）。
#: ★ 本清单自己会被文件名判据命中（`gate_registry`）——它不是门禁，是门禁的目录。
#:   `tools/accept_probe/` 是 L2 验收探针套件，`tools/task.py` 是任务入口：
#:   它们都会给退出码，但「跑一次验一条纪律」不是它们的职责。
_NOT_GATE_PATHS = frozenset(
    {
        "tools/gate_registry.py",
        "tools/task.py",
    }
)
_NOT_GATE_DIRS = ("tools/accept_probe/",)

#: 内容判据里的门禁语义关键词。
_GATE_WORDS = ("预检", "门禁", "守门", "BLOCKING", "blocking", "退出码")


def _scan_targets() -> list[Path]:
    """扫 `tools/**/*.py` 与 `services/api/scripts/*.py`。"""
    out: list[Path] = []
    for path in sorted(_TOOLS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.append(path)
    scripts = API_ROOT / "scripts"
    if scripts.is_dir():
        out.extend(sorted(p for p in scripts.glob("*.py") if p.is_file()))
    return out


def _looks_like_gate(path: Path) -> tuple[bool, str]:
    """像不像门禁。返回 `(判定, 判据说明)`。"""
    rel_path = rel(path)
    if rel_path in _NOT_GATE_PATHS or rel_path.startswith(_NOT_GATE_DIRS):
        return False, ""
    stem = path.stem
    if stem.startswith(_NOT_GATE_PREFIXES):
        return False, ""
    if _GATE_NAME_RE.search(stem):
        return True, "文件名"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, ""
    if ("sys.exit(" in text or "SystemExit(" in text) and any(w in text for w in _GATE_WORDS):
        return True, "内容（退出码 + 门禁语义）"
    return False, ""


def audit() -> dict[str, object]:
    """自查：登记项是否落地、有没有像门禁却没登记的脚本。"""
    registered = {g.path for g in GATES}
    missing: list[dict[str, str]] = []
    bad_test_ref: list[dict[str, str]] = []

    for gate in GATES:
        if not (REPO / gate.path).is_file():
            missing.append({"id": gate.id, "path": gate.path})
        if gate.has_regression_test:
            if not gate.test_ref:
                bad_test_ref.append({"id": gate.id, "test_ref": "", "why": "声明有测试但没写 test_ref"})
            elif not (REPO / gate.test_ref).is_file():
                bad_test_ref.append(
                    {"id": gate.id, "test_ref": gate.test_ref, "why": "test_ref 指向的文件不存在"}
                )

    scanned = _scan_targets()
    unregistered: list[dict[str, str]] = []
    for path in scanned:
        rel_path = rel(path)
        if rel_path in registered:
            continue
        looks, why = _looks_like_gate(path)
        if looks:
            unregistered.append({"path": rel_path, "why": why})

    return {
        "gates": [asdict(g) for g in GATES],
        "scan": {
            "roots": ["tools/**/*.py", "services/api/scripts/*.py"],
            "script_count": len(scanned),
        },
        "missing": missing,
        "bad_test_ref": bad_test_ref,
        "unregistered": unregistered,
        "ok": not missing and not bad_test_ref,
    }


def render(result: dict[str, object]) -> str:
    gates = result["gates"]
    assert isinstance(gates, list)
    lines = [f"门禁清单（tools/gate_registry.py）—— 登记 {len(gates)} 个", ""]
    lines.append(f"  {'id':<22} {'拦':<2} {'回归测试':<8} 路径")
    lines.append(f"  {'-' * 22} {'-' * 2} {'-' * 8} {'-' * 40}")
    for gate in gates:
        block = "✓" if gate["blocking"] else "—"
        test = "✓" if gate["has_regression_test"] else "✗"
        lines.append(f"  {gate['id']:<22} {block:<2} {test:<8} {gate['path']}")
        lines.append(f"  {'':<22} {'':<2} {'':<8} {gate['description']}")

    scan = result["scan"]
    assert isinstance(scan, dict)
    lines.append("")
    lines.append(f"自查：扫 {scan['roots']}，共 {scan['script_count']} 个脚本。")

    unregistered = result["unregistered"]
    assert isinstance(unregistered, list)
    if unregistered:
        lines.append("")
        lines.append(f"△ 像门禁但未登记 {len(unregistered)} 个（未登记不一定是错，但请看一眼）：")
        for item in unregistered:
            lines.append(f"  - {item['path']}（判据：{item['why']}）")
    else:
        lines.append("✓ 没有「像门禁却没登记」的脚本。")

    missing = result["missing"]
    assert isinstance(missing, list)
    if missing:
        lines.append("")
        lines.append(f"✗ 登记项指向不存在的文件 {len(missing)} 个（清单在说谎）：")
        for item in missing:
            lines.append(f"  - {item['id']} → {item['path']}")
    else:
        lines.append("✓ 登记项全部落地。")

    bad_test = result["bad_test_ref"]
    assert isinstance(bad_test, list)
    if bad_test:
        lines.append("")
        lines.append(f"✗ 回归测试引用有问题 {len(bad_test)} 个：")
        for item in bad_test:
            lines.append(f"  - {item['id']} → {item['test_ref'] or '（空）'}：{item['why']}")

    no_test = [g["id"] for g in gates if not g["has_regression_test"]]
    if no_test:
        lines.append("")
        lines.append(f"△ 没有回归测试守着 {len(no_test)} 个门禁：{no_test}")
        lines.append("  ★ 这不是错，是风险：门禁自己坏了没人发现（它绿着，但可能扫的是空目录）。")

    lines.append("")
    lines.append("结论：" + ("✓ 清单自洽" if result["ok"] else "✗ 清单有问题"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="门禁清单：登记表 + 自查（未登记项 / 登记项是否落地 / 有无回归测试）。"
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    result = audit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
