# ISSUE-006 · 内核迁移扫描路径与全部插件实盘目录不一致，builtin 迁移永不执行

> 编号顺延：docs/issues 现有 001–005。005 归 hermes（内核 api client 注入），勿混。

- **提出者**：Qoder CN（L2 真机验收线）
- **影响谁**：T14（`services/api/core/plugins/**` 领地，workbuddy，交付 `3bb5d6c`）＋ Zcode（编排/部署）；**所有带表的 builtin 插件**（docs/todo/finance/notes/calendar/habits/review/agents/web…）
- **状态**：**done（2026-11-24 汐瑶实测复核）** —— 内核已改为「按 manifest 声明解析 + 启动对账」，
  比本卡原提的两个方案更彻底（不再靠猜路径）。复核证据见文末「复核记录」。
- **优先级**：blocker
- **日期**：2026-09-19

---

## 我遇到的现象

`services/api/core/plugins/migrations.py:24`：

```python
def _migration_dir(info): return info.directory / "api" / "migrations"
```

内核只扫 `modules/<id>/api/migrations/`；而**全部 builtin 插件的迁移实际放在 `modules/<id>/migrations/`**（如 `modules/docs/migrations/0001_init.py`、`0002_fts.py`）。两边永远对不上 ⇒ 插件升级/首装时其 SQL 迁移**一条都不会跑**。

复现（本机 dev 库，全新空库起后端后）：

1. 打开文档模块，右键新建文稿 → 输入正文 → 保存；
2. `PUT /api/v1/docs/nodes/{id}/content` → **500**，RFC7807 detail：`no such table: docs_node`；
3. 手工用 `importlib` 加载 `modules/docs/migrations/0001_init.py` 的 `upgrade(engine)` 执行后 → 同操作 **201/200 正常**，`/revisions`、FTS 搜索全部可用。

旁证（不是我第一个撞见，只是没人立卡）：
- **第二实证（21:30，web 模块）**：同一个本地库（docs 表已手工补过）打开「网页」窗，`GET /api/v1/web/entries` → **500**；手工执行 `modules/web/migrations/0001_init.py` 的 `upgrade(engine)` 建出 `web_entry` 表后 → 200，入口增删改与 SSE 广播全部正常。**每个带表模块都要各自死一遍**，docs 不是孤例。
- `T13-report.md §3-4`：生产靠手工 `SQLModel.metadata.create_all` 绕过，并注明「迁移脚本扫描路径与实盘不一致，见既有 ISSUE」——但 `docs/issues/` 里始终没有这张卡；
- `T07-report.md:86`：「内核只扫 `modules/<id>/api/migrations/`，且 run_migrations 仅 third-party install 时执行；builtin 生产路径无自动建表 hook」。

## 为什么这会挡住我

任何**新环境**（干净库首启、灾备恢复、换机迁移）里所有带表模块一保存就 500；L2 验收每条数据链路都得先手工打补丁。手工 `create_all` 只建当前模型形状的表，**跳过 FTS5 虚拟表与后续 0002+ 增量**（docs 的 `docs_fts` trigrams 就在 0002），等于把插件作者写的迁移全部作废——这不是临时绕法，是静默数据层缺失。

## 我认为应该怎么改

给 T14 的两个可选口径（择一，或双跑）：

1. **扫描端兼容**：`_migration_dir()` 依次探测 `api/migrations/` 与 `migrations/`，取存在者；同时把 builtin 的 `run_migrations` 触发点从「仅 third-party install」扩到「启动时按 `_migration_history` 对账补跑」；
2. **实盘端统一**：把全部 `modules/<id>/migrations/` 移动到 `modules/<id>/api/migrations/`（机械移动，但涉及所有插件目录，需 Zcode 统一提交窗口）。

无论哪条，都要给 `deploy/` 首启流程加一条「所有已启用插件迁移全部落库」的断言检查（跑完后 `_migration_history` 覆盖当前版本清单）。

## 建议的验证方式

1. 删掉本地 dev 库 → 起后端（不手工干预）→ 浏览器登录 → 文档模块新建/保存/搜索全绿，无 500；
2. `SELECT name FROM sqlite_master WHERE name IN ('docs_node','docs_revision','docs_fts')` 三行齐；
3. 重启一次后端，确认迁移对账幂等（不重复执行、不报错）。


---

## 复核记录（2026-11-24 · 汐瑶）

**结论：已修复，且修法比本卡提的两个方案更彻底。**

### 修法（现状代码）

`core/plugins/migrations.py` 不再猜路径，而是**按 manifest 声明解析**：

- `_declared_migration_dir(info)`：读 `manifest.migrations` 字段（协议 §4.1「manifest 是唯一契约」）
- `_migration_dir(info)`：声明了 → 按声明解析；**声明的目录不存在则报错**，不静默退回约定位
  （静默退回 = 「manifest 说的不算数」又活过来了）
- 未声明（null）→ 退回约定探测：`api/migrations/` 优先、`migrations/` 兜底
- `reconcile_migrations(engine, modules)`：启动时按 `_migration_history` 对账补跑
- 触发点：`core/app.py:193` 的 `lifespan=make_startup_lifespan(...)` → uvicorn lifespan 阶段
  （对外服务之前，正是「表必须先于请求存在」的时机）

### 实测（全新空库，2026-11-24 14:4x）

```
对账完成：18 个模块补跑了迁移
  agents: ['0001_init']      assets: ['0001_init']
  calendar: ['0001_init', '0002_reminder_log']
  docs: ['0001_init', '0002_fts']      finance: ['0001_init', '0002_snapshot']
  ...
建表数: 35
docs 相关: docs_content, docs_fts, docs_fts_config, docs_fts_content,
           docs_fts_data, docs_fts_docsize, docs_fts_idx, docs_node, docs_revision
web 相关: web_entry
```

本卡原报的三个症状全部消失：
1. `docs_node` 表自动建出（原「no such table」500）
2. `docs_fts` 虚拟表在（0002_fts 增量跑了，不是靠 `create_all` 绕过）
3. `web_entry` 表在（原第二个实证的 500）

### 遗留

本卡建议的「deploy 首启流程加断言：跑完后 `_migration_history` 覆盖当前版本清单」——
`reconcile_migrations` 已返回 `executed` 供断言使用，但**部署脚本里尚未接入**。
不阻塞（对账本身已幂等），建议在 `tools/deploy_backend_increment.py` 里补一条。
