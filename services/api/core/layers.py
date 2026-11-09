"""内核分层清单——「哪个文件属于哪一层」的**唯一真相源**。

## 这份清单解决什么

内核膨胀从来不是有人故意往里塞东西，而是**「加进去」毫无阻力**。
所以先把判断从人的脑子里搬进仓库，让「加进去」这个动作产生摩擦。

有了它，三件事同时成立：

1. **门禁有依据**——内核行数预算、内核不许出现策略分支、策略必须可替换，
   这三条门禁的判据都依赖「知道哪个文件是哪一层」；
2. **争议有结论**——「xxx 应该放哪」直接查清单，不用重新推演；
3. **新文件有阻力**——清单里没有的文件，加进 `core/` 会被门禁拦下。

## 三层的划分依据

划分不是按「重要不重要」，而是按**换一个系统时，这段代码要不要跟着换**：

| 层 | 判据 | 失效方式 |
|---|---|---|
| ① 组合原语 | 没它，插件装不起来 | 膨胀 |
| ② 策略 | 换一套，系统还能跑 | 僵化 |
| ③ 便利 | 对「能不能装」无影响 | 回流 |
| 装配器 | 只在启动跑一次，不算内核 | —— |

「换一套」这个问句是分层的**唯一判据**：
「换一套权限模型内核要不要改」——答案取决于权限判定写在哪一层。

## 为什么要写在代码里而不是文档里

**代码/Schema 为协议，文档为说明。** 清单写在文档里，就只是又一份需要人工同步的说明；
写在代码里，它能被门禁直接 import，判据与清单**不可能分叉**。

## 清单与《微内核技术报告》§2.6 的关系

本文件是技术报告 §2.6「把分层写成清单」那段的**落地实现**——
报告给的是设计，这里给的是**可被程序读的那一份**。

⚠ 与技术报告 §2.6 清单的三处出入，都在这里显式记下——
**不让「报告里没有」变成「代码里漏写」**：

1. **新增** `plugins/crosscheck.py`（跨插件一致性校验，协议报告 §19 第③层）。
   按同一判据归入**② 策略**：校验规则是策略，
   换一套校验规则（更严或更宽）系统仍能跑起来，这正是策略层的定义。

2. **补上** `deps.py` 与 `manifest.py`——技术报告的清单没列它们。
   按同一判据归入**① 组合原语**：
   `deps.py` 是「插件调其它插件」的唯一入口，「不可绕过的点」之一；
   `manifest.py` 是 manifest 的运行时数据模型。
   两者都是**机制**——换一套权限模型要改的是 `permissions.py`（策略层），
   不是这两个文件。

   ★ 但它们各自内部**机制与策略混在同一段代码里**（见《微内核代码地图》§4），
     所以它们同时是**整改对象**：职责要拆开，拆开之前先归到机制这一侧，
     免得「还没拆」变成「没人管」。

3. **新增** `plugins/dependency.py`（依赖表 + 满足性驱动的通知机制）。
   按同一判据归入**① 组合原语**：它提供的是「记谁提供什么能力」与
   「变了就重算一遍」这两个**机制**；至于「满足与否该不该影响阶段」
   （`deactivation: auto` / `manual`）是**策略**，由调用方按声明传入，
   依赖表本身只负责判定与报告，不替插件决定降级方式。

   ★ 依赖表必须走 effect 原语：它的变更要能随插件卸载自动回滚。
     不走的话，插件卸载后依赖表里还留着它的能力名——而这类残留会让别的
     插件误判「这个能力还能用」，最难查。

4. 技术报告清单里的 `kernel.py` 与 `plugins/phase.py` **已落地**
   （2026-10-27）：effect 原语与七态状态机。

5. **新增** `plugins/state_store.py` / `plugins/coeffect.py` / `plugins/reconcile.py`
   （2026-10-27，三章落地时拆出）。按同一判据归入**① 组合原语**：

   - `state_store.py` —— 阶段的**唯一落地点**。「改状态 + 写审计同事务」
     这个要求只能在落地点保证（状态机给不出事务边界），
     而把落地点与状态机分开，状态机才能在没有数据库的情况下被穷举验证。
   - `coeffect.py` —— 依赖变化与状态机之间的**接缝**。依赖表回答
     「能力有没有人提供」，状态机回答「插件现在在哪一步」，
     中间缺的这根线就是「能力被撤了，依赖它的插件不会自己知道」。
   - `reconcile.py` —— 启动对账的**判据**。它之所以要独立成文件，
     是因为它承载了一组具体的风险判断（哪些遗留状态要重试、
     哪些必须等人确认、补偿到底撤什么），
     这些判断散在 `app.py` 里会被当成启动细节，改起来也没人知道动过什么。

⚠ 清单里带「（待新建）」注释的条目**当前还不存在**——
它们是设计已定、代码未落地的部分。门禁不会去检查它们是否存在，
但 `tests_protocol/` 里的清单测试会盯住这件事：**清单与代码的差距要显式可见，不能悄悄漂移。**

（2026-10-27：`kernel.py` 与 `plugins/phase.py` 已落地，「（待新建）」标记已摘。）
"""

