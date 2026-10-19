# 纪念日倒数 countdown（插件 id：`countdown`）

> **作者**：astrbot（云昔）｜ **版本**：0.1.0 ｜ **kind**：`third-party`
> **地位**：★ **Alongside 第一个真实第三方插件**（2026-09-23）。
> 内置插件早就有 17 个，但 `plugins/` 目录此前只有一个 `.gitkeep`——
> 也就是说 `manager.install()` / `uninstall()` 这两个**只对第三方开放**的入口，
> 在本插件之前**从未被真实插件跑过**。本 README 的「踩坑记录」一节就是这一趟的产出。

---

## 1. 它做什么

管理**纪念日**与**倒数日**，并回答一个所有其他模块都想知道的问题：**还有几天**。

| 语义 | `kind` | 行为 |
|:--|:--|:--|
| 一次性倒数日 | `countdown` | 目标日过了就是过了，`days_left` 变负、`is_past=true`（例：距毕业 800 天） |
| 每年重复 | `anniversary` | 自动滚到下一次发生日，`days_left` 恒 ≥ 0（例：唤醒日 6/27、结婚日 7/1） |

★ **2/29 的纪念日在平年落到 2/28**（不抛 `ValueError`、不静默跳月），单测钉着。

## 2. API（前缀由内核按 `manifest.api.base` 自动挂，代码内不写 prefix）

| 方法 | 路径 | 说明 |
|:--|:--|:--|
| GET | `/health` | 内核健康探测 |
| GET | `/manifest` | 回显本插件 manifest 原文 |
| GET | `/items` | 全部，按 `days_left` 升序；`?include_archived=true` 含归档；`?only_upcoming=true` 排除已过期 |
| POST | `/items` | 新建（201） |
| GET/PATCH/DELETE | `/items/{id}` | 详情 / 局部更新 / 删除（204） |
| GET | `/today` | 今天到点的（anniversary 每年命中） |
| **GET** | **`/landmarks?window=7`** | ★ **未来 N 天内的「时间里程碑」**，`{id,title,kind,on_date,days_until,note}` 已升序、已排除归档与过期 |

出参一律直接返回资源（不包 `{code,data}`）；失败走内核统一的 RFC7807 `application/problem+json`。

### `/landmarks` 为什么要存在

依据 **Dai, Milkman & Riis (2014), _The Fresh Start Effect_, Management Science 60(10)**：
人在时间里程碑（周一、月初、生日、节日）之后显著更可能开始追求目标。
系统里本来就存着主人的私人里程碑，**这个端点把它们从「被看的数字」变成「别的模块可以用的触发点」**——
概览卡、复盘发起、提醒择时都是它的下游。

## 3. ⚠️ 当前边界（V1 明确不做的事，别踩）

| # | 边界 | 原因（都是实测，不是推测） |
|:--|:--|:--|
| ① | **没有窗口 UI，`entry` 故意为空串** | 前端 `ModuleRegistry.ts:25` 的 `APP_ENTRY_GLOB = import.meta.glob("../apps/*/index.tsx")` 是**构建期**求值，只扫内置目录；把 `entry` 填成 `@/apps/countdown` 会 `loadEntry` reject「未找到插件入口」。**"第三方前端可插"是平台缺口，不是本插件偷懒** |
| ② | **跨插件调用当前会 403** | `core/deps.py::_caller_plugin_id` 靠 `app.state.modules` 反查 caller，而该注册表只登记内置模块（实测 18 条）→ 第三方永远识别不出来。**修法：census 与清单应同源**，`/example-cross-plugin` 端点保留作复验样本 |
| ③ | **不声明软依赖** | `manifest.optionalDependencies` 被 `contracts/plugin.schema.json`（`additionalProperties:false` 且无此字段）直接拒收——**写了就装不上**。内核模型其实认识这个字段；首版写了、装不上，遂删除并留复现测试 |
| ④ | 无 MCP 工具声明 | V2 候选：若将来插件可申报 `api.tools`，AI 侧即可"发现即用"；本插件首版不做，避免与契约改动抢跑道 |

## 4. 安装 / 卸载（★ 卸载是「三清」，不是「禁用」——先读完）

```bash
# 安装（内核建表 + 挂载路由 + 写 plugin_state）
curl -X POST $BASE/api/v1/plugins/install -H "Authorization: Bearer $TOKEN" -d '{"id":"countdown"}'
# 卸载
curl -X POST $BASE/api/v1/plugins/countdown/uninstall -H "Authorization: Bearer $TOKEN"
```

### 4.1 卸载到底删了什么（`core/plugins/manager.py:197-226`，逐步实测）

| 步 | 动作 | 代码位置 | 删的是**数据**还是**代码** |
|:--|:--|:--|:--|
| 1 | 跑生命周期钩子 `on_uninstall` | `manager.py:204-205` | —（本插件 `onUninstall` 为 `null`，等于无操作） |
| 2 | 摘路由 / 清扩展点注册 | `manager.py:206-207` | 运行态 |
| 3 | **倒序回滚迁移 → 删表** | `manager.py:208-212` → `migrations.py:144-156`（`mod.downgrade(engine)`） | ★★ **数据**：`countdown_item` 表被 `DROP`，**表里的行一起没** |
| 4 | 清状态表 `plugin_state` + `plugin_setting` | `manager.py:213-222` | ★ **数据**：本插件的设置项一并删除 |
| 5 | `shutil.rmtree(插件目录)` | `manager.py:223-225` | **代码**：`plugins/countdown/` 整个目录被删 |

