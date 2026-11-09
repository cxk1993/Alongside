"""仓库路径的**唯一真相源**。

★ 为什么需要这个文件
--------------------
门禁脚本（`tools/**`）之前各自用 `Path(__file__).resolve().parents[N]` 算仓库根：
`N` 取决于脚本自己放在哪一层，于是同一个仓库在不同脚本里被算成三种路径。
症状很隐蔽——门禁照常打印「通过」，只是它扫的那个目录**根本不存在**：
空目录扫出零个文件，零个违规，退出码 0。**门禁绿了，什么也没验。**

⇒ 路径只算一次，就在这里。所有门禁 `from paths import REPO`，不再自己数 `parents`。

★ 为什么不做成可配置
--------------------
仓库根是**发行结构**，不是运行期配置。能配置就能指错，
指错之后门禁会安静地跑在错误目录上——那正是这道防线存在的理由。
（同源约束：`services/api/packs/roots.py` 的发行版包根同样写死在代码里。）

★ 用法
------
    # ① 脚本与 tools/ 同层（最常见）
    python tools/check_research_alignment.py

    # ② 从任意 CWD 调用（CI、pytest、别的脚本 subprocess 起它）
    #    脚本自己把 tools/ 塞进 sys.path，再从本模块取常量

    # ③ 作为包导入
    from tools.paths import REPO, API_ROOT
"""
from __future__ import annotations

from pathlib import Path

# ─────────────────────── 根 ───────────────────────

#: 本文件在 `<repo>/tools/paths.py` ⇒ parents[0] = tools，parents[1] = 仓库根。
REPO: Path = Path(__file__).resolve().parents[1]

# ─────────────────────── 后端 ───────────────────────

#: 后端服务根（`services/api/`）—— 后端的一切相对它算。
API_ROOT: Path = REPO / "services" / "api"

#: 门禁与运维脚本（`services/api/scripts/`）。
SCRIPTS: Path = API_ROOT / "scripts"

#: 内置插件（`services/api/modules/`）—— 每个子目录一个 manifest.json。
MODULES: Path = API_ROOT / "modules"

#: 内核实现（`services/api/core/`）。
CORE: Path = API_ROOT / "core"

#: 包层实现（`services/api/packs/`）—— ★ 不是发行版包根，见 PACKS_BUNDLED。
API_PACKS: Path = API_ROOT / "packs"

#: 后端测试（`services/api/tests/`）。
TESTS: Path = API_ROOT / "tests"

#: 协议测试（`services/api/tests_protocol/`）。
TESTS_PROTOCOL: Path = API_ROOT / "tests_protocol"

#: 包层测试（`services/api/tests_packs/`）。
TESTS_PACKS: Path = API_ROOT / "tests_packs"

# ─────────────────────── 仓库级 ───────────────────────

#: 契约（`contracts/`）：`plugin.schema.json` / `pack*.schema.json`。
CONTRACTS: Path = REPO / "contracts"

#: 门禁脚本（`tools/`）。
TOOLS: Path = REPO / "tools"

#: 第三方插件（`plugins/`）—— 与内置插件同构，但来源是用户/第三方。
PLUGINS: Path = REPO / "plugins"

#: ★ 发行版包根（项目根 `packs/`）—— 随发行版走的官方包。
#:   与 `API_PACKS`（代码包）同名不同物：
#:   放错层级会 import 到发行目录里的某个包，症状极怪而根因只是路径。
PACKS_BUNDLED: Path = REPO / "packs"

#: 用户数据里的包根（`data/packs/`）—— 本地包，read-write。
PACKS_DATA: Path = REPO / "data" / "packs"

#: 文档（`docs/`）。
DOCS: Path = REPO / "docs"

#: 前端（`apps/web/`）。
WEB: Path = REPO / "apps" / "web"

#: 前端源码（`apps/web/src/`）。
WEB_SRC: Path = WEB / "src"

# ─────────────────────── 工具函数 ───────────────────────


def rel(path: Path) -> str:
    """把绝对路径转成**相对仓库根**的 POSIX 串（报告里统一用它）。

    ★ 报告里出现 `E:\\ai work\\...` 这种绝对路径时，换台机器就没人能读。
      统一用相对路径，且分隔符固定为 `/`——Windows 的 `\\` 在
      Markdown 表格里要被转义，写进报告会变成一坨。
    """
    p = Path(path).resolve()
    try:
        return p.relative_to(REPO).as_posix()
    except ValueError:
        # 不在仓库里（例如用户数据目录）：退回绝对路径，但仍是 POSIX 分隔符
        return p.as_posix()


def exists_report(path: Path) -> str:
    """给门禁打印用的「路径 + 是否存在」短串。

    ★ 门禁最常见的一种假绿是「目标目录不存在 ⇒ 扫了零个文件 ⇒ 零违规」。
      把它打出来，看日志的人一眼能看出扫的是哪儿、在不在。
    """
    p = Path(path)
    mark = "✓" if p.exists() else "✗ 不存在"
    return f"{rel(p)} ({mark})"


__all__ = [
    "REPO",
    "API_ROOT",
    "SCRIPTS",
    "MODULES",
    "CORE",
    "API_PACKS",
    "TESTS",
    "TESTS_PROTOCOL",
    "TESTS_PACKS",
    "CONTRACTS",
    "TOOLS",
    "PLUGINS",
    "PACKS_BUNDLED",
    "PACKS_DATA",
    "DOCS",
    "WEB",
    "WEB_SRC",
    "rel",
    "exists_report",
]