from __future__ import annotations

from typing import Literal

Layer = Literal["primitives", "policies", "conveniences", "assembler", "unknown"]

# ── ① 组合原语：没它，插件装不起来 ───────────────────────────────
KERNEL_PRIMITIVES: tuple[str, ...] = (
    "registry.py",            # 路由挂载点
    "deps.py",                # 能力授权与内部调用的唯一入口
    "db_scope.py",            # 数据访问作用域（插件只碰自己前缀的表）
    "manifest.py",            # manifest 运行时数据模型
    "events.py",              # 事件总线
    "kernel.py",              # effect 原语（注册即带逆）
    "plugins/manager.py",     # 插件状态机
    "plugins/migrations.py",  # 迁移台账
    "plugins/discover.py",    # 插件发现
    "plugins/activator.py",   # 激活流程
    "plugins/phase.py",       # 阶段转移（七态机）
    "plugins/dependency.py",  # 依赖表 + 幂等重算通知
    "plugins/state_store.py",  # 阶段的唯一落地点（改状态 + 写审计同事务）
    "plugins/coeffect.py",    # 依赖变化 → 状态转移的接缝
    "plugins/reconcile.py",   # 启动对账（遗留状态处置计划）
    "plugins/version.py",     # 版本判定
    "plugins/lifecycle.py",   # 生命周期钩子
)

# ── ② 策略：换一套，系统还能跑 ───────────────────────────────────
POLICIES: tuple[str, ...] = (
    "plugins/permissions.py",
    "plugins/validate.py",
    "plugins/crosscheck.py",  # 跨插件一致性校验（技术报告成文后新增）
    "middleware.py",
    "security.py",
    "mcp_writes.py",
)

# ── ③ 便利：对「能不能装」无影响 ─────────────────────────────────
CONVENIENCES: tuple[str, ...] = (
    "errors.py",
    "logging.py",
    "config.py",
    "pagination.py",
    "plugins/settings.py",
)

# ── 装配器：只在启动跑一次，不算内核 ─────────────────────────────
ASSEMBLER: tuple[str, ...] = (
    "app.py",
)

# ── 不参与分层 ────────────────────────────────────────────────────
# 包标记文件：只有 `from __future__ import annotations` 之类，没有分层含义。
PACKAGE_MARKERS: tuple[str, ...] = (
    "__init__.py",
)

#: **已移出 `core/` 的便利模块**——G15「回流防护」的登记处。
#:
#: ★ 为什么单独一个集合：G15 判的是「被移出去的东西又被 import 回来」
#:   （技术报告 §9.4：③便利的失效方式是「**回流**」）。
#:   而便利模块**现在还在 `core/` 里**——此时 import 它们完全正常
#:   （错误类型是内核的一部分）。
#:   所以 G15 不是「便利文件在 core/ 里时不能被 import」，
#:   而是「移出之后不许再拉回来」。
#:
#: ⇒ 某个便利模块真的移出 `core/` 时，把它登记到这里，
#:   G15 就开始盯着它。**现在是空的**——因为还没有模块被移出。
EXTRACTED: frozenset[str] = frozenset()

