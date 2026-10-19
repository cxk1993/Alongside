# 成长罗盘（插件 id：`dashboard`）

T11 **最小首屏**：打开系统能看到「今天」。概览是**聚合器，不是数据源**——
本插件不持有业务表（`migrations: null`），所有数字都来自其它模块的真实 API。

| 项 | 值 |
|:--|:--|
| id | `dashboard` |
| kind | `builtin` |
| API 前缀 | `/api/v1/dashboard` |
| 表名前缀 | （无表） |
| 前端入口 | `apps/web/src/apps/dashboard/`（`@apps/dashboard`） |

## API

| 方法 | 路径 | 说明 |
|:--|:--|:--|
| GET | `/api/v1/dashboard/health` | 插件探活（免鉴权） |
| GET | `/api/v1/dashboard/manifest` | 清单原文 |
| GET | `/api/v1/dashboard/overview` | ★ 首屏一次取全（需鉴权） |
| GET | `/api/v1/dashboard/today` | 今日合并视图（需鉴权） |
| GET | `/api/v1/dashboard/health-of-system` | 各模块健康灯（需鉴权） |
| GET | `/api/v1/dashboard/growth` | 罗盘三轴占位（空数组 + 说明） |

`overview` 字段：

- `date` — 本地日历日
- `today` — calendar / todo / habits 原始聚合结果 + `counts`
- `money` — 最新资产快照（`/api/v1/finance/snapshots?limit=1`）+ 账本汇总
- `review` — `/api/v1/review/source`
- `growth` — `axes: []` + 占位说明
- `system[]` — 各模块健康（ok / timeout / error）
- `cards` + `cards_hint` — 声明了 `dashboard.card` 的插件提示；**真卡片由前端 SlotHost 渲染**

## 聚合策略（本卡技术要点）

1. `aggregator.py` 用 `asyncio.gather` **并行**调各模块 HTTP API。
2. **每个子调用超时 800ms**（`CALL_TIMEOUT_MS`），超时 → `{"status":"timeout"}`。
3. 失败 → `{"status":"error", "detail": "..."}`。
4. **任何一个模块挂掉都不能让 overview 500** —— 对应卡片显示「该模块暂不可用」，其余照常。
5. 上游路径零硬编码数字：计数全部从上游响应字段读出。

## 环境变量

| 变量 | 默认 | 说明 |
|:--|:--|:--|
| `DASHBOARD_SELF_BASE` | `http://127.0.0.1:8000` | 同机 Alongside API 根地址 |
| `DASHBOARD_SELF_TOKEN` | （空） | 内网自调 token；空则复用 core 签发的 admin JWT |

## 测试

`services/api/tests/test_dashboard.py` **mock aggregator 的 HTTP 客户端**
（`modules.dashboard.aggregator.set_fetch_override`），测试进程不需要真起 8000。

```bash
.venv/Scripts/python.exe -m pytest tests/test_dashboard.py -q
```

## 改这个插件时不许做的事

1. 不许 import 别的插件 —— 走 HTTP / 事件总线 / 扩展点
2. 不许 join 别人的表 —— 本插件甚至没有自己的表
3. 不许自己写登录 —— 内核统一注入 `Authorization: Bearer <jwt>`
4. 不许写死颜色 —— 只用设计令牌（`var(--accent)` 之类）
5. 不许改 `kernel/` 或 `core/`
6. 不许在概览里写死任何统计数字

## 未做（最小闭环边界）

- 完整三轴成长罗盘（条目 CRUD / 进度 / 「暂时搁置」状态）
- PATCH `growth/items/{id}`
- 面板拖拽排序持久化
- 真实 HTTP e2e（起 8000 聚合实测）
- 事件订阅增量刷新（consumes 已在 manifest 声明，前端整页重拉）
