# 微内核与包层：落地现状（2026-11）

> 这份文档回答一个问题：**仓库里现在到底跑的是哪套内核、哪一层包层，以及哪些声称的能力还没接线。**
> 设计动机不在这里（见组内技术报告）；这里只记「代码走到哪一步」。

---

## 1. 内核分层：唯一真相源是 `core/layers.py`

`core/` 的 32 个文件按「拿掉它，插件还装得起来吗」分成三类：

| 层 | 判据 | 文件数 | 含义 |
|---|---|---:|---|
| ① 组合原语 | 没它，插件装不起来 | 17 | 这才是「无特权内核」的准确含义 |
| ② 策略 | 换一套，系统还能跑 | 6 | 设计上**待下沉为插件**，现在仍住在 `core/` |
| ③ 便利 | 对「能不能装」无影响 | 5 | 设计上**待移出内核** |
| 装配器 | 只在启动跑一次 | 1 | `packs/boot.py`，不算内核 |
| 分层清单自身 | 它定义层，不被层定义 | 1 | `core/layers.py` |
| 包标记 | 无分层含义 | 2 | `__init__.py` |

**别自己数文件**：`core/layers.py` 是唯一真相源，门禁与文档都从它读。

## 2. 插件协议：36 字段 / 必填 11 / 三层校验

- **契约**：`contracts/plugin.schema.json`（唯一真相源）
- **第一层 · 结构**：`core/plugins/validate.py`（自实现 JSON Schema 子集，不引依赖）
- **第二层 · 跨字段**：同文件 `_check_consistency`（`requires ∩ optionalDependencies = ∅` 等）
- **第三层 · 跨插件**：`core/plugins/crosscheck.py`（能力缝二义、多 `provides` 登记待裁决）

**「字段有落点」不等于「被消费」**：协议里的每个字段都能被 `Manifest` 读到，
但只有少数会**改变运行时行为**。两档差距由 `services/api/scripts/check_protocol_consumption.py`
机器判出（AST 扫描），且**豁免表不许过期**——字段一旦真被消费，门禁会逼你把它从表里摘掉。

## 3. 包层：`services/api/packs/`，**不在 `core/` 下**

这是有意的：包层是「类内核以外」的第三层，与插件层同级。
住在 `core/` 里，内核的分层清单与预算门禁都会把它当内核的一部分——
三年后接手的人会顺着目录位置把它当内核改。**目录位置本身就是最有力的架构声明。**

四条实现纪律：

1. 装配器**不实现** enable / disable，全部由内核注入；
2. `pack_state`（用户调整层）只由用户动作写，装载**只读**；
3. 四样留档各管一件事：`pack.json` 原件 / `pack.lock.json`（基线）/ `pack.assembly.json`（快照）/ `state.json`；
4. 独占的键是**装配目标**，不是包名。

**内核侧只留一次调用**（`core/app.py`）：`app.state.pack_reports = boot_packs(app)`。
内核连「要不要装」都不判断——写了 `if settings.packs_enabled` 就等于让内核知道「包层有开关」，
那条边界就开始漏。

## 4. ★ 哪些声称的能力还没接线（诚实登记）

**2026-11-09 更新：数据作用域已接线，且实测三态成立。**

| 能力 | 现状 | 差在哪 |
|---|---|---|
| **数据作用域（SQLite authorizer）** | ✅ **已接线（2026-11-09）** | 三态：`kernel_session()`=内核特权 / `db_session(id)`=插件边界 / **未声明=deny-all**。`registry.mount(scoped=...)` 只给 `kind=third-party` 挂作用域依赖（内置模块之间本来就有跨模块读，那是误伤不是安全）。`db/engine.py` 挂 `Session.after_begin` + `engine.checkin`。**实测 7/7**：kernel 读写任意表 OK；`db_session("todo")` 读/写 `todo_item` OK、读 `finance_entry` 与 `plugin_state` DENIED；裸 Session DENIED。门禁 `tools/gates/verify_db_scope_wiring.py`（G16）守着它 |
| 逐次拦截层 / Reactive Coeffect | 机制在（`coeffect.py` / `dependency.py`），按依赖表驱动 | 依赖通知已可用；拦截仍是三合一通道 |
| `lifecycle` 四钩子 | ✅ **已接线（2026-11-10）** | 钩子**名字固定**（`on_install/on_enable/on_disable/on_uninstall`），manifest 的 `lifecycle` 从「钩子名映射对象」降级为枚举：`"hooks"` = 有钩子，`null`/缺省 = 没有。内核 `core/plugins/lifecycle.py:declaration_problem()` 校验「声明 ↔ 实现」一致——声明了却没文件、或写了钩子却没声明，都会写进 `plugin_state.last_error`（readyz 可见），但不拦启停（软失败语义不变）。裁决依据：允许自定义钩子名没有真实需求（唯一用过映射的 pi-agent，三个值恰好等于固定名），代价是「写错名=静默不执行」 |
| `consumes` 声明即订阅 | ✅ **已接线（2026-11-09）** | `subscribe_declared(info, handler, topics=...)` 是插件订阅的**唯一入口**：内核从 `info`（PluginInfo / Manifest / 原始 manifest）读 `consumes`，处理器要的 topic 不在声明内就**当场拒绝注册**（fail-closed），投递时再按声明过滤。push 的三路联动（提醒/待办/上课）已从 `add_listener` 直连改为声明式，并补齐了 manifest。内核自用的挂点改名为 `add_kernel_hook`——名字就是纪律。`check_protocol_consumption` 的未消费表里已摘掉 `consumes` |
| `absorbs` 吸收机制 | ✅ **已接线（2026-11-10）** | 吸收关系只有一处来源：manifest 的 `tabs[].entry`（**有页**的吸收）∪ `absorbs[]`（**无页**归口）。前端 `Dock.tsx` 的 `DOCK_MERGE`（19 条）与 `moduleGraph.ts` 的补表都已**删除**，`moduleGraph.buildModuleGraph()` 直接按 manifest 推导。19 条关系全部在 manifest 中有落点：14 条由 tabs 表达、system 的 `auth` 是无 entry 页、`countdown` 走 system 的 absorbs、`agents`/`ai-chat` 走 pi-agent 的 absorbs、`diary` 是 knowledge 的第 4 页。门禁 `tools/check_manifest_absorbs.py`（G17）判五条一致性（重复声明 / 双容器争抢 / 幽灵目标 / 吸收容器 / 有 UI 却被无页归口） |
| `emits` / `consumes` 投递 | ✅ **双向契约成立（2026-11-10）** | 订阅端（2026-11-09）：未声明 topic 不注册、不投递。发布端（2026-11-10）：`publish()` 按 `source` 查该模块的 `emits` 声明，未登记/未覆盖 ⇒ 抛 `UndeclaredPublication`，**事件不发出去**（fail-closed）；内核自身事件走 `publish_kernel_event()`，不受模块声明约束。全仓对账后 45 条声明 ↔ 45 种发布一一对应：补了 agents 的 5 条（agent.created/updated/deleted、task.deleted、task.reported）与 review 的 `review.all.ingested`，删了 9 条空承诺（packs 3 / plugins 4 / push.sent / agents.task.done）。`check_protocol_consumption.py` 做双向静态校验（含 `source` 必须等于模块 id —— 顺带抓出 web 曾用 `web_entry` 当事件源） |