#: 清单自身：它不属于任何一层——它**定义**了层。
#: 把它算进某一层，等于让「度量尺子」自己被尺子量。
SELF = "layers.py"

ALL_LAYERS: dict[Layer, tuple[str, ...]] = {
    "primitives": KERNEL_PRIMITIVES,
    "policies": POLICIES,
    "conveniences": CONVENIENCES,
    "assembler": ASSEMBLER,
}

LAYER_LABELS: dict[Layer, str] = {
    "primitives": "①组合原语",
    "policies": "②策略",
    "conveniences": "③便利",
    "assembler": "装配器（不算内核）",
    "unknown": "未分类",
}

#: 属于内核的行数预算口径包含的层。
#: 装配器不计入——它只在启动跑一次，不是插件的运行期依赖。
BUDGET_LAYERS: tuple[Layer, ...] = ("primitives", "policies", "conveniences")


def layer_of(rel_path: str) -> Layer:
    """返回 `core/` 下某个相对路径所属的层。

    ★ 传进来的应是**相对 `core/` 的路径**，用 `/` 分隔（`"plugins/validate.py"`）。
      传绝对路径或带 `core/` 前缀都会返回 `"unknown"`——
      这是刻意的：判据要窄，宁可判不出来，也不要判错。
    """
    rel = rel_path.replace("\\", "/").strip()
    if rel.startswith("core/"):
        rel = rel[len("core/"):]
    if not rel or rel in PACKAGE_MARKERS or rel == SELF:
        return "unknown"
    for layer, files in ALL_LAYERS.items():
        if rel in files:
            return layer
    return "unknown"


def is_kernel_file(rel_path: str) -> bool:
    """该文件是否属于内核（组合原语 / 策略 / 便利）。

    装配器**不算**内核——它只在启动跑一次。
    """
    return layer_of(rel_path) in BUDGET_LAYERS


def files_in_layer(layer: Layer) -> tuple[str, ...]:
    """返回某层的全部文件（相对 `core/` 的路径）。"""
    return ALL_LAYERS.get(layer, ())


def unclassified() -> tuple[str, ...]:
    """列出 `core/` 下**已存在但清单里没有**的文件。

    ★ 这是「加进去毫无阻力」的解药：
      新文件进了 `core/` 却没有被清单登记，门禁会报出来，
      逼着人回答「它属于哪一层」——而不是让它悄悄混进内核。

    ★ 它查的是**自己所在目录**，所以放在这里而不是门禁里：
      清单住在 `core/layers.py`，它对「`core/` 里有什么」有第一手认知。

    ★ 只统计**实际存在**的文件；清单里写了但文件还不存在的（待新建）不算「漏登记」，
      那是另一件事（设计已定、代码未落地），由清单测试单独盯。
    """
    from pathlib import Path

    core = Path(__file__).resolve().parent
    found: list[str] = []
    for p in sorted(core.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(core).as_posix()
        if p.name in PACKAGE_MARKERS or rel == SELF:
            continue
        if rel not in known_files() and rel not in ASSEMBLER:
            found.append(rel)
    return tuple(found)


def known_files() -> frozenset[str]:
    """清单里登记的**全部**文件（含装配器，不含包标记与清单自身）。

    ★ 为什么含装配器：这里是「清单登记了什么」，不是「内核有什么」。
      两者用不同的函数回答——内核用 `is_kernel_file()`。
      把两个问题混在一个函数里，就会出现「装配器到底算不算内核」
      这种每次都要重新想一遍的歧义。
    """
    out: set[str] = set()
    for files in ALL_LAYERS.values():
        out.update(files)
    return frozenset(out)
