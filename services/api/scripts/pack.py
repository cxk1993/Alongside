#!/usr/bin/env python
"""整合包作者命令面 —— `alongside pack check / lock / diff / audit`。

用法（在 `services/api` 目录下）：
    python scripts/pack.py check <包目录>
    python scripts/pack.py lock  <包目录>
    python scripts/pack.py diff  <包目录>
    python scripts/pack.py audit <包目录> [--source local]

★ 为什么这四条命令在这里，而不做成 HTTP 端点
--------------------------------------------
它们是**作者面**（写包的人在本机跑），不是**用户面**（装包的人在界面上点）。
`check` / `diff` 与 `audit` 都是只读的；只有 `lock` 写文件——而它写的是
**包目录里的基线锁**（随包分发的声明），不是运行期的装配快照。
⇒ 放进界面等于把「改包」这件事开给所有能登录的人。

★ 四条命令的分工（`packs/author.py` / `packs/audit.py` 的既有语义，本脚本只接线）
  check —— 跑 B1 结构 + B2 一致性，**不写任何文件**
  lock  —— 生成/更新基线锁（**基线锁的唯一写入口**）
  diff  —— 对比清单与基线，回答「装下去会新增/移除/升级什么」
  audit —— 静态审查：这个包会带来什么、有什么需要人拍板

★ 本脚本**不 import 内核运行时**（不建 app、不连库）：
  作者机器上可能根本没装这个包的成员，也没有数据库。
  ⇒ 只用 `packs.*` 的纯逻辑，插件索引通过 `core.plugins.discover` 现场扫磁盘。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:  # 让 `import packs.*` 在直接执行本文件时也成立
    sys.path.insert(0, str(_API_ROOT))

from packs.audit import AuditReport, audit_pack, format_audit  # noqa: E402
from packs.author import (  # noqa: E402
    CheckResult,
    DiffResult,
    check_pack,
    diff_pack,
    format_check,
    format_diff,
    lock_pack,
)


def _discover() -> Any | None:
    """现场发现本机插件。**失败就返回 None**，不拖垮命令。

    ★ 为什么允许 None：`check` / `diff` 的价值有一半在「本机没装成员也能验结构」。
      插件发现失败（契约坏了、跨插件冲突）时整条命令崩，等于把
      「包写错了」和「本机环境坏了」混成同一个失败——作者无从下手。
    """
    try:
        from core.plugins.discover import discover_plugins  # noqa: PLC0415

        return discover_plugins()
    except Exception as exc:  # noqa: BLE001 —— 见上：环境问题不该吞掉结论
        print(f"! 插件发现失败，本轮只做结构校验：{type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def _plugin_index(found: Any | None) -> dict[str, dict[str, Any]]:
    """`DiscoveryResult` → `{插件 id: manifest(+_directory)}`（`audit` 要的形状）。"""
    index: dict[str, dict[str, Any]] = {}
    for info in getattr(found, "plugins", []) or []:
        manifest = getattr(info, "manifest", None)
        if not isinstance(manifest, dict):
            continue
        entry = dict(manifest)
        directory = getattr(info, "directory", None)
        if directory is not None:
            entry["_directory"] = str(directory)
        index[str(getattr(info, "id", ""))] = entry
    return index


def _emit(payload: Any, *, as_json: bool, text: str) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2) if as_json else text)


def cmd_check(args: argparse.Namespace) -> int:
    found = _discover()
    result: CheckResult = check_pack(Path(args.pack_dir), discover=lambda: found)
    _emit(
        {"pack_id": result.pack_id, "ok": result.ok, "errors": list(result.errors),
         "warnings": list(result.warnings)},
        as_json=args.json,
        text=format_check(result),
    )
    return 0 if result.ok else 1


def cmd_lock(args: argparse.Namespace) -> int:
    found = _discover()
    lock = lock_pack(
        Path(args.pack_dir),
        discover=lambda: found,
        now=datetime.now(UTC).isoformat(),
        kernel_api=args.kernel_api,
    )
    _emit(
        {
            "pack_id": lock.pack_id,
            "pack_version": lock.pack_version,
            "generated_at": lock.generated_at,
            "members": [
                {"id": m.id, "version": m.version, "digest": m.digest} for m in lock.members
            ],
            "self_digest": lock.self_digest,
        },
        as_json=args.json,
        text=(
            f"基线锁已写入 {Path(args.pack_dir) / 'pack.lock.json'}："
            f"{lock.pack_id} {lock.pack_version} · 成员 {len(lock.members)} 个 · "
            f"摘要 {lock.self_digest or '算不出来'}"
        ),
    )
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    found = _discover()
    result: DiffResult = diff_pack(Path(args.pack_dir), discover=lambda: found)
    _emit(
        {
            "pack_id": result.pack_id,
            "added": list(result.added),
            "removed": list(result.removed),
            "upgraded": list(result.upgraded),
            "unchanged": list(result.unchanged),
            "digest_changed": list(result.digest_changed),
        },
        as_json=args.json,
        text=format_diff(result),
    )
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    found = _discover()
    index = _plugin_index(found)
    report: AuditReport = audit_pack(
        Path(args.pack_dir),
        source=args.source,
        plugins=index or None,
    )
    _emit(report.to_dict(), as_json=args.json, text=format_audit(report))
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pack",
        description="整合包作者命令面：check / lock / diff / audit（除 lock 外全部只读）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _add(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("pack_dir", help="包目录（含 pack.json）")
        p.add_argument("--json", action="store_true", help="输出 JSON（便于脚本消费）")
        return p

    _add("check", "校验清单（B1 结构 + B2 一致性），不写文件")
    lock = _add("lock", "生成 / 更新基线锁（唯一写入口）")
    lock.add_argument("--kernel-api", default=None, help="写入锁的 kernelApi 区间，如 ^1")
    _add("diff", "对比清单与基线：会新增 / 移除 / 升级什么")
    audit = _add("audit", "静态审查：这个包会带来什么、什么需要你拍板")
    audit.add_argument("--source", default="local", help="来源标记（默认 local）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "check": cmd_check,
        "lock": cmd_lock,
        "diff": cmd_diff,
        "audit": cmd_audit,
    }
    try:
        return handlers[args.command](args)
    except Exception as exc:  # noqa: BLE001 —— 命令面：坏包不该以 traceback 收场
        print(f"✗ {type(exc).__name__}: {exc}", file=sys.stderr)
        code = getattr(exc, "code", None)
        if code:
            print(f"  规则编号：{code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