> 口径提醒：数据作用域是**纪律边界，不是沙箱**。
> 插件与内核同进程同权，`import sqlite3; sqlite3.connect(path)` 就绕开了。
> 它防 bug / 无意越界 / AI 写错表名，**不防恶意代码**。对外不许写成「隔离」。

### 4.1 ★ 内核仍持有业务路由（不是缺陷，但必须写清）

`/api/v1/summary/today`（BFF 聚合）与 `summary` 模块**同前缀**。**2026-11-10 已裁决（选项 a）**：
模块自己的 `/today` **删掉了**（模块只留 `/health`），今日摘要只有内核 BFF 一个实现。

为什么不是「把 BFF 挪到 /api/v1/_bff/today，让模块路由活过来」：summary 模块**没有独立数据源**，
它读的全是 calendar/todo/diary/review 的 `today-summary`。让模块也持有一份聚合 = 在内核之外
再放一份聚合逻辑，而「内核只转发不解析」的边界正是靠「聚合只有一处」撑住的。

判据锁在 `services/api/tests_protocol/test_route_shadowing.py`：任何模块路由只要与内核显式路径
撞上（同 method + 同 path），测试就红 —— 那类路由**永远不会执行**，是最难被当成 bug 报上来的缺陷。

同样在 `core/` 里的还有：`/api/v1/events/*`（事件总线 SSE）、`/api/v1/modules`、
`/api/docs`（聚合文档）、`/api/{module_id}/openapi.json` 与 `/api/v1/{module_id}/openapi.json`。

**这不是「内核不纯」**——BFF 聚合与事件总线是内核职责（协议 R-1：内核只转发不解析）。
但**下一个人很容易去调 summary 模块那个死端点**，所以写在这里：
**要看今日摘要，走 `/api/v1/summary/today`（BFF），不要走模块路由。**

### 4.2 初版范围

三档（必带 / 可带需标注 / 本期剥离）见
`E:\ai work\alongside-refresh\reports\initial-version-scope.md`。

## 5. 门禁在哪

| 门禁 | 位置 | 拦什么 |
|---|---|---|
| 协议结构 + 跨字段 | `tests_protocol/test_protocol_gates.py` | 36 字段契约、规则表逐条 |
| 校验器可信度 | `tools/crosscheck_validator.py` + 同名测试 | 自研校验器 vs 标准 JSON Schema 交叉裁判；`--selfcheck` 五种注入反向验证 |
| 字段消费 | `services/api/scripts/check_protocol_consumption.py` | 未消费字段必须登记，登记了就必须真有消费方 |
| 包层不变量 | `tools/gates/verify_pack_invariants.py` | 包层 §6 的全部不变量 |
| 门禁清单自省 | `tools/gate_registry.py` | 登记的门禁必须存在、且有回归测试守着它自己 |
| 报告与代码不脱节 | `tools/gen_{kernel_doc,protocol_doc,kernel_code_map}.py` | 三份报告由代码生成，改代码后重跑 |

★ `tools/paths.py` 是**仓库路径的唯一真相源**。
早先各门禁自己数 `parents[N]`，算错时门禁会扫一个不存在的目录——零文件、零违规、退出码 0。
**门禁绿了，什么也没验。** 这是最难发现的一类假绿。
