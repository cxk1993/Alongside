#!/usr/bin/env python
"""部署前守门（predeploy guard）—— 把「部署前必须核对工作区」从纪律变成机读约束。

★ 由来（2026-09-23 真实事故）：重建 dist 上线 push 时，工作区混有**他人在制、未门禁**的
  改动，被一并带上生产。裁定「疏漏成立、下不为例」，并**新增组规**：
  「重建前必须 `git status` 核对工作区无他人在制，或用 `git stash` / 单独 checkout 构建」。
  —— 纪律靠人记就会再犯（同日内我在部署流程上踩了两次坑），故本工具把它变成跑一次就知道。

★ 设计参照（外部，讨论第二轮已引）：
  - winget-pkgs 的「Pre-Submission 本地校验」+「PR Scope：每次 PR 只改一个 manifest set」
  - ext-preflight 的 blocking / warning 两档报告

★ 与 N4-A 的联动（承 astrbot《讨论第三轮》）：
  「装插件 = 重建前端」会让 dist 从"偶发变更"变成"每次装插件都变更" → 今天的事故有
  **常规化**风险。故本工具提供 `--plugin-collect <id>` 模式：**只允许该插件相关路径变更**，
  其余一律 BLOCKING —— 它是 N4-A 这条路的**安全阀**。

用法：
    python tools/predeploy_guard.py --scope apps/web services/api
    python tools/predeploy_guard.py --plugin-collect countdown
    python tools/predeploy_guard.py --scope . --json out.json      # 只查构建新鲜度/契约
    python tools/predeploy_guard.py --scope apps/web --contract    # 附加契约一致性比对

退出码：0 = 无 BLOCKING（可部署）；1 = 有 BLOCKING（不许部署）。
零第三方依赖（subprocess + json + pathlib）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRY_RE = __import__("re").compile(r"assets/(index-[\w-]+\.js)")


def _find_git() -> str | None:
    """找 git：GIT_EXE 环境变量 → PATH → 常见 PortableGit 位置（本机 WorkBuddy 环境）。"""
    env = os.environ.get("GIT_EXE")
    if env and Path(env).is_file():
        return env
    found = shutil.which("git")
    if found:
        return found
    for base in (
        Path.home() / ".workbuddy" / "binaries" / "PortableGit",
        Path("C:/Program Files/Git"),
    ):
        if base.is_dir():
            for g in sorted(base.glob("**/cmd/git.exe")):
                return str(g)
    return None


def _git(git: str, *args: str) -> str:
    r = subprocess.run(  # noqa: S603 — 只跑固定子命令，无 shell
        [git, *args], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return r.stdout


def changed_paths(git: str) -> list[tuple[str, str]]:
    """[(状态码, 相对路径)]，来自 git status --porcelain（含未跟踪）。"""
    items: list[tuple[str, str]] = []
    for line in _git(git, "status", "--porcelain").splitlines():
        if len(line) < 4:
            continue
        code = line[:2].strip() or "??"
        path = line[3:].strip()
        if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
            path = path[1:-1]
        items.append((code, path))
    return items


def norm(p: str) -> str:
    return p.replace("\\", "/").lstrip("./")


def in_scope(path: str, scopes: list[str]) -> bool:
    sp = norm(path)
    for s in scopes:
        ss = norm(s)
        if ss in ("", ".") or sp == ss or sp.startswith(ss + "/"):
            return True
    return False


def check_worktree(git: str | None, scopes: list[str]) -> tuple[str, str, str]:
    if git is None:
        return ("WARNING", "git 不可用", "未找到 git → 工作区检查被跳过（可用 GIT_EXE 指定）")
    dirty = changed_paths(git)
    if not dirty:
        return ("PASS", "工作区纯净", "无任何变更")
    outside = [(c, p) for c, p in dirty if not in_scope(p, scopes)]
    if not outside:
        return ("PASS", "变更均在 scope 内", f"{len(dirty)} 项，全部属于本单")
    show = "; ".join(f"{c} {p}" for c, p in outside[:8])
    more = f"（另 {len(outside) - 8} 项）" if len(outside) > 8 else ""
    return (
        "BLOCKING",
        f"scope 外有 {len(outside)} 项变更",
        f"{show}{more}   ← 与 2026-09-23 dist 事故同型：别把他人在制带上生产",
    )


def check_dist_freshness(
    dist_dir: Path | None = None, src_dirs: tuple[Path, ...] | None = None
) -> tuple[str, str, str]:
    """dist 构建时间 vs 源码最新提交时间（前端改了没重建 = 上线的是旧包）。

    ★ 2026-11-24（汐瑶）：加 `dist_dir` / `src_dirs` —— 为让 selfcheck 能在
      临时目录里构造「旧 / 新 / 缺失」三种现场，不碰真实 dist。
    """
    dist_index = (dist_dir or (REPO_ROOT / "apps" / "web" / "dist")) / "index.html"
    if not dist_index.is_file():
        try:
            shown = str(dist_index.relative_to(REPO_ROOT))
        except ValueError:
            shown = str(dist_index)
        return ("WARNING", "dist 不存在", f"未找到 {shown}（跳过）")
    dist_mtime = dist_index.stat().st_mtime
    newest_src = 0.0
    newest_file = ""
    dirs = src_dirs if src_dirs is not None else (
        REPO_ROOT / "apps" / "web" / "src",
        REPO_ROOT / "apps" / "web" / "public",
    )
    for d in dirs:
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.is_file() and f.suffix in {".ts", ".tsx", ".css", ".json", ".js", ".html"}:
                m = f.stat().st_mtime
                if m > newest_src:
                    newest_src = m
                    try:
                        newest_file = str(f.relative_to(REPO_ROOT))
                    except ValueError:
                        newest_file = str(f)
    if newest_src > dist_mtime:
        return (
            "BLOCKING",
            "dist 比源码旧",
            f"源码最新 {newest_file} 晚于 dist → 必须重新 build（否则上线的是旧包）",
        )
    return ("PASS", "dist 新鲜", f"dist 不落后于源码（entry={_entry_hash(dist_index) or '?'}）")


def _entry_hash(index_html: Path) -> str:
    """提取入口**核心 hash**（如 `Cg4ao9Lg`）——与 expected.json 的 entry_hash 同形。

    ★ 必须归一化：index.html 里是全名 `index-Cg4ao9Lg.js`，而 expected.json 存的是
      核心哈希 `Cg4ao9Lg`。首版没归一化，工具第一次跑就把"其实一致"误判为不一致。
    """
    m = ENTRY_RE.search(index_html.read_text(encoding="utf-8", errors="ignore"))
    if not m:
        return ""
    return m.group(1).removeprefix("index-").removesuffix(".js")


def check_contract() -> tuple[str, str, str]:
    """expected.json 的 hash 段 vs 本地 dist 入口（契约一致性，WARNING 级）。"""
    exp = REPO_ROOT / "tools" / "accept_probe" / "expected.json"
    dist_index = REPO_ROOT / "apps" / "web" / "dist" / "index.html"
    if not exp.is_file() or not dist_index.is_file():
        return ("WARNING", "契约比对跳过", "expected.json 或 dist/index.html 不存在")
    try:
        data = json.loads(exp.read_text(encoding="utf-8"))
        want = data.get("suites", {}).get("hash", {}).get("entry_hash", "")
    except Exception as e:  # noqa: BLE001
        return ("WARNING", "契约解析失败", str(e)[:80])
    local = _entry_hash(dist_index)
    if want and local and want != local:
        return (
            "WARNING",
            "契约 hash 与本地 dist 不一致",
            f"expected.json={want} 本地 dist={local} → 若本单改前端，部署后需同步契约（先回帖再改）",
        )
    return ("PASS", "契约 hash 一致", f"{want or local}")



def selfcheck() -> int:
    """反向自证：六种现场，该红的必须红、不该红的不能误红。

    ★ 2026-11-24（汐瑶）：这道门禁守着「别把他人在制改动一起发上生产」，
      它自己坏了就等于没有。两种失效都钉：
      ① 瞎了 —— scope 外有改动也放行；② 误红 —— 干净工作区报 BLOCKING，
      会逼人加 `--scope .` 绕过（那就是门禁被关掉）。

    全部在临时目录 / monkeypatch 上跑，不碰真实仓库、不跑真 git。
    """
    import tempfile
    import time

    checks: list[tuple[str, bool]] = []

    # ── A. dist 新鲜度 ────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        dist = root / "dist"
        dist.mkdir()
        src = root / "src"
        src.mkdir()
        (src / "a.tsx").write_text("x", encoding="utf-8")
        (dist / "index.html").write_text(
            '<script src="assets/index-ABC123.js"></script>', encoding="utf-8"
        )
        now = time.time()
        os.utime(src / "a.tsx", (now - 100, now - 100))
        os.utime(dist / "index.html", (now, now))
        checks.append(
            ("A0 基线：dist 比源码新 ⇒ PASS", check_dist_freshness(dist, (src,))[0] == "PASS")
        )

        os.utime(src / "a.tsx", (now + 100, now + 100))
        level = check_dist_freshness(dist, (src,))[0]
        checks.append(("A1 dist 比源码旧 ⇒ 必须 BLOCKING", level == "BLOCKING"))

        level = check_dist_freshness(root / "nope", (src,))[0]
        checks.append(
            ("A2 dist 不存在 ⇒ WARNING（不是 BLOCKING —— 可能还没 build）", level == "WARNING")
        )

    # ── B. 工作区范围 ─────────────────────────────────────
    real = globals().get("changed_paths")
    try:
        globals()["changed_paths"] = lambda _git: []
        checks.append(
            ("B0 工作区纯净 ⇒ PASS", check_worktree("git", ["apps/web"])[0] == "PASS")
        )

        globals()["changed_paths"] = lambda _git: [("M", "apps/web/src/a.tsx")]
        checks.append(
            ("B1 变更全在 scope 内 ⇒ PASS", check_worktree("git", ["apps/web"])[0] == "PASS")
        )

        globals()["changed_paths"] = lambda _git: [("M", "services/api/x.py")]
        checks.append(
            (
                "B2 scope 外有改动 ⇒ 必须 BLOCKING（与 2026-09-23 dist 事故同型）",
                check_worktree("git", ["apps/web"])[0] == "BLOCKING",
            )
        )

        globals()["changed_paths"] = lambda _git: [
            ("M", "apps/web/src/a.tsx"),
            ("M", "services/api/x.py"),
        ]
        checks.append(
            (
                "B3 混着一个 scope 外的也必须 BLOCKING（不许被「大部分在 scope 内」稀释）",
                check_worktree("git", ["apps/web"])[0] == "BLOCKING",
            )
        )
    finally:
        if real is not None:
            globals()["changed_paths"] = real

    checks.append(
        ("B4 git 不可用 ⇒ WARNING（不是静默通过）", check_worktree(None, ["apps/web"])[0] == "WARNING")
    )

    print("predeploy-guard 门禁自检（反向验证）")
    ok = True
    for name, passed in checks:
        print(f"  {'✓' if passed else '✗'} {name}")
        ok = ok and passed
    print()
    print("结论：" + ("✓ 自检通过 —— 该红的红、不该红的不误红" if ok else "✗ 自检失败 —— 门禁本身坏了"))
    return 0 if ok else 1


def main() -> int:
    if "--selfcheck" in sys.argv[1:]:
        return selfcheck()
    ap = argparse.ArgumentParser(description="部署前守门（BLOCKING / WARNING 两档）")
    ap.add_argument("--scope", nargs="*", default=[], help="本单允许变更的路径前缀（默认：任意）")
    ap.add_argument("--plugin-collect", metavar="PLUGIN_ID", default="",
                    help="N4-A 模式：只允许该插件相关路径变更，其余 BLOCKING")
    ap.add_argument("--contract", action="store_true", help="附加契约一致性比对")
    ap.add_argument("--json", default="", help="结果落盘路径")
    args = ap.parse_args()

    scopes = list(args.scope) or ["."]
    if args.plugin_collect:
        pid = args.plugin_collect
        scopes = [f"apps/web/src/apps/{pid}", f"plugins/{pid}", f"services/api/modules/{pid}"]
        print(f"[plugin-collect 模式] 本单允许变更：{scopes}\n")

    git = _find_git()
    checks: list[tuple[str, str, str]] = [
        check_worktree(git, scopes),
        check_dist_freshness(),
    ]
    if args.contract:
        checks.append(check_contract())

    icon = {"PASS": "✅", "WARNING": "⚠️ ", "BLOCKING": "⛔"}
    width = max(len(c[1]) for c in checks) + 2
    for level, name, detail in checks:
        print(f"{icon[level]} {level.ljust(8)} {name.ljust(width)} {detail}")

    blocking = [c for c in checks if c[0] == "BLOCKING"]
    warns = [c for c in checks if c[0] == "WARNING"]
    verdict = "ALLOW ✅（可部署）" if not blocking else "BLOCK ⛔（不许部署）"
    print(f"\n{verdict}  BLOCKING={len(blocking)}  WARNING={len(warns)}  scope={scopes}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "verdict": "allow" if not blocking else "block",
                    "scopes": scopes,
                    "checks": [{"level": lv, "name": n, "detail": d} for lv, n, d in checks],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"结果已落盘：{args.json}")

    return 0 if not blocking else 1


if __name__ == "__main__":
    raise SystemExit(main())
