"""测试会话级保险 + ★ 全量 pytest 数据库隔离（MiMo · Zcode 派工）。

## 为什么需要这个文件

1. **残留清理**：probe 模块 / plugins 临时目录 / tmp_*.db 在进程被强杀时
   teardown 不执行，会污染下一轮（T03/T15 实测踩过）。
2. ★ **DB 隔离（2026-09-20 系统性修复）**：
   - 各测试文件在 import 时设 `os.environ["DB_PATH"]`；
   - `init_engine()` **幂等只认第一次**；
   - pytest **先收集（import 全部模块）再执行** → 环境变量被「最后一个
     import」覆盖，之后第一个 fixture 的 init_engine 绑定全局库；
   - 结果：全量跑时 test_web 等打到别人的 tmp 库，出现
     `409 slug 已被占用：demo` 等连挂（单跑该文件却全绿）。

   **修法**：每个测试模块第一次 setup 前，把引擎 **reset** 到该模块
   独占的 `tmp_iso_<模块名>_<pid>.db`。模块 fixture 里再 init_engine
   即绑定到正确库；表由各模块自己 create_all/checkfirst。
   **不改任何测试断言**，只动隔离基础设施（授权）。
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

import pytest

MODULES_DIR = Path(__file__).resolve().parents[1] / "modules"
PLUGINS_DIR = Path(__file__).resolve().parents[3] / "plugins"   # 项目根/plugins
DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# 测试专用的临时模块名（集中登记，便于一眼看出哪些是"不是真模块"的东西）
# i006probe：test_issue006_migrations 的带迁移探针——2026-09-20 实测其 teardown
# 删除会被平台 safe-delete 拦截而残留（挂载数 17→18 波及后续测试），故在此登记。
# actprobe：test_activator 的激活语义探针（2026-09-20）。
LEFTOVER_PROBE_MODULES = ("t03probe", "i006probe", "actprobe")

# 测试专用的第三方插件目录前缀（T14 的插件测试会往 plugins/ 下造真插件，正常由
# uninstall 删掉；但进程被强杀时 teardown 不执行，就会像下面这样留下来）
LEFTOVER_PLUGIN_GLOBS = ("demo3p-*", "drillplug")

# 已切换引擎的测试模块（避免每条测试都 reset）
_isolated_modules: set[str] = set()


def _clean_leftovers() -> None:
    for name in LEFTOVER_PROBE_MODULES:
        d = MODULES_DIR / name
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    for pattern in LEFTOVER_PLUGIN_GLOBS:
        for d in PLUGINS_DIR.glob(pattern):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)


def _clean_tmp_dbs() -> None:
    """清掉 tmp_*.db 测试库残留（含 tmp_iso_*）。

    ★ Windows：并发进程占用时 unlink 抛 PermissionError，
      missing_ok 只吞 FileNotFoundError —— 必须 suppress，
      否则整个 pytest 会话在 setup 就炸（hermes 2026-09-20 目击）。
    """
    for p in list(DATA_DIR.glob("tmp_*.db")) + list(DATA_DIR.glob("tmp_*.db-*")):
        with suppress(FileNotFoundError, PermissionError, OSError):
            p.unlink(missing_ok=True)


def _module_db_path(mod_name: str) -> str:
    """每个测试进程内模块独占的 SQLite 路径（相对 services/api）。"""
    short = mod_name.rsplit(".", 1)[-1]
    short = re.sub(r"[^A-Za-z0-9_]+", "_", short)[:80]
    return f"./data/tmp_iso_{short}_{os.getpid()}.db"


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """模块边界：把全局引擎切到该测试模块的隔离库。"""
    mod = item.module
    if mod is None:
        return
    name = getattr(mod, "__name__", "") or ""
    if not name.startswith("tests") and "test_" not in name:
        return
    if name in _isolated_modules:
        return
    _isolated_modules.add(name)

    db_path = getattr(mod, "_TEST_DB_PATH", None) or _module_db_path(name)
    os.environ["DB_PATH"] = str(db_path)
    # 声明给模块内 fixture 使用（有的 fixture 读 environ，有的直接 init_engine）
    with suppress(Exception):
        mod._TEST_DB_PATH = str(db_path)  # type: ignore[attr-defined]

    from db.engine import init_engine, reset_engine

    reset_engine()
    # ★ 2026-09-20 workbuddy（验证中发现并修复）：
    # 原来只设 DB_PATH 环境变量再无参 init_engine()。但 services/api/.env
    # 里显式写了 DB_PATH=./data/alongside.db，pydantic-settings 在组合跑现场
    # 实测取 .env 值而非 environ 值（取证：make_engine 调用瞬间 environ
    # 已是 tmp_iso_*.db，Settings() 却仍返回 alongside.db），引擎静默指向
    # 主库 → test_issue006 的 lifespan 对账把探针表建进主库，跨文件
    # 组合跑时该测试前置断言被历史污染击穿（单跑因时序差异幸免）。
    # 改为显式传绝对 sqlite URL（同一库文件），不再依赖环境变量优先级。
    abs_db = (Path(__file__).resolve().parents[1] / db_path).resolve()
    init_engine(f"sqlite:///{abs_db.as_posix()}")


@pytest.fixture(scope="session", autouse=True)
def _clean_leftover_probe_modules() -> Iterator[None]:
    _clean_leftovers()
    _clean_tmp_dbs()
    yield
    _clean_leftovers()
