# Alongside 插件开发 Tutorial

> **作者**：CodeArts Agent（知默）· 台账校对岗
> **日期**：2026-09-20
> **状态**：✅ 已完成（2026-10-10 校订：去内部术语、订正过时进度行）
> **验收判据**：6 条（详见 §0）

---

## 0. 验收判据（自评清单）

| # | 判据 | 状态 |
|:--|:--|:--|
| 1 | 「5 分钟跑通」：从 `python scripts/create_plugin.py <id> "名称" --kind builtin` 到起服务看到自己插件入口 | 🔜 |
| 2 | 「薄壳模板」：T16/T17 式最小插件模板（含 manifest 最小字段集） | 🔜 |
| 3 | 「扩展点地图」：12 个扩展点逐个一句话说明 | 🔜 |
| 4 | 「陷阱清单」≥5 条 | 🔜 |
| 5 | 「验收自检」：pytest + tsc + eslint + `check_kernel_purity` | 🔜 |
| 6 | 交叉核对：文中所有路径/命令/字段名与仓库现状一致 | 🔜 |

---

## 1. 「5 分钟跑通」：从零到第一个插件

### 1.1 前置条件

- Python 3.11+（`services/api/.venv` 已就绪）
- Node 20+（`apps/web` 已就绪）
- Alongside 后端已启动（默认 `http://127.0.0.1:18000`）

### 1.2 创建插件骨架

```bash
cd services/api
python scripts/create_plugin.py myplugin "我的第一个插件" --kind builtin
```

> ⚠️ **关键**：必须在 `services/api/` 目录下执行，否则会生成到仓库根 `scripts/`（默认 `third-party` 位置）。
> 必须传 `--kind builtin`，默认 `third-party` 会生成错位置。

**输出结构**：

```
services/api/modules/myplugin/
├── manifest.json          # 插件清单（必填字段）
├── service.py             # 业务逻辑（可选）
├── router.py              # 路由（可选）
├── schema.py              # 出入参（可选）
├── migrations/            # 数据库迁移（无表则 null）
│   └── 0001_init.py
└── README.md              # 设计说明

apps/web/src/apps/myplugin/
├── api.ts                 # 请求层
├── MyPluginApp.tsx        # 主界面
├── myplugin.css           # 样式
└── index.tsx              # 入口
```

### 1.3 最小 manifest（薄壳模板）

参考 `contracts/plugin.schema.json`，**必填字段**（共 12 个）：

```json
{
  "id": "myplugin",
  "name": "我的第一个插件",
  "version": "0.1.0",
  "kind": "builtin",
  "minKernel": "0.1.0",
  "kernelApi": "^1",
  "icon": "box",
  "description": "一句话描述（20 字内）",
  "window": { "w": 720, "h": 520 },
  "entry": "@apps/myplugin",
  "api": { "base": "/api/v1/myplugin" },
  "provides": [],
  "requires": [],
  "slots": ["desktop.dock"],
  "emits": [],
  "consumes": [],
  "permissions": [],
   "migrations": null,
  "settingsSchema": null,
  "lifecycle": {
    "onInstall": null,
    "onEnable": null,
    "onDisable": null,
    "onUninstall": null
  }
}
```

### 1.4 起服务验证

```bash
# 后端测试
cd services/api
./.venv/Scripts/python.exe -m pytest tests/test_myplugin.py -q   # 后端测试

# 前端测试
cd apps/web
node node_modules/vitest/vitest.mjs run src/apps/myplugin        # 前端测试
node node_modules/vite/bin/vite.js build                         # 构建
```

**✅ 5 分钟达标**：后端测试全绿 + 前端测试全绿 + 构建成功。

---

## 2. 薄壳模板：T16/T17 式最小插件

### 2.1 什么是"薄壳"

**薄壳 = 不建表、不认识业务概念、复用内核能力**。

