# 复盘（插件 id：`review`）

Work-Review **只读**接入：把本机工作日报接进 Alongside，形成可回看、可趋势对比的复盘视图。

| 项 | 值 |
|:--|:--|
| id | `review` |
| kind | `builtin` |
| API 前缀 | `/api/v1/review` |
| 表名前缀 | `review_` |
| 表 | `review_daily`（日落库）/ `review_note`（主人批注） |
| 迁移目录 | `modules/review/migrations/` |

## 环境变量（凭据只走 env，不进代码/git）

| 变量 | 默认 | 说明 |
|:--|:--|:--|
| `REVIEW_UPSTREAM` | `mock` | `mock` 内置假数据（离线测试全绿）；`api` 打真上游 |
| `WORK_REVIEW_BASE_URL` | `http://127.0.0.1:49996` | 上游根地址（★ 端口 49996，不是 47831） |
| `WORK_REVIEW_TOKEN` | （api 模式必填） | Bearer token，从 `localhost_api_token.txt` 放进 `.env` |
| `WORK_REVIEW_BRIDGE` | `false` | `true` 时失败语义为「桥离线」，超时 ≤3s |

## API

| 方法 | 路径 | 说明 |
|:--|:--|:--|
| GET | `/health` | 透传上游探活（mock 返回 version 1.0.56） |
| GET | `/manifest` | 插件清单 |
| GET | `/source` | 取数来源：mock/direct/bridge + 在线状态 + 最后同步 |
| GET | `/days?from=&to=&page=&size=` | 日期列表（分页，date 倒序） |
| GET | `/day?date=` | 单日结构（五块 + AI + 批注 + source） |
| GET | `/trend?metric=&days=` | 趋势（total/category/app） |
| GET | `/compare?date=&against=` | 两日对比 + 自然语言结论 |
| GET | `/weekly?date=` | 周报（代理上游，短时缓存；不落库） |
| GET | `/raw?date=` | 原始日报 Markdown |
| GET | `/notes?date=` | 批注列表 |
| POST | `/ingest?date=` | 拉取某日写入 Alongside 库（幂等；对上游只读） |
| POST | `/notes` | 写自己的批注 |

事件：`review.day.ingested`

## 铁律

1. **不向 Work-Review 做任何写 HTTP**；POST 只写 Alongside 自己的库。
2. token 不进代码/前端/报告/git。
3. 本卡不实现桥/frp 传输；`WORK_REVIEW_BRIDGE=true` 仅影响错误语义。
4. `raw_path` 是来源标识 `work-review:<date>`，**不是文件路径**。
5. 不许 import 别的插件；不许改 `core/**`、`db/**`、`services/bridge/**`。
6. `contracts/data-dictionary.md` 不改别人的行；`raw_md`/`review_note` 登记需求见报告或 docs/issues。

## 测试

```bash
# 在 services/api 下
set REVIEW_UPSTREAM=mock
set DB_PATH=./data/tmp_t09.db
.venv\Scripts\python.exe -m pytest tests/test_review.py -q
```
