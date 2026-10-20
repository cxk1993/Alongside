#!/usr/bin/env python
"""契约变更守门（提案 B）—— 「改了 expected.json 却没写 changelog」= BLOCKING。

★ 由来（讨论第一轮 · 提案 B）：T03 判据演进、`mcp_tools 25→26`、hash 五连跳……
  契约变更一直靠"先回帖再改"的**纪律**维系，无机读记录。业界锚点（dev.to《pre-flight gate》）：
  「**The remaining gap is reviewability. A green CI run does not show a reviewer exactly
  what changed and what you checked.**」—— 绿 CI ≠ 可审查。
  本脚本把纪律变成机读约束。

约定（expected.json）：
{
  "_meta": {
    "changelog": [
      {"at": "2026-09-23T11:30", "by": "workbuddy", "field": "mcp_tools.count",
       "from": 25, "to": 26, "reason": "push 上线 provides 机械映射",
       "post": "life-chat/workbuddy/最新/xxx.md"}
    ]
  },
  "suites": { ... }
}

检查逻辑：
- 无工作区改动（expected.json 未修改）→ 只查结构（有无 _meta.changelog），缺则 WARNING
- **有工作区改动** → changelog 必须存在，且最后一条的 `at` 必须是**今天或更新**
  （改契约不写记录 = BLOCKING）
- `x_` 前缀跳过（扩展位思路，与 D′ 一致）

用法：
    python tools/check_contract_changelog.py
    python tools/check_contract_changelog.py --json out.json
退出码：0 = PASS；1 = BLOCKING。零第三方依赖。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Path(__file__).resolve().parents[1] / "tools" / "accept_probe" / "expected.json"
# PortableGit bundled with the local toolchain (path derived, not hard-coded to a user name).
_PORTABLE_GIT = str(
    Path.home() / ".workbuddy" / "binaries" / "PortableGit" / "versions" / "1.2.0" / "cmd" / "git.exe"
)
GIT_CANDIDATES = ("git", _PORTABLE_GIT)


def _git_exe() -> str | None:
    import shutil

    env = __import__("os").environ.get("GIT_EXE")
    if env and Path(env).is_file():
        return env
    found = shutil.which("git")
    if found:
        return found
    for c in GIT_CANDIDATES[1:]:
        if Path(c).is_file():
            return c
    return None


def _git(git: str, *args: str) -> str:
    r = subprocess.run([git, *args], cwd=REPO_ROOT, capture_output=True, text=True)
    return r.stdout


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    checks: list[tuple[str, str, str]] = []  # (level, name, detail)

    raw = CONTRACT.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"⛔ BLOCKING expected.json 不是合法 JSON：{e}")
        return 1

    meta = data.get("_meta") or {}
    changelog = meta.get("changelog")

    # git 状态：expected.json 是否有未提交改动
    git = _git_exe()
    modified = False
    if git:
        out = _git(git, "status", "--porcelain", "--", str(CONTRACT.relative_to(REPO_ROOT)))
        modified = bool(out.strip())
    else:
        checks.append(("WARNING", "git 不可用", "无法判断 expected.json 是否有未提交改动"))

    # 结构检查（无条件）
    if changelog is None:
        checks.append(
            (
                "WARNING",
                "_meta.changelog 未初始化",
                "契约变更将无从追溯；建议按 _meta.changelog 约定补一条初始化条目",
            )
        )
    elif not isinstance(changelog, list):
        checks.append(("BLOCKING", "_meta.changelog 不是数组", f"实际类型 {type(changelog).__name__}"))
    else:
        checks.append(("PASS", "changelog 结构存在", f"{len(changelog)} 条记录"))

    # 变更守门（关键）
    if modified:
        if not changelog:
            checks.append(
                ("BLOCKING", "契约已改但无变更记录", "改 expected.json 必须在 _meta.changelog 追加一条（先回帖再改的机读化）")
            )
        else:
            last = changelog[-1]
            at = str(last.get("at", ""))
            try:
                at_dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
                # 允许 ±14h 时差容错
                today = datetime.now(timezone.utc).date()
                ok_date = at_dt.date() >= today - timedelta(days=1)
            except ValueError:
                ok_date = False
            if not ok_date:
                checks.append(
                    (
                        "BLOCKING",
                        "契约已改但最后一条 changelog 不是近期的",
                        f"last at={at!r}；field={last.get('field')!r} —— 请为本次变更补一条记录（by/from/to/reason/post）",
                    )
                )
            else:
                checks.append(
                    (
                        "PASS",
                        "契约变更已记录",
                        f"by={last.get('by')!r} field={last.get('field')!r} reason={str(last.get('reason'))[:40]!r}",
                    )
                )
            missing_keys = [k for k in ("by", "field", "reason") if not last.get(k)]
            if missing_keys:
                checks.append(
                    ("WARNING", "changelog 条目字段不全", f"缺 {missing_keys}（by/field/reason 至少要齐）")
                )

    icon = {"PASS": "✅", "WARNING": "⚠️ ", "BLOCKING": "⛔"}
    width = max(len(c[1]) for c in checks) + 2
    for level, name, detail in checks:
        print(f"{icon[level]} {level.ljust(8)} {name.ljust(width)} {detail}")

    blocking = [c for c in checks if c[0] == "BLOCKING"]
    print(f"\n{'ALLOW ✅' if not blocking else 'BLOCK ⛔'}  BLOCKING={len(blocking)}")
    return 0 if not blocking else 1


if __name__ == "__main__":
    raise SystemExit(main())
