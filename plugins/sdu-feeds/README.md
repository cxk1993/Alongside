# sdu-feeds · 山大通知聚合（插件 id：`sdu-feeds`）

> 纯 API 第三方插件：抓取山大**公开**站点的通知与讲座，统一去重入库；需要时一键转成待办。
> 不登录、不碰任何需要身份的接口 —— 这一版刻意把「认证」留给下一个插件。

## 1. 它抓什么

| 预设 key | 站点 | 列表页 | 日期形态 |
|---|---|---|---|
| `cs-announcements` | 计算机学院 · 学院公告 | `/xygg.htm` | `2025-01-02` |
| `cs-talks` | 计算机学院 · 学术报告 | `/xsbg.htm` | `2025-01-02` |
| `bkjx-notices` | 本科生院 · 通知公告 | `/index/tzgg.htm` | `11.11` + `2026` |
| `youth-notices` | 青春山大 · 重要通知 | `list.jsp?...wbtreeid=1004` | `12` + `2026-11` |

**站点是固定的**：只抓内置的 4 个山大公开站点（计算机学院公告 / 学术报告 /
本科生院通知 / 青春山大）。不提供“自定义源”——任何 URL 都由代码内置，
服务器不会去抓用户输入的地址（这是刻意的 SSRF 防护）。
`link_pattern` 与 `date_mode` 这两个参数就是本插件唯一需要"看懂站点"的地方。

## 2. 快速开始

```bash
# 1) 补齐内置预设（幂等）
curl -X POST http://127.0.0.1:8000/api/v1/sdu-feeds/sources/seed \
  -H "Authorization: Bearer <token>"

# 2) 同步（全部启用的源）
curl -X POST http://127.0.0.1:8000/api/v1/sdu-feeds/sync \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{}'

# 3) 看条目
curl "http://127.0.0.1:8000/api/v1/sdu-feeds/items?state=unread&limit=20" \
  -H "Authorization: Bearer <token>"

# 4) 转待办（幂等：转过就回旧 id）
curl -X POST http://127.0.0.1:8000/api/v1/sdu-feeds/items/<item_id>/promote \
  -H "Authorization: Bearer <token>"
```

## 3. 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康探测 |
| GET | `/manifest` | 回显 manifest |
| GET | `/sources` | 内置源列表 |
| POST | `/sources/seed` | 补齐内置预设（幂等） |
| POST | `/sync` | 抓取（单源或全部内置源） |
| GET | `/items` | 条目列表（按 source/state 过滤） |
| POST | `/items/{id}/state` | unread / read / archived |
| POST | `/items/{id}/promote` | 转待办（写 `todo.item.write`） |
| GET | `/stats` | 各源条目数 |

## 4. 设计边界（重要）

- **不算"爬虫"**：只抓列表页，频率由调用方控制（建议 ≥1 小时/次）；不做全文抓取、不做并发轰炸。
- **失败要诚实**：抓不到就写 `last_error`，并在 `/sync` 报告里区分
  `blocked`（需要校园网/VPN）与 `error`（网络/站点结构变了）——**绝不返回空列表冒充"今天没通知"**。
- **青春山大是 IP 白名单**：校外直连会被跳到 `ipauth.htm`，插件会明确报「需要校园网/VPN」。
- **权限是声明式的**：`permissions.net:out:<host>` 写死了三个域名，但内核当前**不执行** permissions
  —— 真正的边界靠代码里只用这三个 base_url（换源时请自己保证）。
- **转待办是唯一跨插件调用**：能力写在 `optionalDependencies`，todo 被禁用时该端点 403，其余端点照常。

## 5. 已知限制

- 第三方插件的 `web/` 不会被前端加载，所以本插件**没有界面**（这是内核现状，不是偷懒）。
  要在桌面上看见数据：转待办后去 todo 看，或直接用 Swagger（`/api/docs`）。
- 列表页改版会导致解析到 0 条 —— 这是"结构变化"，`last_error` 不会报，但 `fetched=0` 会暴露它。
  加新站点时先用 `python plugins/sdu-feeds/tests/test_parse.py` 的 fixture 方式把结构钉住。

## 6. 测试

```bash
python plugins/sdu-feeds/tests/test_parse.py          # 不依赖 FastAPI/SQLModel
python -m pytest plugins/sdu-feeds/tests -q           # 在 services/api 的 venv 里
```

fixtures 是 2026-11-13 的真实响应（各截前 4 条 `<li>`），覆盖三种日期形态与未闭合标签。

## 安全说明（2026-11-14 审计）

- 抓取地址全部来自内置预设，且抓取前仍会校验 **只允许 `*.sdu.edu.cn`**；
  拒绝回环/内网/云元数据地址与带凭据 URL（防 SSRF）。
- 抓取时**手动跟随重定向**，每一跳都重新做域名白名单校验（防 302 绕过）。
- 单页 HTML 上限 3MB，避免超大响应耗尽内存。
- `link_pattern` 是管理员级配置，仍是自由正则；如担心 ReDoS 可自行收紧写法
  （建议只用 `info/\d+/\d+\.htm` 这类简单模式，避免嵌套量词）。
