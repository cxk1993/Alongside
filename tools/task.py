#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Alongside 跨平台任务入口（项目唯一入口的真实实现）。

为什么不是纯 Makefile？
    总纲 §4 雷区 #1/#2 说得清楚：不许假设目标机器有 make / grep / find / wmic。
    主人这台 Win11 没有 make。所以真正的实现放在这个跨平台脚本里，
    根目录的 Makefile 只是它的薄壳 —— 两条入口行为完全一致。

用法（在项目根目录执行）：
    python tools/task.py help
    python tools/task.py setup        # 首次：建 venv + 装前后端依赖
    python tools/task.py dev          # 同起前端 5173 与后端 8000
    python tools/task.py lint         # tsc + eslint + prettier / ruff + mypy
    python tools/task.py test         # vitest + pytest
    python tools/task.py check --paths apps/web/src/apps/docs services/api/modules/docs
                                      # 按卡自检（XA）：只跑 --paths 涉及的那组检查
    python tools/task.py verify       # lint + test + 前端构建（交付前必跑）
    python tools/task.py build        # 前端产物（有 docker 时顺带建镜像）
    python tools/task.py clean        # 清缓存，data/ 绝不动
    python tools/task.py new-plugin --id calendar --name "日程表"

环境变量：
    ALONGSIDE_PYTHON    指定用于建 venv 的 Python 3.11 可执行文件
    PIP_INDEX_URL    pip 源（国内建议 https://pypi.tuna.tsinghua.edu.cn/simple）
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

if sys.version_info < (3, 11):  # tomllib 需要 3.11+
    print("tools/task.py 需要 Python 3.11+（用于读取 pyproject.toml）", file=sys.stderr)
    raise SystemExit(1)

import tomllib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "web"
API = ROOT / "services" / "api"
VENV = API / ".venv"

WEB_PORT = 5173
API_PORT = 8000
IS_WIN = os.name == "nt"


