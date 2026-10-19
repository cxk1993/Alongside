# 网页工作台（插件 id：`web`）· T19

> **一句话**：在 Alongside 里打开**任意网页**、固定成快捷入口，并为它声明 **API/MCP 能力**。
> ★ 它是「能力目录」（T20）的**输入端** —— 本插件负责**录入**能力条目，T20 负责**汇总与对外暴露**。

| 项 | 值 |
|:--|:--|
| id | `web` |
| kind | `builtin` |
| API 前缀 | `/api/v1/web` |
| 表名前缀 | `web_` |
| 迁移目录 | `api/migrations/` |

---

## ★ 核心判据：本插件**不认识任何具体站点**

任何外部服务（记账、笔记、复盘…）都只是 `web_entry` 表里的一行数据。
**判据（可机械验证）**：

```bash
grep -rniE "beecount|obsidian|work-review" \
  apps/web/src/apps/web services/api/modules/web \
  --include="*.ts" --include="*.tsx" --include="*.py" --include="*.css" --include="*.json"
# 必须 0 命中
```

写代码时若想"顺手支持一下某个站点"，**那是把业务塞进插件** —— 应该改成往表里加一行。

---

## API（8 个端点）

| 方法 | 路径 | 说明 |
|:--|:--|:--|
| `GET` | `/health` | 内核据此判断能力是否可用（**每个插件都必须有**） |
| `GET` | `/manifest` | 模块清单（前端 / AI 发现能力用） |
| `GET` | `/entries` | 列表；`?enabled=true` 只看启用的；按 `order` 升序 |
| `POST` | `/entries` | 新建（201） |
| `GET` | `/entries/{id}` | 详情 |
| `PATCH` | `/entries/{id}` | 改（含 `enabled` 开关；只改传了的字段） |
| `DELETE` | `/entries/{id}` | 删（204） |
| `POST` | `/entries/{id}/touch` | 记一次打开（v0.1 只回时间戳，便于以后加使用统计） |

**状态码约定**（跟项目走）：校验类错误 **422**（`core.errors.ValidationError`）、
slug 冲突 **409**、找不到 **404**、未鉴权 **401**。

---

## 三处**必须守住**的校验（都在后端，前端只是第一道）

| # | 规则 | 为什么 |
|:--:|:--|:--|
| 1 | `url` **只允许 `http` / `https`** | 拒 `javascript:` / `data:` —— 防注入 |
| 2 | `auth_ref` 只收 `"none"` 或 `"<pat\|bearer\|basic\|token>:env:<大写变量名>"` | ★ **绝不存明文凭据**；形状不符一律拒，含"疑似贴了 token"的启发式拦截 |
| 3 | `kind` 非 `web` 时必须给 `endpoint` | 声明了能力却没地方调 = 骗 agent |

实现位置：`schema.py` 的 `normalize_url` / `normalize_auth_ref` / `normalize_kind`。

> **`token:` 这一种是给谁用的**：有些自建服务认 query 参数（如 pi-web-ui 的 `?token=…`），
> 而不是 `Authorization` 头。`token:env:PI_WEB_TOKEN` 会在内嵌时拼成 `?token=<值>`。
> 四种形态共用同一条纪律：**表里只存变量名，真值永远在 `.env`**。

---

## ★ 与 T20 的接口契约（**改字段名要同步 T20**）

`service._capability()` 产出的形状（`GET /entries` 里每条都带 `capability` 字段）：

```json
{
  "id": "example-portal", "name": "示例门户", "kind": "web+mcp",
  "url": "https://example.invalid/app",
  "endpoint": "http://127.0.0.1:9999/api/v1/mcp",
  "auth_ref": "pat:env:EXAMPLE_TOKEN",
  "capabilities": ["thing.read"],
  "enabled": true, "note": null,
  "source": "web_entry"
}
```

- `source` 三值：`web_entry`（本卡）／`plugin`（T20 自动生成）／`kernel`（T20 内核基础）
- 字段集由 **pytest `test_capability_shape_matches_t20`** 直接断言与
  `schema.CapabilityEntry.model_fields` 相等 —— **改这里，那条测试会红**。
- **T20 直接消费它，不要另定义一套字段名。**

---

## 前端

| 文件 | 作用 |
|:--|:--|
| `index.tsx` | 插件入口，`default { manifestId: "web", Component }` |
| `WebApp.tsx` | 主界面：左栏入口 + 右侧内嵌；页签「浏览 / 管理入口」 |
| `WebFrame.tsx` | 内嵌窗口（工具条 + 超时兜底 + 常驻退路）；★ 带 `auth_ref` 的条目先调 `frame-url` 换真地址 |
| `WebEntriesPanel.tsx` | 入口管理：增删改 / 开关 / **能力声明表单 + catalog 形状预览** |
| `api.ts` | 请求层 + `webKeys` 查询键 |
| `web.css` | 样式，**只用设计令牌** |

### ★ 凭据注入链路（2026-10-10 补全）

条目里的 `auth_ref` 是**引用**，不是值。真正拼进 iframe 地址的是后端：

```
条目        auth_ref = "token:env:PI_WEB_TOKEN"
                    ↓  （iframe 渲染前调用）
后端        GET /api/v1/web/entries/{id}/frame-url
                    ↓  （从 os.environ 取 PI_WEB_TOKEN）
返回        { url: "https://…/pi/?token=<真值>", parsed: true }
                    ↓
前端        <iframe src={真值}>   ← 凭据只进 iframe，不进界面/剪贴板
```

- 没填 `auth_ref` 的条目：**不发这个请求**，直接用 `entry.url`（老条目零变化）。
- `.env` 里缺该变量 → 前端显示「凭据没能解析」+ 变量名，**不静默白屏**。
- 工具栏显示的地址会**去掉 query**（只留 origin + path），防 token 出现在界面上。

### ⚠️ 一个**已知限制**（不是 bug）

**浏览器拿不到跨域 iframe 被 `X-Frame-Options` / CSP 拒绝的信号**
（被拒时 `onLoad` 照样触发、`contentDocument` 跨域读不到）。
**故意不做"服务端预探测"**：那等于让后端去 fetch 用户填的任意 URL → **SSRF 口子**，
与"内嵌任意网页"的白名单需求直接冲突。

→ 采用 **6s 超时兜底 + 底部常驻「在新窗口打开」**，保证任何情况下都不是死路。
代价：被拒站点若加载很快，会先看到空白（但有常驻提示可点）。

### 置顶 / 固定几何**故意没做**

`WebFrame` 里那两个按钮是 `disabled` 的**接口预留**，属 **T22 窗口能力**（地基改造）的领地。

---

## 改这个插件时不许做的事

1. 不许 import 别的插件 —— 走事件总线 / 对方公开 API / 扩展点
2. 不许 join 别人的表 —— 要数据就调对方的 API，并在 `manifest.requires` 里声明
3. 不许自己写登录 —— 内核统一注入 `Authorization: Bearer <jwt>`
4. 不许写死颜色 —— 只用设计令牌（`var(--accent)` 之类）
5. 不许改 `kernel/` 或 `core/` —— 那是框架的 bug，去 `docs/issues/` 写卡
6. ★ **不许硬编码任何具体站点**（见本文开头判据）
7. ★ **不许存明文凭据** —— `auth_ref` 只收 `env:` 引用，凭据本体放 `.env`
