#!/usr/bin/env python3
"""门禁 G19 · 图标契约：manifest.icon 必须落在内核图标表里。

★ 为什么需要这道门禁
--------------------
`contracts/plugin.schema.json` 写着 icon 是「从内核图标表挑选的图标名」，
但在这道门禁之前，**没有任何东西保证这句话是真的**：
  · 插件作者写个 `icon: "rocket"`，schema 放行、门禁放行，界面上就会显示
    一个叫 `rocket` 的英文单词（或退回首字），而且没人发现；
  · 内核改了图标表（删了一个键），所有用它的 manifest 会静默退化成文本。

⇒ 判据必须来自**代码里的那张表**，不是文档里的清单：
     apps/web/src/kernel/icons/default-set.tsx    （默认集：语义名 → 图形）
     apps/web/src/kernel/icons/registry.ts        （别名：globe → web）
   本脚本从这两个文件里 parse 出「合法语义名集合」，再扫全部 manifest。

★ 允许的三种取值（与运行时兜底链一一对应）
   1. 语义名：在 default-set 的键集合里；
   2. 别名：在 registry 的 ALIASES 里（当前只有 globe→web）；
   3. 单字符：中文/日文/韩文单字（manifest 里确实有「课」「记」「程」），
      运行时走「单字直出」兜底 —— 这是刻意保留的合法形态，不是漏网。

★ 退出码
   0 = 全部合法（或 --report 模式下只报告）
   1 = 有非法 icon / 有 manifest 读不出来 / 图标表解析不到（表不见了比值错了更严重）
   2 = 参数错误

用法：
    python tools/gates/verify_icon_coverage.py            # 拦
    python tools/gates/verify_icon_coverage.py --report   # 只报告，永远 0
    python tools/gates/verify_icon_coverage.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent
_REPO = _TOOLS.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

DEFAULT_SET = _REPO / "apps" / "web" / "src" / "kernel" / "icons" / "default-set.tsx"
REGISTRY = _REPO / "apps" / "web" / "src" / "kernel" / "icons" / "registry.ts"
MODULES_DIR = _REPO / "services" / "api" / "modules"
PLUGINS_DIR = _REPO / "plugins"


@dataclass
class Report:
    ok: bool = True
    semantic_names: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    checked: int = 0
    glyph_ok: list[str] = field(default_factory=list)
    missing: list[tuple[str, str, str]] = field(default_factory=list)  # (manifest, module, icon)
    bad_manifests: list[tuple[str, str]] = field(default_factory=list)  # (manifest, reason)

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "semantic_names": self.semantic_names,
            "aliases": self.aliases,
            "checked": self.checked,
            "glyph_ok": self.glyph_ok,
            "missing": [{"manifest": m, "module": mod, "icon": i} for m, mod, i in self.missing],
            "bad_manifests": [{"manifest": m, "reason": r} for m, r in self.bad_manifests],
        }


_KEY_RE = re.compile(r'^\s{2}"?([A-Za-z][A-Za-z0-9-]*)"?: \($', re.M)
_ALIAS_RE = re.compile(r'^\s{2}([A-Za-z][A-Za-z0-9-]*):\s*"([A-Za-z][A-Za-z0-9-]*)",$', re.M)


def parse_icon_table(set_path: Path, registry_path: Path) -> tuple[list[str], dict[str, str]]:
    """从 TSX/TS 源码里 parse 出语义名集合与别名表。

    ★ 用「行首两空格 + 键 + 冒号 + 左括号」做判据，而不是 import 执行：
      这两个文件是 TSX（含 JSX 字面量），Python 无法直接求值；
      而它们的书写形态由 prettier 固定（对象字面量、每键一行），
      形态漂移会被下面「解析结果为空即判红」兜住。
    """
    if not set_path.exists():
        raise SystemExit(f"图标表不存在：{set_path}（表没了比值错了更严重，直接判红）")
    if not registry_path.exists():
        raise SystemExit(f"图标注册表不存在：{registry_path}")

    names = sorted(set(_KEY_RE.findall(set_path.read_text(encoding="utf-8"))))
    aliases = dict(_ALIAS_RE.findall(registry_path.read_text(encoding="utf-8")))
    # 别名值也必须是合法语义名，否则别名是死的
    for src, dst in aliases.items():
        if dst not in names:
            raise SystemExit(f"别名 {src} → {dst} 指向不存在的语义名（别名是死的）")
    return names, aliases


def iter_manifests() -> list[Path]:
    out: list[Path] = []
    if MODULES_DIR.exists():
        out.extend(sorted(MODULES_DIR.glob("*/manifest.json")))
    if PLUGINS_DIR.exists():
        out.extend(sorted(PLUGINS_DIR.glob("*/manifest.json")))
    return out


def is_single_glyph(value: str) -> bool:
    return len(list(value)) == 1


def check(report: Report) -> None:
    report.semantic_names, report.aliases = parse_icon_table(DEFAULT_SET, REGISTRY)
    legal = set(report.semantic_names) | set(report.aliases)

    for path in iter_manifests():
        # 目录可能是被测试注入的临时目录（不在仓库下），此时退回绝对路径，不要抛
        try:
            rel = path.relative_to(_REPO).as_posix()
        except ValueError:
            rel = str(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - 坏 JSON 本身就是门禁要抓的
            report.bad_manifests.append((rel, f"JSON 解析失败：{e}"))
            continue
        mod_id = str(data.get("id") or path.parent.name)
        icon = data.get("icon")
        report.checked += 1
        if icon is None:
            # 无 icon 是合法的（运行时退回首字），但记一笔方便审计覆盖率
            continue
        if not isinstance(icon, str):
            report.missing.append((rel, mod_id, repr(icon)))
            continue
        if icon in legal:
            continue
        if is_single_glyph(icon):
            report.glyph_ok.append(f"{mod_id}={icon}")
            continue
        report.missing.append((rel, mod_id, icon))

    report.ok = not report.missing and not report.bad_manifests and bool(report.semantic_names)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="G19 · manifest.icon 必须落在内核图标表里")
    ap.add_argument("--report", action="store_true", help="只报告不拦（永远 0，供巡检用）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    report = Report()
    check(report)

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=1))
    else:
        print("图标契约门禁（manifest.icon → 内核语义名）")
        print(
            f"图标表：{len(report.semantic_names)} 个语义名 / {len(report.aliases)} 个别名；"
            f"扫到 {report.checked} 个 manifest"
        )
        if report.glyph_ok:
            print(f"△ 单字兜底（合法）：{len(report.glyph_ok)} 个 → {', '.join(report.glyph_ok[:6])}")
        if report.missing:
            print(f"✗ 非法 icon {len(report.missing)} 个：")
            for rel, mod, icon in report.missing:
                print(f"  {rel}（模块 {mod}）= {icon}")
        if report.bad_manifests:
            print(f"✗ 读不出的 manifest {len(report.bad_manifests)} 个：")
            for rel, why in report.bad_manifests:
                print(f"  {rel}：{why}")
        print("结论：" + ("✓ 通过" if report.ok else "✗ 有非法 icon（加进 default-set.tsx 或改用已有语义名）"))

    if args.report:
        return 0
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