★★ **所以"卸载"= 删表 + 删设置 + 删源码目录，三样一起清，且没有"数据留存"通道。**
**重新安装 = 全新空表，纪念日数据不可恢复。**

### 4.2 想留数据，怎么办（三条，按推荐度）

| # | 做法 | 说明 |
|:--|:--|:--|
| ① ★ **用 `disable`，不要用 `uninstall`** | 禁用只摘路由 + 停用状态，**表与数据原样保留**；想要回来 `enable` 即可 |
| ② **先导出再卸载** | `GET /api/v1/countdown/items?include_archived=true` → 存成 JSON（含 `id/title/target_date/kind/note/color/archived`）。★ 注意 `id` 是自增主键，重装后不能原样回灌，**要按 `title+target_date` 重建** |
| ③ **手备份整库** | 卸载前备份 SQLite 库文件；比 ② 重，但能连 `id` 一起保下来 |

### 4.3 作者侧纪律

本插件的 e2e 测试因此**绝不对本体执行 uninstall**，而是复制一份带随机后缀的副本来跑破坏性步骤
（`tests/test_lifecycle_e2e.py`：uninstall 三清 = 目录 / 表 / `plugin_state`，并有一条「原件未误删」红线）。

> **为什么这一节写得这么细**：`plugins/` 目录在本插件之前只有一个 `.gitkeep`，
> `manager.uninstall()` 这条路径**从未被真实插件跑过**。第一趟跑下来发现——
> 平台上**没有**"卸载但保留数据"的选项（`onUninstall` 钩子也拿不到"是否要留数据"的信号），
> 而业界规范（Agent Plugins Spec）把**数据保留**当作合规默认。
> 这属于**平台缺口**，已作为改进提案上报；**本节的写法只解决"读者不踩坑"，不解决"平台该不该改"**。

> ★ **勘误留痕**：2026-09-23 引用为 `manager.py:193-222`，**行号有 4 行偏差**；
> 正本为 `197-226`（`def uninstall` 起，`return` 止）。此处以**实测行号**为准并留痕。

## 5. 测试

```bash
cd services/api && python3 -m pytest ../../plugins/countdown/tests -q     # 20 passed
```

| 文件 | 覆盖 |
|:--|:--|
| `tests/test_countdown.py` | 日期语义 8 例（含 2/29 平年、过期不滚、当天命中）· manifest 契约校验 · **相对导入守卫** · **模型↔契约字段差集 pin** · **软依赖被契约拒的复现测试** · 表名前缀 |
| `tests/test_lifecycle_e2e.py` | 真实生命周期：discover → install → 建表 → CRUD → `/landmarks` → disable/enable → **uninstall 三清（目录 / 表 / plugin_state）** + 原件未误删红线 |

★ 其中三条是**「提醒碑」测试**（故意钉住平台现状，平台修好后它们会红）：
`test_复现_声明软依赖会被契约拒收` / `test_pin_模型与契约两张字段表不一致` / `test_清单形状与manifest缺位_pin`。
它们已被收录为该插件的判据 4/5/8/10，**修好后请顺手撤碑**。

## 6. 踩坑记录（给下一个第三方插件作者）

1. **第三方插件不能用相对导入。** 内核按文件路径加载（`discover.py:158`），`from .schema import X` 必炸 `ImportError`。
   内置插件走包导入、可以相对导入——**两者规则不同，别照抄 `modules/`**。
   本插件的做法：表模型独立在 `models.py`，`router.py` 与迁移各自**按路径加载**，
   且 `_MODEL_KEY` / `_MODEL_MODULE` **必须逐字一致**（否则缓存不共享）。
2. **迁移里的 `_load_models()` 必须先看 `sys.modules`。** 无条件 `exec_module` 会让 SQLModel 类被定义两次，
   挂载时报 `InvalidRequestError: Table 'xxx_item' is already defined for this MetaData instance`。
   ——**`create_plugin.py` 生成的迁移模板缺这道判空**（已在两份文件中修好，并回填模板）。
3. **`get_plugin_client` 不是 FastAPI 依赖。** 它签名是 `(request, target_capabilities)`，第二参数无默认值，
   写成 `Depends(get_plugin_client)` 会让 GET 端点 **422 body Field required**。正确写法见 `router.py` 的
   `/example-cross-plugin`（函数直调 + target 必须 ⊆ `manifest.requires`）。
   ——**`create_plugin.py --with-example` 生成的示例就是这个错写法**，属本插件的账，已在报备。
4. **`optionalDependencies` 目前写进 manifest 会导致装不上**（见 §3-③）。
5. **Tutorial 里两处与实现不符**：①「必须在 `services/api/` 下执行否则生成到 `scripts/`」——路径全部由
   `__file__` 推导（`create_plugin.py:41-43`），与 cwd 无关，且第三方落点是 `plugins/<id>/`；
   ② 通篇没有「相对导入」这条约束（`grep -c 相对` = 0），而它恰恰是第三方作者第一个会撞的墙。

## 7. 数据

单表 `countdown_item`（`id` / `title` / `target_date` / `kind` / `note` / `color` / `archived` + 时间戳），
迁移在 `api/migrations/0001_init.py`，权限只声明 `db:own`。
删除采用**硬删**（`archived` 字段用于"收起来"而非删除）——主人的立场是纪念物是资产，
真要永久清除得明示，所以列表默认不显示归档、也不自动清理任何东西。
