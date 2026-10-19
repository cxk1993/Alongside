# 理财（插件 id：`finance`）

由 `scripts/create_plugin.py` 生成骨架，T08 交付本地流水 CRUD，
**T08B 增补** BeeCount MCP 只读联动。**请先读 `docs/adr/0002-插件协议.md`
与 `docs/adr/0003-mcp与外部集成策略.md`。**

| 项 | 值 |
|:--|:--|
| id | `finance` |
| kind | `builtin` |
| version | `0.2.0` |
| API 前缀 | `/api/v1/finance` |
| 表名前缀 | `finance_` |
| 迁移目录 | `api/migrations/` |
| 外网权限 | `net:out:127.0.0.1`（BeeCount 恒本机同机部署） |

## BeeCount 联动（T08B）：为何 MCP、为何只读、为何禁 sync

### 为何走 MCP（不是 REST）

实测结论（《对接档案-外部系统实测.md》§1.0.1，ADR-0003 §2.6）：

```text
GET /api/v1/read/ledgers  with Bearer bcmcp_...
→ HTTP 403 {"message":"PAT can only be used for MCP endpoints"}
```

BeeCount 的 PAT（`bcmcp_*`）**只能访问 MCP 端点**。若改走 REST，则必须在
Alongside 内存放主人主密码换 JWT —— 风险显著更高，已否决。

唯一合法机器通道：

```text
POST {BEECOUNT_BASE_URL}/api/v1/mcp
Authorization: Bearer $BEECOUNT_MCP_TOKEN
Accept: application/json, text/event-stream   ← Streamable HTTP
```

### 为何本轮只读

- BeeCount 是账本**权威源**（主人手机也在用）；Alongside 以读快照为主。
- 写操作本应白名单 + `Idempotency-Key` + `audit_log`；**T08B 不实现写路径**。
- `create_transaction` 仅留接口桩并**默认禁用**（调用即 403 语义拒绝）。
- **禁止**暴露任何删除类工具（`delete_transaction` 等）。

### 为何禁 `/api/v1/sync/*`

`sync/full|pull|push` 是官方 App 的多端实时同步协议（LWW / change_id 单调性）。
Alongside 介入会与主人手机端打架。全代码库不得出现该路径调用。

### 凭据与模式

| 环境变量 | 含义 | 缺省行为 |
|:--|:--|:--|
| `FINANCE_UPSTREAM` | `mock` \| `mcp` | 默认 `mock`：离线假数据，测试全绿 |
| `BEECOUNT_BASE_URL` | BeeCount 根地址 | mcp 模式缺失 → **明确 503**，不静默空数据 |
| `BEECOUNT_MCP_TOKEN` | PAT（scope 含 mcp:read） | 同上；**只驻留内存**，不进日志/响应/报告 |

凭据**只**从环境变量（项目 `.env`，已 gitignore）读取，禁止写进代码/前端/git/报告。

### 只读工具白名单（10）

`list_ledgers` / `get_active_ledger` / `list_transactions` / `get_transaction` /
`list_categories` / `list_accounts` / `list_tags` / `list_budgets` /
`get_ledger_stats` / `get_analytics_summary`

同步只用后两个：拉统计 + 收支汇总 → 幂等写入 `finance_snapshot`（`date` 唯一 upsert）。

### 相关 API（前缀 `/api/v1/finance`）

| 方法 | 路径 | 说明 |
|:--|:--|:--|
| POST | `/snapshots/sync` | 手动只读同步（同日覆盖不翻倍） |
| GET | `/snapshots` | 快照列表（date 倒序） |
| GET | `/beecount/source` | upstream / configured / last_sync（无 token） |

### 代码边界

| 文件 | 职责 |
|:--|:--|
| `beecount_mcp.py` | MCP 客户端：initialize + tools/call、超时、错误映射、读白名单 |
| `beecount_sync.py` | 只读同步：stats + analytics → finance_snapshot 幂等 upsert |
| `migrations/0002_snapshot.py` | 建 `finance_snapshot`（date 唯一） |

## 改这个插件时不许做的事

1. 不许 import 别的插件 —— 走事件总线 / 对方公开 API / 扩展点
2. 不许 join 别人的表 —— 要数据就调对方的 API，并在 manifest.requires 里声明
3. 不许自己写登录 —— 内核统一注入 `Authorization: Bearer <jwt>`
4. 不许写死颜色 —— 只用设计令牌（`var(--accent)` 之类）
5. 不许改 `kernel/` 或 `core/` —— 那是框架的 bug，去 `docs/issues/` 写卡
6. **T08B 额外**：不碰 BeeCount `/api/v1/sync/*`；不暴露 delete/写工具；不把 PAT 写进任何仓库文件
