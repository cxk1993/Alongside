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
| `lifecycle` 四钩子 | ✅ 已被内核调用（`run_lifecycle_hook`，**按固定名** `on_enable/on_disable/on_uninstall`） | **manifest 的 `lifecycle` 映射字段不被消费**——pi-agent 曾声明全仓不存在的 `start_pi/stop_pi/cleanup_pi`，已改成与实现一致的固定名。⇒ 协议里应注明「钩子名固定，此字段仅声明位」 |
| `consumes` 声明即订阅 | ✅ **已接线（2026-11-09）** | `subscribe_declared(info, handler, topics=...)` 是插件订阅的**唯一入口**：内核从 `info`（PluginInfo / Manifest / 原始 manifest）读 `consumes`，处理器要的 topic 不在声明内就**当场拒绝注册**（fail-closed），投递时再按声明过滤。push 的三路联动（提醒/待办/上课）已从 `add_listener` 直连改为声明式，并补齐了 manifest。内核自用的挂点改名为 `add_kernel_hook`——名字就是纪律。`check_protocol_consumption` 的未消费表里已摘掉 `consumes` |
| `absorbs` 吸收机制 | ⚠️ **未建** | 31 份 manifest **全部未声明**该字段；真正的吸收写死在前端 `Dock.tsx` 的 `DOCK_MERGE`（19 条硬编码），且与 `SystemApp.tsx` 的 `DEFAULT_TABS` **已经分叉**（diary/countdown 被吸收却无页签 ⇒ 入口消失；habits 有页签却没被吸收 ⇒ 重复入口） |
| `emits` / `consumes` 投递 | `consumes` 侧已成立；`emits` 侧未声明 | 订阅端：未声明 topic 不注册、不投递 ✅。发布端仍缺约束：`emits` 有 9 个声明从未发布、6 个发布未声明（agents/packs/plugins/push 最严重）⇒ 作为双向「事件契约」目前只成立一半 |

> 口径提醒：数据作用域是**纪律边界，不是沙箱**。
> 插件与内核同进程同权，`import sqlite3; sqlite3.connect(path)` 就绕开了。
> 它防 bug / 无意越界 / AI 写错表名，**不防恶意代码**。对外不许写成「隔离」。

### 4.1 ★ 内核仍持有业务路由（不是缺陷，但必须写清）

`/api/v1/summary/today`（BFF 聚合）与 `summary` 模块**同前缀** ⇒ FastAPI「先注册先匹配」，
**模块自己的 `/today` 是死代码**（`modules/summary/router.py` 的注释里也这么写着）。

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
