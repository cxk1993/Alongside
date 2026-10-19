# 能力目录（插件 id：`catalog`）· T20 · 「接口说明页」

★ 主人愿景落地：让任何 agent 只凭一枚 token + `GET /api/v1/catalog` 就能自助接入 Alongside。

| 项 | 值 |
|:--|:--|
| id | `catalog` |
| kind | `builtin` |
| API 前缀 | `/api/v1/catalog` |
| 表名前缀 | `catalog_`（`catalog_entry`：手动条目） |
| 迁移目录 | `api/migrations/` |
| 依赖 | T14（模块注册表）、T19（web_entry 形状 + 事件）、ISSUE-005 A 案（get_plugin_client） |

## 一句话

把系统里所有能被调用的能力汇总成一份「照镜子」的目录：插件自动（`plugin`）、
T19 网页条目（`web_entry`）、内核基础（`kernel`）、手动导入（`manual`）。
**四源实时合并，零登记** —— 装新插件 / 加网页 / 手动导入之后自动出现在目录里。

## API（`/api/v1/catalog`）

| 方法 | 路径 | 说明 |
|:--|:--|:--|
| GET | `/health` `/manifest` | 探针 / 清单 |
| GET | ``（根） | ★ 四源合并全量目录：`{entries, generatedAt, counts}`；**需登录**（JWT，场景 C 的入口） |
| POST | `/manual` | 新建手动条目（校验对齐 T19：url http/https、auth_ref 只收引用形式、kind 枚举） |
| PATCH | `/manual/{id}` | 编辑 / 切 `enabled` 开关 |
| DELETE | `/manual/{id}` | 删除手动条目 |

错误一律 RFC7807；`kind` 枚举沿用 `web / web+rest / web+mcp`。

## 四源数据边界（★「照镜子」原则：清单从数据来，不许写死）

| 来源 | `source` | 数据从哪来 | 联动方式 |
|:--|:--|:--|:--|
| 插件自动 | `plugin` | 模块注册表 manifest.provides | **每请求现读**，插件启停天然联动 |
| T19 网页条目 | `web_entry` | T19 的 web_entry（经 get_plugin_client 跨插件 HTTP 调 `/api/v1/web/entries?enabled=true`） | 失败降级为空列表（不报错） |
| 内核基础 | `kernel` | `kernel_capabilities.json` 静态声明文件 | 不写死在业务代码里 |
| 手动导入 | `manual` | 自己的表 `catalog_entry` | 每条独立 `enabled` 开关 |

## 共享契约追加（T20 特批，仅此一处）

`services/api/modules/web/schema.py` 的 `CapabilityEntry.source` 注释追加
`manual` 枚举值语义 —— 字段集未改，`tests/test_web.py` 契约锁全绿。

## 改这个插件时不许做的事

1. 不许 import 别的插件（尤其 T19）—— 走事件总线 / 对方公开 API / 扩展点
2. 不许 join 别人的表 —— web_entry 源经 HTTP 调用 T19 公开端点
3. 不许自己写登录 —— 内核统一注入 `Authorization: Bearer ***
4. 不许写死颜色 —— 前端只用设计令牌（`var(--accent)` 之类）
5. 不许改 `kernel/` 或 `core/` —— 那是框架的 bug，去 `docs/issues/` 写卡
6. 不许硬编码任何服务名/条目 —— 清单只能来自四源数据（照镜子判据 grep 业务词 = 0）
7. 不许实现 MCP 执行逻辑 —— 执行通道归 T18，本卡只列目录
