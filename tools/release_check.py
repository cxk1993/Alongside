#!/usr/bin/env python
"""Alongside 初版发布验收 · 一键串联「这版能不能发」。

一条命令跑完全部发布判据，每项打印 ✓/✗（外带一行证据），最后给总结论与退出码：
    0 = 可以发（所有阻塞项通过）
    1 = 不能发（至少一项阻塞项红）

用法：
    python tools/release_check.py                 # 全跑（默认）
    python tools/release_check.py --list          # 只看会跑哪些项
    python tools/release_check.py --only pytest --only frontend-tsc
    python tools/release_check.py --json          # 机读输出（便于 CI）
    python tools/release_check.py --timeout 1200  # 单步超时（秒，默认见下）

判据分两类：
    · 阻塞（blocking）—— 红了就是不能发，退出码 1。
    · 仅报告（report）—— 红/黄只提示，不影响结论（例如前端内核纯度这批已知债务）。

设计纪律：
    ★ 所有子进程**严格串行**执行（本机内存紧张，绝不并发）。
    ★ 只读：本脚本不写仓库任何文件（临时脚本与临时库都落在系统临时目录）。
    ★ 不依赖仓库外的路径；数据作用域三态实测在这里自带一份独立实现。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# 路径与解释器
# ─────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent          # 仓库根（tools/ 的上一级）
API_DIR = ROOT / "services" / "api"
WEB_DIR = ROOT / "apps" / "web"
PACK_DIR = ROOT / "packs" / "official.life-tasks"


def _pick_python() -> str:
    """优先用后端自带的 .venv（依赖都装在那里），否则退回当前解释器。"""
    for cand in (
        API_DIR / ".venv" / "Scripts" / "python.exe",   # Windows
        API_DIR / ".venv" / "bin" / "python",           # POSIX
    ):
        if cand.exists():
            return str(cand)
    return sys.executable


PY = _pick_python()
NODE = shutil.which("node") or "node"
TSC = WEB_DIR / "node_modules" / "typescript" / "bin" / "tsc"
# ★ 走 dist/cli.js 而不是 vitest.mjs：本机 node_modules/vitest 的 package.json 声明
#   bin 指向 ./vitest.mjs，但该文件**实际不存在**（只装了 dist/ 与 index.cjs），
#   用 npx 能跑是因为 .bin/vitest 是另一条 sh 包装。直连 dist/cli.js 最稳。
VITEST = WEB_DIR / "node_modules" / "vitest" / "dist" / "cli.js"

DEFAULT_TIMEOUT = 1800          # 单步默认超时（秒）——全量 pytest 最慢
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


# ─────────────────────────────────────────────────────────────
# 结果模型
# ─────────────────────────────────────────────────────────────

@dataclass
class Result:
    ok: bool
    evidence: str                       # 一行证据
    detail: list[str] = field(default_factory=list)   # 失败时的尾部日志（可选）


def _tail(text: str, n: int = 8) -> list[str]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return lines[-n:]


def _strip_ansi(text: str) -> str:
    """剥掉 ANSI 颜色/控制码。

    ★ 必须剥：vitest 在**非 TTY** 下仍然会给汇总行上色（`\\x1b[32m67 passed\\x1b[39m`），
      于是 `^\\s*Test Files\\s+` 这种锚定行首的正则全部匹配不到 ——
      表现为「明明 509 passed，脚本却报 ? 个文件 / 0 passed」。
      这是今天踩到的第二个「输出明明对、解析判红」的坑（第一个是 vitest.mjs 缺失）。
    """
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def run(
    cmd: list[str], cwd: Path, timeout: int, env: dict[str, str] | None = None
) -> tuple[int, str, float]:
    """跑一个子进程，合并 stdout/stderr，返回 (退出码, 输出, 耗时秒)。输出已剥 ANSI。"""
    t0 = time.time()
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env or CHILD_ENV,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        out = _strip_ansi(p.stdout.decode("utf-8", errors="replace"))
        return p.returncode, out, time.time() - t0
    except subprocess.TimeoutExpired as exc:
        got = _strip_ansi((exc.stdout or b"").decode("utf-8", errors="replace"))
        return 124, got + f"\n[timeout] 超过 {timeout}s 被终止", time.time() - t0
    except FileNotFoundError as exc:
        return 127, f"[无法执行] {exc}", time.time() - t0


# ─────────────────────────────────────────────────────────────
# 各项判据
# ─────────────────────────────────────────────────────────────

def check_worktree_clean(timeout: int) -> Result:
    """工作区干净度——**仅报告**，但很重要。

    「能跑」的前提是「跑的就是要发的那份代码」。多会话并行时工作区一直在变，
    红了要先分清是「代码真坏了」还是「别人正改到一半」。
    脏工作区下的结论只对当前工作区成立，**不对 HEAD 成立**。
    """
    code, out, _ = run(["git", "status", "--porcelain"], ROOT, timeout)
    if code != 0:
        return Result(False, f"git status 失败 exit={code}")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    dirty = [ln for ln in lines if not ln.startswith("??")]
    untracked = [ln for ln in lines if ln.startswith("??")]
    if not lines:
        return Result(True, "工作区干净（被测对象 == HEAD）")
    names = ", ".join(ln[3:].strip() for ln in (dirty or untracked)[:4])
    more = "" if len(dirty) + len(untracked) <= 4 else " …"
    evidence = (
        f"工作区不干净：{len(dirty)} 改动 + {len(untracked)} 未跟踪 ⇒ "
        f"结论只对当前工作区成立（{names}{more}）"
    )
    return Result(False, evidence)


def check_pytest(timeout: int) -> Result:
    code, out, _ = run([PY, "-m", "pytest", "tests", "tests_protocol", "tests_packs"],
                       API_DIR, timeout)
    m_pass = re.findall(r"(\d+) passed", out)
    m_fail = re.findall(r"(\d+) failed", out)
    m_err = re.findall(r"(\d+) error", out)
    m_skip = re.findall(r"(\d+) skipped", out)
    passed = int(m_pass[-1]) if m_pass else 0
    failed = int(m_fail[-1]) if m_fail else 0
    errors = int(m_err[-1]) if m_err else 0
    skipped = int(m_skip[-1]) if m_skip else 0
    ev = f"{passed} passed / {failed} failed / {errors} error / {skipped} skipped"
    ok = code == 0 and failed == 0 and errors == 0
    # 失败时优先贴「哪条用例红了」，比贴 warnings 有用得多
    bad_lines = [
        ln.strip() for ln in out.splitlines()
        if ln.strip().startswith(("FAILED ", "ERROR "))
    ]
    detail = bad_lines[:10] or (_tail(out) if not ok else [])
    return Result(ok, ev, detail)


def check_purity_backend(timeout: int) -> Result:
    code, out, _ = run([PY, "scripts/check_kernel_purity.py", "--scope", "backend"],
                       API_DIR, timeout)
    ok = code == 0
    m = re.search(r"发现 (\d+) 处", out)
    if ok and "内核干净" in out:
        ev = "内核干净（backend 0 违规）"
    elif m:
        ev = f"backend 出现 {m.group(1)} 处业务词汇"
    else:
        ev = f"exit={code}"
    return Result(ok, ev, _tail(out) if not ok else [])


def check_purity_frontend(timeout: int) -> Result:
    """前端内核纯度——**仅报告**：这批业务词是已知债务（C1），不阻塞初版。"""
    code, out, _ = run([PY, "scripts/check_kernel_purity.py", "--scope", "frontend", "--warn-only"],
                       API_DIR, timeout)
    m = re.search(r"发现 (\d+) 处", out)
    if "内核干净" in out:
        ev = "内核干净（frontend 0 违规）"
        return Result(True, ev)
    if m:
        return Result(False, f"frontend 有 {m.group(1)} 处业务词汇（已知债务，仅报告）")
    return Result(code == 0, f"exit={code}（仅报告）")


def check_protocol_consumption(timeout: int) -> Result:
    code, out, _ = run([PY, "scripts/check_protocol_consumption.py"], API_DIR, timeout)
    ok = code == 0 and "登记表与扫描结果一致" in out
    m = re.search(r"被消费 (\d+) · 仅格式 (\d+) · 未消费 (\d+)", out)
    ev = (f"36 字段：被消费 {m.group(1)} / 仅格式 {m.group(2)} / 未消费 {m.group(3)}"
          if m else f"exit={code}")
    if ok:
        ev += "（登记表自洽）"
    return Result(ok, ev, _tail(out) if not ok else [])


def check_pack(timeout: int) -> Result:
    code, out, _ = run([PY, "scripts/pack.py", "check", "../../packs/official.life-tasks"],
                       API_DIR, timeout)
    ok = code == 0
    ev = "official.life-tasks 通过（无问题）" if ok else f"包检查失败 exit={code}"
    return Result(ok, ev, _tail(out) if not ok else [])


def check_db_scope_wiring(timeout: int) -> Result:
    code, out, _ = run([PY, "tools/gates/verify_db_scope_wiring.py"], ROOT, timeout)
    ok = code == 0
    n_pass = len(re.findall(r"✓ W\d", out))
    n_fail = len(re.findall(r"✗ W\d", out))
    ev = f"G16 运行时判据 {n_pass} 项通过 / {n_fail} 项失败"
    return Result(ok, ev, _tail(out) if not ok else [])


def check_gate_registry(timeout: int) -> Result:
    code, out, _ = run([PY, "tools/gate_registry.py"], ROOT, timeout)
    ok = code == 0 and "清单自洽" in out
    m = re.search(r"登记 (\d+) 个", out)
    ev = f"门禁登记 {m.group(1)} 个，清单自洽" if (ok and m) else f"exit={code}"
    return Result(ok, ev, _tail(out) if not ok else [])


# —— 数据作用域三态实测：自带一份独立实现（不依赖仓库外脚本）——
_SCOPE_PROBE = r'''
import os, sys, tempfile
tmpdir = tempfile.mkdtemp(prefix="scope_check_")
os.environ["DB_PATH"] = os.path.join(tmpdir, "check.db")
sys.path.insert(0, ".")
from db.engine import get_engine, init_engine
init_engine()
import db.models  # noqa
from modules.finance.models import FinanceEntry  # noqa
from modules.todo.models import TodoItem  # noqa
from sqlmodel import Session, SQLModel
from sqlalchemy import text
SQLModel.metadata.create_all(get_engine())
from core.deps import db_session, kernel_session
results = []

def check(name, fn, expect_ok):
    try:
        fn(); got = "OK"
    except Exception as exc:
        msg = str(exc).lower()
        got = "DENIED" if ("prohibited" in msg or "not authorized" in msg) else \
              f"ERROR({type(exc).__name__}:{str(exc)[:40]})"
    want = "OK" if expect_ok else "DENIED"
    passed = got.startswith(want)
    results.append((name, got, want, passed))
    print(f"{'OK ' if passed else 'BAD'} {name:<52} got={got:<24} want={want}")

def _kernel_read():
    with kernel_session() as s: s.exec(text("SELECT count(*) FROM todo_item"))
def _kernel_write():
    with kernel_session() as s:
        s.exec(text("INSERT INTO app_setting (id, created_at, updated_at, key, value_json) "
                    "VALUES ('scope-check','2026-01-01','2026-01-01',"
                    "'scope-check','null')"))
        s.commit()
def _todo_read_own():
    with db_session("todo") as s: s.exec(text("SELECT count(*) FROM todo_item"))
def _todo_write_own():
    with db_session("todo") as s:
        s.exec(text("INSERT INTO todo_item (id, created_at, updated_at, text, done, "
                    "origin, sort, instance_no) "
                    "VALUES ('scope-check-2','2026-01-01','2026-01-01','x',0,'human',0,0)"))
        s.commit()
def _todo_read_other():
    with db_session("todo") as s: s.exec(text("SELECT count(*) FROM finance_entry"))
def _todo_read_kernel_table():
    with db_session("todo") as s: s.exec(text("SELECT count(*) FROM plugin_state"))
def _bare_read():
    with Session(get_engine()) as s: s.exec(text("SELECT count(*) FROM todo_item"))

check("kernel_session 读 todo_item", _kernel_read, True)
check("kernel_session 写 app_setting", _kernel_write, True)
check("db_session(todo) 读 todo_item", _todo_read_own, True)
check("db_session(todo) 写 todo_item", _todo_write_own, True)
check("db_session(todo) 读 finance_entry", _todo_read_other, False)
check("db_session(todo) 读 plugin_state", _todo_read_kernel_table, False)
check("裸 Session 读 todo_item", _bare_read, False)
bad = [r for r in results if not r[3]]
print(f"结果：{len(results)-len(bad)}/{len(results)} 通过")
sys.exit(1 if bad else 0)
'''


def _probe_env() -> dict[str, str]:
    """三态探针需要的环境。

    它要 `init_engine()`，而 `Settings` 的三个安全字段必填、值来自仓库根 `.env`。
    干净 clone 上 `.env` 不存在（只在 `.env.example` 里）⇒ 这里补一份**假值**，
    否则报错会是「pydantic 缺字段」，看不出跟数据边界有什么关系。
    真 `.env` 存在时**不动**：用真配置，探针才和线上同一条路径。
    """
    env = dict(CHILD_ENV)
    if not (ROOT / ".env").exists():
        env.setdefault("SECRET_KEY", "0" * 64)
        env.setdefault("ADMIN_PASSWORD_HASH", "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$" + "0" * 43)
        env.setdefault("TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    return env


def check_scope_tristate(timeout: int) -> Result:
    code, out, _ = run([PY, "-c", _SCOPE_PROBE], API_DIR, timeout, env=_probe_env())
    m = re.search(r"结果：(\d+)/(\d+) 通过", out)
    ok = code == 0 and bool(m) and m.group(1) == m.group(2)
    ev = (f"三态 {m.group(1)}/{m.group(2)} 通过（kernel 放行 / 插件越界 DENIED / 未声明 DENIED）"
          if m else f"未取到结果，exit={code}")
    return Result(ok, ev, _tail(out) if not ok else [])


def check_frontend_tsc(timeout: int) -> Result:
    if not TSC.exists():
        return Result(False, f"找不到 tsc：{TSC}")
    code, out, _ = run([NODE, str(TSC), "--noEmit"], WEB_DIR, timeout)
    ok = code == 0
    n_err = len(re.findall(r"error TS\d+", out))
    ev = "tsc --noEmit 无类型错误" if ok else f"tsc 报 {n_err or '?'} 处类型错误"
    return Result(ok, ev, _tail(out) if not ok else [])


def check_frontend_vitest(timeout: int) -> Result:
    if not VITEST.exists():
        return Result(False, f"找不到 vitest：{VITEST}")
    cmd = [NODE, str(VITEST), "run", "--poolOptions.forks.singleFork=true"]
    code, out, _ = run(cmd, WEB_DIR, timeout)

    def _pick(label: str) -> str:
        rows = re.findall(rf"(?m)^\s*{label}\s+(.+)$", out)
        return rows[-1] if rows else ""

    def _num(text: str, word: str) -> int:
        m = re.search(rf"(\d+)\s+{word}", text)
        return int(m.group(1)) if m else 0

    tests_line = _pick("Tests")
    files_line = _pick("Test Files")
    passed = _num(tests_line, "passed")
    failed = _num(tests_line, "failed")
    m_total = re.search(r"\((\d+)\)", files_line)
    files = m_total.group(1) if m_total else "?"
    ok = code == 0 and failed == 0 and passed > 0
    ev = f"单进程 vitest：{files} 个文件 / {passed} passed / {failed} failed"
    if code != 0 and not tests_line:
        ev = f"vitest 异常退出 exit={code}（未见汇总行）"
    return Result(ok, ev, _tail(out) if not ok else [])


# ─────────────────────────────────────────────────────────────
# 登记表
# ─────────────────────────────────────────────────────────────

@dataclass
class Step:
    id: str
    title: str
    fn: Callable[[int], Result]
    blocking: bool = True


STEPS: list[Step] = [
    Step(
        "worktree",
        "工作区干净度（被测对象是否 == HEAD，仅报告）",
        check_worktree_clean,
        blocking=False,
    ),
    Step("pytest", "后端全量 pytest（tests + tests_protocol + tests_packs）", check_pytest),
    Step("purity-backend", "内核纯度 · backend（阻塞）", check_purity_backend),
    Step(
        "purity-frontend",
        "内核纯度 · frontend --warn-only（仅报告）",
        check_purity_frontend,
        blocking=False,
    ),
    Step("protocol", "协议字段消费门禁 G13", check_protocol_consumption),
    Step("pack", "包层校验 pack.py check official.life-tasks", check_pack),
    Step("db-scope-wiring", "数据作用域接线门禁 G16（运行时判据）", check_db_scope_wiring),
    Step("gate-registry", "门禁清单自洽 gate_registry", check_gate_registry),
    Step("scope-tristate", "数据作用域三态实测（kernel/插件/未声明 7 项）", check_scope_tristate),
    Step("frontend-tsc", "前端 npx tsc --noEmit", check_frontend_tsc),
    Step("frontend-vitest", "前端单进程 vitest（singleFork）", check_frontend_vitest),
]

# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def _git_head() -> str:
    code, out, _ = run(["git", "log", "--oneline", "-1"], ROOT, 30)
    return out.strip() if code == 0 else "?"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Alongside 初版发布验收（一条命令回答「能不能发」）")
    ap.add_argument("--list", action="store_true", help="只列出会跑哪些项")
    ap.add_argument(
        "--only", action="append", default=None, metavar="ID", help="只跑指定项（可重复）"
    )
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help=f"单步超时秒数（默认 {DEFAULT_TIMEOUT}）",
    )
    args = ap.parse_args(argv)

    steps = STEPS
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {s.id for s in STEPS}
        if unknown:
            print(f"未知的项：{sorted(unknown)}；可用：{[s.id for s in STEPS]}")
            return 2
        steps = [s for s in STEPS if s.id in wanted]

    if args.list:
        for s in steps:
            tag = "阻塞" if s.blocking else "仅报告"
            print(f"  {s.id:<18} [{tag}] {s.title}")
        return 0

    banner = [
        "Alongside 初版发布验收 · release_check",
        f"repo  : {ROOT}",
        f"HEAD  : {_git_head()}",
        f"python: {PY}",
        f"时间  : {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "纪律  : 子进程严格串行（内存紧张，不并发）",
        "",
    ]

    if not args.json:
        print("\n".join(banner), flush=True)

    records: list[dict] = []
    total = len(steps)
    for i, step in enumerate(steps, 1):
        if not args.json:
            print(f"[{i}/{total}] {step.title}", flush=True)
        try:
            res = step.fn(args.timeout)
        except Exception as exc:            # 判据自身崩了，也要算「不能发」
            res = Result(False, f"判据自身异常：{type(exc).__name__}: {exc}")

        mark = "✓" if res.ok else ("✗" if step.blocking else "△")

        if not args.json:
            print(f"      {mark} {res.evidence}", flush=True)
            for line in res.detail:
                print(f"        {line}", flush=True)

        records.append({
            "id": step.id,
            "title": step.title,
            "blocking": step.blocking,
            "ok": res.ok,
            "mark": mark,
            "evidence": res.evidence,
            "detail": res.detail,
        })

    # 汇总
    blocking_fail = [r for r in records if r["blocking"] and not r["ok"]]
    report_fail = [r for r in records if not r["blocking"] and not r["ok"]]
    blocking_total = len([r for r in records if r["blocking"]])
    can_ship = not blocking_fail
    exit_code = 0 if can_ship else 1

    if args.json:
        print(json.dumps({
            "can_ship": can_ship,
            "exit_code": exit_code,
            "head": _git_head(),
            "steps": records,
        }, ensure_ascii=False, indent=2))
        return exit_code

    print("\n" + "─" * 60, flush=True)
    if can_ship:
        print(
            f"结论：✓ 可以发 —— {blocking_total} 项阻塞判据全部通过（共跑 {total} 项）",
            flush=True,
        )
    else:
        print(f"结论：✗ 不能发 —— {len(blocking_fail)}/{blocking_total} 项阻塞判据失败", flush=True)
        for r in blocking_fail:
            print(f"  · [{r['id']}] {r['title']}", flush=True)
            print(f"      → {r['evidence']}", flush=True)
    if report_fail:
        print("仅报告项（不阻塞结论）：", flush=True)
        for r in report_fail:
            print(f"  △ [{r['id']}] {r['evidence']}", flush=True)
    print(f"EXIT={exit_code}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