# ────────────────────────────── 基础设施 ──────────────────────────────
def log(msg: str) -> None:
    print(f"[task] {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"[task] 失败：{msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def run(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    shown = " ".join(cmd)
    log(f"$ {shown}")
    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and p.returncode != 0:
        die(f"命令退出码 {p.returncode}：{shown}")
    return p


def find_python311() -> list[str]:
    """找一个 Python 3.11。总纲 §1.1 把后端锁在 3.11，所以这里严格校验。"""
    cands: list[list[str]] = []
    forced = (os.environ.get("ALONGSIDE_PYTHON") or os.environ.get("LIFEOS_PYTHON"))
    if forced:
        cands.append([forced])
    cands += [["py", "-3.11"], ["python3.11"], ["python3"], ["python"], [sys.executable]]

    probe = "import sys;print('%d.%d' % sys.version_info[:2])"
    tried: list[str] = []
    for c in cands:
        exe = c[0]
        if not (Path(exe).exists() or shutil.which(exe)):
            continue
        try:
            p = subprocess.run(
                [*c, "-c", probe], capture_output=True, text=True, timeout=30
            )
        except Exception:  # noqa: BLE001
            continue
        out = (p.stdout or "").strip()
        tried.append(f"{' '.join(c):<28} -> {out or (p.stderr or '').strip()[:50]}")
        if p.returncode == 0 and out == "3.11":
            return c
    die(
        "找不到 Python 3.11（总纲 §1.1 锁定 3.11）。已尝试：\n    "
        + "\n    ".join(tried)
        + "\n  解决：设环境变量 ALONGSIDE_PYTHON 指向 3.11 的可执行文件。"
    )
    return []  # 不会走到


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


def npm_cmd(args: list[str]) -> list[str]:
    """Windows 上 npm 是 .cmd 批处理，必须经 cmd /c 才能被 subprocess 拉起。"""
    if IS_WIN:
        return ["cmd", "/c", "npm", *args]
    return [shutil.which("npm") or "npm", *args]


def docker_available() -> bool:
    return shutil.which("docker") is not None


# ────────────────────────────── 依赖准备 ──────────────────────────────
def api_dependencies() -> list[str]:
    """从 pyproject.toml 读依赖 —— 单一事实来源，不另建 requirements.txt。"""
    with (API / "pyproject.toml").open("rb") as f:
        data = tomllib.load(f)
    proj = data.get("project", {})
    deps: list[str] = list(proj.get("dependencies", []))
    deps += list(proj.get("optional-dependencies", {}).get("dev", []))
    return deps


def ensure_node() -> None:
    if not shutil.which("node"):
        die("找不到 node。请安装 Node 20+ 并加入 PATH（版本见 .nvmrc）。")


def ensure_venv() -> None:
    if venv_python().exists():
        return
    log("创建后端虚拟环境 services/api/.venv ...")
    run([*find_python311(), "-m", "venv", str(VENV)])


def ensure_api_deps() -> None:
    ensure_venv()
    py = str(venv_python())
    probe = subprocess.run([py, "-c", "import fastapi, sqlmodel"], capture_output=True)
    if probe.returncode == 0:
        return
    log("安装后端依赖（首次会慢一些）...")
    run([py, "-m", "pip", "install", "--upgrade", "pip", "-q"])
    run([py, "-m", "pip", "install", *api_dependencies()])


def ensure_web_deps() -> None:
    ensure_node()
    if (WEB / "node_modules").is_dir():
        return
    log("安装前端依赖（首次会慢一些）...")
    run(npm_cmd(["install"]), cwd=WEB)


def ensure_all() -> None:
    ensure_api_deps()
    ensure_web_deps()


def api_lint_targets() -> list[str]:
    """算出 mypy 该检查哪些路径。

    注意：不能只看"目录存不存在" —— 目录可能只有 .gitkeep 占位
    （core/ db/ modules/ 在对应任务块交付前就是这种状态）。
    一个目录里**一个 .py 都没有**时，mypy 会直接报错退出，
    所以这里必须按"有没有 Python 文件"来判断。
    """
    targets: list[str] = []
    for d in ("core", "db", "modules", "scripts"):
        p = API / d
        if p.is_dir() and any(p.rglob("*.py")):
            targets.append(d)
    for f in ("main.py",):
        if (API / f).exists():
            targets.append(f)
    return targets


# ────────────────────────────── 按卡自检（XA） ──────────────────────────────
def _rel_posix(p: Path, base: Path) -> str:
    return p.relative_to(base).as_posix()


def classify_paths(raw_paths: list[str]) -> tuple[list[str], list[str]]:
    """把 --paths 归一成（相对 apps/web 的路径, 相对 services/api 的路径）。

    同一批参数允许三种写法：相对项目根、相对 apps/web、相对 services/api。
    不存在或落在两端之外的路径记日志跳过 —— 脚手架阶段目录还没长齐是常态，
    自检工具不该因为"某端还没建"就拒绝服务另一端。
    """
    web: list[str] = []
    api: list[str] = []
    for raw in raw_paths:
        p = Path(raw.replace("\\", "/"))
        hit = next((b / p for b in (ROOT, WEB, API) if (b / p).exists()), None)
        if hit is None:
            log(f"跳过（路径不存在）：{raw}")
        elif WEB in hit.parents:
            web.append(_rel_posix(hit, WEB))
        elif API in hit.parents:
            api.append(_rel_posix(hit, API))
        else:
            log(f"跳过（apps/web 与 services/api 之外）：{raw}")
    return web, api


def node_bin(cands: list[str]) -> list[str]:
    """直接用 node 调包内 CLI —— npm run 会把参数追加到整仓命令后面，
    按路径过滤必须点名到 bin 传文件列表。"""
    for c in cands:
        if (WEB / c).exists():
            return ["node", c]
    die(f"apps/web 下找不到 {' 或 '.join(cands)}（先跑 python tools/task.py setup）")
    return []  # 不会走到


def _has_py(p: Path) -> bool:
    return (p.is_file() and p.suffix == ".py") or (
        p.is_dir() and any(p.rglob("*.py"))
    )


def related_py_tests(api_paths: list[str]) -> list[str]:
    """按路径关键词在 services/api/tests/ 里找相关测试文件。

    模块名（文件名去 .py，目录取末段）出现在测试文件名里即算相关，
    例：modules/finance/scheduler.py → test_finance_scheduler.py。
    找不到不报错，只记日志 —— 无测试本身就是要暴露的信息。
    """
    tests = API / "tests"
    if not tests.is_dir():
        return []
    kws: list[str] = []
    for p in api_paths:
        pp = Path(p)
        if pp.suffix == ".py":
            segs = [pp.stem] + ([pp.parts[-2]] if len(pp.parts) >= 2 else [])
        else:
            segs = [pp.parts[-1]]
        for kw in segs:
            if kw and kw not in kws:
                kws.append(kw)
    found: list[str] = []
    for f in sorted(tests.rglob("test_*.py")):
        stem = f.stem.lower()
        if any(k.lower() in stem for k in kws):
            rel = _rel_posix(f, API)
            if rel not in found:
                found.append(rel)
    return found


# ────────────────────────────── 各条命令 ──────────────────────────────
def cmd_setup(_: argparse.Namespace) -> None:
    ensure_all()
    log("环境就绪。下一步： python tools/task.py dev")


def _pump(name: str, proc: subprocess.Popen[str]) -> None:
    assert proc.stdout is not None
    for line in iter(proc.stdout.readline, ""):
        if not line:
            break
        print(f"[{name}] {line.rstrip()}", flush=True)


def cmd_dev(_: argparse.Namespace) -> None:
    ensure_all()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    specs = [
        ("web", npm_cmd(["run", "dev"]), WEB),
        (
            "api",
            [str(venv_python()), "-m", "uvicorn", "main:app", "--reload", "--port", str(API_PORT)],
            API,
        ),
    ]
    procs: list[tuple[str, subprocess.Popen[str]]] = []
    for name, cmd, cwd in specs:
        log(f"启动 {name}: {' '.join(cmd)}")
        procs.append(
            (
                name,
                subprocess.Popen(  # noqa: S603
                    cmd,
                    cwd=str(cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=env,
                ),
            )
        )

    for name, proc in procs:
        threading.Thread(target=_pump, args=(name, proc), daemon=True).start()

    log(f"前端 http://localhost:{WEB_PORT}    后端 http://localhost:{API_PORT}/docs")
    log("按 Ctrl+C 一起停止。")
    try:
        while any(p.poll() is None for _, p in procs):
            time.sleep(0.5)
        log("有一个服务退出了，一起停。")
    except KeyboardInterrupt:
        log("收到 Ctrl+C，正在停止 ...")
    finally:
        for _, p in procs:
            if p.poll() is None:
                p.terminate()
        for _, p in procs:
            try:
                p.wait(timeout=10)
            except Exception:  # noqa: BLE001
                p.kill()


def cmd_lint(_: argparse.Namespace) -> None:
    ensure_all()

    log("── 前端静态检查 ──")
    for script in ("typecheck", "lint", "format:check"):
        run(npm_cmd(["run", script]), cwd=WEB)

    log("── 后端静态检查 ──")
    py = str(venv_python())
    run([py, "-m", "ruff", "check", "."], cwd=API)
    targets = api_lint_targets()
    if targets:
        run([py, "-m", "mypy", *targets], cwd=API)
    else:
        log("跳过 mypy：core/ db/ modules/ main.py 都还不存在。")

    purity = API / "scripts" / "check_kernel_purity.py"
    if purity.exists():
        run([py, str(purity)], cwd=API)
    else:
        log("跳过内核洁癖检查：scripts/check_kernel_purity.py 未就位（T14 交付后自动启用）。")

    log("lint 通过。")


def cmd_test(_: argparse.Namespace) -> None:
    ensure_all()
    run(npm_cmd(["run", "test"]), cwd=WEB)
    run([str(venv_python()), "-m", "pytest"], cwd=API)
    log("test 通过。")


def cmd_check(args: argparse.Namespace) -> None:
    """XA 按卡自检：只跑 --paths 这一组路径涉及的检查。

    与 verify 的区别是范围不是严格度：所有子检查都跑完再汇总失败项
    （一次性暴露全部问题，方便按卡修复），最后以非零码退出。
    """
    ensure_all()
    web, api = classify_paths(args.paths)
    if not web and not api:
        die("check：--paths 里没有可用路径。示例：check --paths apps/web/src/apps/docs")
    log(f"按卡自检范围：前端 {web or '（无）'} ／ 后端 {api or '（无）'}")
    failures: list[str] = []

    def attempt(name: str, cmd: list[str], cwd: Path) -> None:
        if run(cmd, cwd=cwd, check=False).returncode != 0:
            failures.append(name)

    if web:
        log("── 前端（apps/web）──")
        # tsc 无法按路径裁剪：类型图是整仓的，只报一次全量结果。
        log("tsc 为全项目 --noEmit（类型检查不可按路径拆分，有意为之）")
        attempt("tsc", node_bin(["node_modules/typescript/bin/tsc"]) + ["--noEmit"], WEB)
        attempt("eslint", node_bin(["node_modules/eslint/bin/eslint.js"]) + web, WEB)
        # 与 format:check 同口径（只查 ts/tsx/css）：目录展开成 glob，
        # 其他后缀的文件按仓库约定直接放行。
        pp_targets: list[str] = []
        for p in web:
            if (WEB / p).is_dir():
                pp_targets.append(f"{p}/**/*.{{ts,tsx,css}}")
            elif Path(p).suffix in (".ts", ".tsx", ".css"):
                pp_targets.append(p)
            else:
                log(f"prettier 跳过（仓库口径不含该后缀）：{p}")
        if pp_targets:
            attempt(
                "prettier",
                node_bin(
                    [
                        "node_modules/prettier/bin/prettier.cjs",
                        "node_modules/prettier/bin-prettier.js",
                    ]
                )
                + ["--check", *pp_targets],
                WEB,
            )
        attempt(
            "vitest",
            node_bin(["node_modules/vitest/vitest.mjs"])
            + ["run", "--passWithNoTests", *web],
            WEB,
        )

    if api:
        log("── 后端（services/api）──")
        py = str(venv_python())
        attempt("ruff", [py, "-m", "ruff", "check", *api], API)
        mypy_targets = [
            # 与 lint 口径一致：tests/ 有意不进 mypy（全仓测试函数无标注，
            # cmd_lint 的目标集本来就只含 core/db/modules/scripts/main.py）。
            p
            for p in api
            if _has_py(API / p) and Path(p).parts[:1] != ("tests",)
        ]
        if mypy_targets:
            attempt("mypy", [py, "-m", "mypy", *mypy_targets], API)
        else:
            log("跳过 mypy：--paths 里没有该查的目标（tests/ 按项目口径不进 mypy）。")
        tests = related_py_tests(api)
        if tests:
            attempt("pytest", [py, "-m", "pytest", *tests], API)
        else:
            log("提示：没找到相关 pytest 测试文件（tests/ 下无同名匹配）。")

    if failures:
        die("check 未通过：" + "、".join(failures))
    log("check 通过（按卡路径）。")


def cmd_verify(_: argparse.Namespace) -> None:
    cmd_lint( argparse.Namespace() )
    cmd_test( argparse.Namespace() )
    log("── 前端构建 ──")
    run(npm_cmd(["run", "build"]), cwd=WEB)
    # T18 唯一判据守门：发现脚本存在即自动运行（同 check_kernel_purity 的接入模式）。
    criterion = API / "scripts" / "verify_t18_criterion.py"
    if criterion.exists():
        log("── T18 唯一判据（加工具 = manifest 多一行 provides）──")
        run([str(venv_python()), str(criterion)], cwd=API)
    log("verify 通过：lint + test + build + T18 判据全绿。")


def cmd_build(_: argparse.Namespace) -> None:
    ensure_web_deps()
    log("── 前端产物 ──")
    run(npm_cmd(["run", "build"]), cwd=WEB)

    log("── 后端镜像 ──")
    compose = ROOT / "docker-compose.yml"
    if not docker_available():
        log("本机没有 docker，跳过镜像构建。")
        log("说明：镜像与生产编排由 T13 负责；本机开发用 `python tools/task.py dev` 即可。")
    elif not compose.exists():
        log("找不到 docker-compose.yml，跳过。")
    else:
        run(["docker", "compose", "-f", str(compose), "build"])


def cmd_clean(_: argparse.Namespace) -> None:
    targets = [
        WEB / "dist",
        WEB / "node_modules" / ".vite",
        API / ".pytest_cache",
        API / ".mypy_cache",
        API / ".ruff_cache",
    ]
    for t in targets:
        if t.exists():
            log(f"删除 {t.relative_to(ROOT)}")
            shutil.rmtree(t, ignore_errors=True)

    removed = 0
    for p in API.rglob("__pycache__"):
        if VENV in p.parents:
            continue
        shutil.rmtree(p, ignore_errors=True)
        removed += 1
    log(f"清理 __pycache__ {removed} 个。")
    log("data/ 未动。clean 完成。")


def cmd_new_plugin(args: argparse.Namespace) -> None:
    script = API / "scripts" / "create_plugin.py"
    if not script.exists():
        die(
            "services/api/scripts/create_plugin.py 还不存在 —— 它是 T14（插件框架）的交付物。\n"
            "  T14 交付后本命令自动可用。"
        )
    # ★ 必须转发 --kind：create_plugin.py 的默认是 third-party
    #   （落 `plugins/<id>/`，带 api/ 子目录），而内置业务插件要的是 builtin
    #   （落 `services/api/modules/<id>/` + `apps/web/src/apps/<id>/`）。
    #   早期版本漏了这个参数，于是内置卡的骨架全被生成成第三方布局：
    #   多出 api/ 层、迁移落错位置、web 入口根本没生成。
    run([str(venv_python()), str(script), args.id, args.name, "--kind", args.kind], cwd=API)


def cmd_help(_: argparse.Namespace) -> None:
    print(
        """
Alongside 任务入口（与 Makefile 等价）

  setup        首次准备：建 venv + 装前后端依赖
  dev          同起前端 5173 与后端 8000（Ctrl+C 一起停）
  lint         前端 tsc + eslint + prettier ／ 后端 ruff + mypy
  test         前端 vitest ／ 后端 pytest
  verify       交付前必跑：lint + test + 前端构建
  check        按卡自检：--paths 只跑这组路径涉及的检查（tsc 为全量，见 help）
  build        前端产物（有 docker 时顺带建后端镜像）
  clean        清构建缓存（data/ 绝不动）
  new-plugin   生成新插件骨架（脚手架由 T14 提供）
  help         显示这段说明
""".strip()
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="task", description="Alongside 跨平台任务入口")
    sub = parser.add_subparsers(dest="command")

    for name, fn, help_text in [
        ("help", cmd_help, "显示帮助"),
        ("setup", cmd_setup, "首次准备依赖"),
        ("dev", cmd_dev, "同时启动前后端"),
        ("lint", cmd_lint, "静态检查"),
        ("test", cmd_test, "跑测试"),
        ("verify", cmd_verify, "lint + test + build"),
        ("build", cmd_build, "构建产物"),
        ("clean", cmd_clean, "清缓存"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=fn)

    p_ck = sub.add_parser("check", help="按卡自检：只跑 --paths 涉及的检查")
    p_ck.add_argument(
        "--paths",
        nargs="+",
        required=True,
        metavar="PATH",
        help="卡片涉及的路径（相对项目根 / apps/web / services/api 三种写法均可）",
    )
    p_ck.set_defaults(func=cmd_check)

    p_np = sub.add_parser("new-plugin", help="生成新插件骨架")
    p_np.add_argument("--id", required=True, help="插件 id（小写英文，等于目录名）")
    p_np.add_argument("--name", required=True, help="插件中文名")
    p_np.add_argument(
        "--kind",
        choices=["builtin", "third-party"],
        default="builtin",
        help="插件类型，默认 builtin（内置插件的骨架分落两处）",
    )
    p_np.set_defaults(func=cmd_new_plugin)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        cmd_help(args)
        return
    args.func(args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