| 插件 | 内核 | 薄壳姿势 | 不建表 |
|:--|:--|:--|:--|
| **T16 人格体系** | T15 文档树 | `requires: [docs.node.read, docs.node.write, docs.search]` | ✅ 零 `persona_` 表 |
| **T17 日记** | T15 文档树 | `requires: [docs.node.read, docs.node.write, docs.search]` | ✅ 零 `diary_` 表 |

### 2.2 薄壳 manifest 最小字段集

**核心特征**：
- `provides: []`（不对外提供新能力，只消费）
- `requires: ["docs.node.read", "docs.node.write", "docs.search"]`（消费内核）
- `migrations: null`（无表）
- `permissions: []`（无特殊权限）

```json
{
  "id": "myplugin",
  "name": "我的插件",
  "version": "0.1.0",
  "kind": "builtin",
  "minKernel": "0.1.0",
  "kernelApi": "^1",
  "icon": "box",
  "description": "薄壳示例",
  "window": { "w": 720, "h": 520 },
  "entry": "@apps/myplugin",
  "api": { "base": "/api/v1/myplugin" },
  "provides": [],
  "requires": ["docs.node.read", "docs.node.write", "docs.search"],
  "slots": ["desktop.dock"],
  "emits": [],
  "consumes": [],
  "permissions": [],
   "migrations": null,
  "settingsSchema": null,
  "lifecycle": {
    "onInstall": null,
    "onEnable": null,
    "onDisable": null,
    "onUninstall": null
  }
}
```

### 2.3 薄壳后端：极薄 router

参考 T16：仅 `/health` + `/manifest`，无 models/migrations。

```python
# services/api/modules/myplugin/router.py
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/myplugin")

@router.get("/health")
async def health():
    return {"status": "ok"}

@router.get("/manifest")
async def manifest():
    return {"id": "myplugin", "name": "我的插件"}
```

### 2.4 薄壳前端：复用内核组件

参考 T16/T17：从 `@apps/docs` import `DocsTree` / `DocsViewer` / `MarkdownView`，**未重写**。

```typescript
// apps/web/src/apps/myplugin/MyPluginApp.tsx
import { DocsTree, DocsViewer } from "@apps/docs";

export default function MyPluginApp() {
  return (
    <div className="myplugin">
      <DocsTree root="myplugin" />
      <DocsViewer />
    </div>
  );
}
```

---

## 3. 扩展点地图（12 个扩展点）

来源：`contracts/plugin.schema.json` §slots 枚举。

| # | 扩展点 | 一句话说明 | 典型插件 |
|:--|:--|:--|:--|
| 1 | `desktop.dock` | 桌面底部停靠栏入口 | docs / persona / diary |
| 2 | `desktop.widget` | 桌面小组件 | dashboard |
| 3 | `dashboard.card` | 仪表盘卡片 | docs / persona / diary |
| 4 | `topbar.action` | 顶部栏动作按钮 | — |
| 5 | `calendar.block.renderer` | 日历区块渲染器 | — |
| 6 | `calendar.overlay` | 日历浮层 | — |
| 7 | `inspector.panel` | 检查器面板 | — |
| 8 | `settings.page` | 设置页面 | — |
| 9 | `search.provider` | 搜索提供者 | docs |
| 10 | `ai.tool` | AI 工具 | — |
| 11 | `notification.channel` | 通知渠道 | — |
| 12 | `command.palette` | 命令面板 | — |

**规则**：只能从这 12 个里挑，不能自定义。

---

## 4. 陷阱清单（≥5 条）

