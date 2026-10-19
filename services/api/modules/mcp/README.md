# MCP Server（插件 id：`mcp`）· ADR-0003 的「桥」

把各插件 manifest 里声明的能力（`provides`）**机械映射成 MCP tools**，
让 AI 客户端（Claude / Cursor 等任意 MCP 客户端）经标准 MCP 协议操作 Alongside。

| 项 | 值 |
|:--|:--|
| id | `mcp` |
| kind | `builtin` |
| API 前缀 | `/api/v1/mcp` |
| 表名前缀 | `mcp_`（本插件只有 `mcp_pat`） |
| 迁移目录 | `api/migrations/` |

## ★ 桥语义（改这里之前必读）

- **本插件自己不声明任何业务能力**（manifest.provides 恒为 `[]`）。
- **加工具不需要改本插件任何文件**：在其他插件的 manifest 里加一行
  `provides`（如 `"notes.note.read"`），下次 `tools/list` 自动多出
  `notes_note_read`。这是 T18 的唯一判据，由
  `scripts/verify_t18_criterion.py` 机器守门。
- **映射是机械的**（`registry_adapter.py`）：
  `calendar.event.write` + `api.base=/api/v1/calendar`
  → tool `calendar_event_write` = `POST /api/v1/calendar/events`，scope `calendar:write`。
  动词表：read/list/search/free/get→GET；write/create→POST；update→PUT；delete/remove→DELETE。

## 文件地图

| 文件 | 职责 |
|:--|:--|
| `registry_adapter.py` | ★ 读 T14 注册表 → 机械摊平成 (tool, method, path, scope) |
| `forward.py` | ★ tools/call 向插件 REST 端点发内部 HTTP（不 import 插件） |
| `mcp_server.py` | MCP 协议：initialize / tools/list / tools/call（JSON-RPC） |
| `auth.py` | PAT 生成 / 哈希 / 鉴权依赖（401/403 走内核 problem+json） |
| `audit.py` | 写操作与拒绝 → audit_log（actor=`mcp:` 前缀） |
| `router.py` | 端点（MCP 入口 + PAT 管理 + 调试/审计） |
| `models.py` + `migrations/` | `mcp_pat` 表（只存哈希，明文只回一次） |

## 传输形态（备案）

MCP Streamable HTTP 的 **stateless request-response 子集**：POST=JSON-RPC 单响应、
notification=202、GET/DELETE（SSE/会话）=405（规范明文允许）。
官方 SDK 的 `StreamableHTTPSessionManager` 需要 app lifespan，插件框架给不了 ——
互操作性由官方 SDK 客户端在 verify 脚本里实测握手保证。

## 改这个插件时不许做的事

1. 不许 import 别的插件 —— 本插件只经 HTTP 转发（ADR-0002 铁律）
2. 不许在源码里写业务词 —— 工具只来自 manifest 字符串
3. 不许把 PAT 明文落库 / 落日志 / 进审计详情
4. 不许改 `provides` 契约形态 —— 冻结契约，增强走 `docs/rfc/`
5. 不许改 `kernel/` 或 `core/` —— 那是框架的 bug，去 `docs/issues/` 写卡