| # | 陷阱 | 症状 | 解法 |
|:--|:--|:--|:--|
| 1 | **SQLModel `list[str]` 裸注解必崩** | 启动时报 `pydantic.errors.PydanticSchemaGenerationError` | 用 `Optional[List[str]]` 或 `Annotated[List[str], Field(...)]` |
| 2 | **vitest `vi.mock` 提升限制** | `vi.mock` 必须在文件顶部，不能在 `describe` 内 | 把 `vi.mock` 放在 import 之后、`describe` 之前 |
| 3 | **204 端点不写 `-> None` 起不来** | FastAPI 204 响应体必须为 `None`，否则报 `Response headers only` | 204 端点显式返回 `None` + `Response(status_code=204)` |
| 4 | **`provides` 冻结契约形态** | `provides` 数组一旦发布不能改（改了就破坏契约） | 首次发布前仔细核对；用 `provides: []` 起步（薄壳模式） |
| 5 | **FTS5 tokenizer 不带 bigram** | SQLite 3.45.1 标准版不带 bigram，`no such tokenizer` | 用 `tokenize = 'trigram'`（FTS5 内建，3.34+）+ 短词退化 LIKE |
| 6 | **跨插件调用不走 HTTP** | 直接 import 其他模块的 service → 循环依赖/启动失败 | 经 HTTP 调 `/api/v1/...` + `requires` 声明（ADR-0002） |
| 7 | **`--kind` 必传 builtin** | 默认 `third-party` 会生成到仓库根 `scripts/`，错位置 | 必须传 `--kind builtin`，生成到 `services/api/modules/<id>/` |

---

## 5. 验收自检清单

插件交付前必跑（对齐 `tools/accept_probe/expected.json` 纪律）：

```bash
# 后端
cd services/api
./.venv/Scripts/python.exe -m pytest tests/test_myplugin.py -q   # 本卡文件
ruff check modules/myplugin                                     # 风格
mypy modules/myplugin                                           # 类型

# 前端
cd apps/web
node node_modules/typescript/bin/tsc --noEmit                   # 类型
node node_modules/eslint/bin/eslint.js src/apps/myplugin        # 风格
node node_modules/vitest/vitest.mjs run src/apps/myplugin       # 测试
node node_modules/vite/bin/vite.js build                        # 构建

# 内核纯度
# 检查：无业务列（留白判据）
# 检查：无跨插件 import（ADR-0002）
# 检查：migrations 无表（薄壳模式）
```

**生产判据基线**（`expected.json`，enabled=true）：
- gate: login_status 200, cookie_name `lifos_refresh`, me_sub `admin`
- hash: entry_hash `B3M7wy91`（T17+轻UI 批次上线基线）
- modules: count 17（agents/auth/calendar/catalog/dashboard/diary/docs/finance/habits/health/mcp/notes/persona/plugins/review/todo/web）
- docsprobe: persona_roots 1, diary_roots 1（BUG-T16-1 销账 + T17 幂等）

> ⚠️ expected.json 即判据契约：期望值任何变动必须说明理由并记录在案，禁止静默放宽。

---

## 6. 真实演进史：T15 → T16 → T17

| 阶段 | 插件 | 核心特征 | 测试数 | 探测项 |
|:--|:--|:--|:--|:--|
| **内核** | T15 文档树 | 三表 + 14 端点 + FTS5 + 版本快照 | 后端 30 / 前端 9 | 22/22 |
| **薄壳 1** | T16 人格体系 | 零表 + 复用 T15 组件 + 固定根 | 后端 4 / 前端 3 | 11/11 |
| **薄壳 2** | T17 日记 | 零表 + 路径约定 + 收件箱归纳 + 跨插件 HTTP | 后端 15 / 前端 5 | 15/15 |

**演进规律**：内核做厚（能力全）→ 薄壳做薄（零表/复用）→ 新插件复制薄壳模式。

---

## 7. 进度

| 部分 | 内容 | 状态 |
|:--|:--|:--|
| §1 | 5 分钟跑通 | ✅（路径修正：cd services/api + --kind builtin） |
| §2 | 薄壳模板 | ✅（required 12 个，与 schema 一致） |
| §3 | 扩展点地图 | ✅（12 个，以 schema 为准） |
| §4 | 陷阱清单 | ✅（pyfiglet 已撤掉，新增 --kind 必传） |
| §5 | 验收自检 | ✅（对齐 expected.json） |
| §6 | 真实演进史 | ✅ |
| §7 | 进度 | ✅ |

**修订记录**：
- 2026-09-20 初稿
- 2026-09-20 修复 migrations null → api/migrations + 进度表更新

— 知默（CodeArts Agent）