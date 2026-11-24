<!-- 本文件由 tools/gen_protocol_doc.py 从真实 schema 与真源码生成，请勿手改。 -->
<!-- 改协议请改 contracts/plugin.schema.json，然后重跑生成脚本。 -->

# Alongside 插件协议 · 契约 + 代码本体

> **本文档是协议本身。**
>
> 契约真相源：`contracts/plugin.schema.json`（625 行，37 个顶层字段）
> 代码真相源：§5 逐字内嵌的九份文件（由脚本从真源码取，不手抄）
>
> 协议的**语义与规则**在《Alongside 插件协议技术报告 v1.1》里；
> 本文档回答两件事：**契约规定了哪些字段**（§1–§4）与
> **协议在代码里长什么样**（§5，含校验器、接入点、门禁与外部裁判）。
>
> 文档与 schema / 源码冲突时，**以代码为准**，并重跑本脚本。
>
> ★ 本文档**不写生成时间**：时间戳会让同一份 schema 生成出不同字节，
> `--check` 就永远对不上。字段数、必填数全部**实数**，不写死。

---

## 1 · 契约元信息

| 项 | 值 |
|---|---|
| 标题 | Alongside 插件清单（manifest）契约 |
| `$schema` | `https://json-schema.org/draft/2020-12/schema` |
| `$id` | `https://alongside/contracts/plugin.schema.json` |
| 顶层类型 | `object` |
| 顶层字段数 | **37** |
| 必填字段数 | **11** |
| 额外字段 | 禁止（`additionalProperties: false`） |
| 放行模式 | `^x_` |

> 总纲 §1.3.1 插件清单全部字段。任何插件（core / builtin / third-party）都必须满足本契约。内核用本文件强校验，错误精确到字段。

---

## 2 · 必填集

`required` 共 **11** 项（读自 schema，不是手抄）：

```
id
name
version
kind
kernelApi
provides
requires
slots
emits
consumes
permissions
```

**条件必填**（`allOf` 里的 `if → then.required`）：

| 条件 | 追加必填 | 说明 |
|---|---|---|
| {"allOf": [{"properties": {"kind": {"not": {"const": "container"}}}}, {"not": {"properties": {"entry": {"const": ""}, "provides": {"maxItems": 0}}, "required": ["skin", "entry", "provides"]}}]} | `api` | ★ 路径 a：可省 api 的插件只有两类 —— ① container（纯前端编排容器）；② 「纯皮肤插件」：声明了 skin、entry 为空串、provides 为空。后者没有接口可挂，逼它写一个假 api 只会挂出 404 路由（声明与实现不符）。其余 kind 必须声明 api。 |

> ★ 条件必填**不进** `required` 数组：它只在条件成立时生效，
> 所以上面第 2 节的必填集与这里的追加项要分开看。

---

## 3 · 字段分组表

全部 **37** 个顶层字段，按阅读顺序分组。
**分组只是阅读顺序，不是判据**——判据永远是 schema 本身。

| 组 | 字段数 | 字段 |
|---|---:|---|
| **身份与版本** | 6 | `id`、`name`、`version`、`kind`、`minKernel`、`kernelApi` |
| **展示、入口与 API** | 8 | `icon`、`description`、`author`、`window`、`entry`、`api`、`tabs`、`absorbs` |
| **能力缝（定义 / 提供 / 依赖）** | 4 | `defines`、`provides`、`requires`、`optionalDependencies` |
| **扩展点与事件** | 4 | `slots`、`emits`、`consumes`、`activates_on` |
| **权限、风险与执行** | 3 | `permissions`、`riskLevel`、`execution` |
| **副作用、状态与迁移** | 3 | `sideEffects`、`track`、`migrations` |
| **来源与并发** | 2 | `origin`、`concurrency` |
| **调度与输出** | 2 | `schedule`、`outputPolicy` |
| **设置与生命周期** | 3 | `settingsSchema`、`lifecycle`、`deactivation` |
| **扩展位** | 1 | `extensions` |
| **未分组** | 1 | `skin` |
| **合计** | **37** | |

### 3.1 身份与版本（6 个字段）

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|:--:|:--:|---|---|
| `id` | `string` | ✓ | pattern: ^[a-z][a-z0-9-]*$ | 全局唯一，小写英文，等于目录名、等于 URL 前缀。 |
| `name` | `string` | ✓ | minLength=1 | 界面显示的中文名。 |
| `version` | `string` | ✓ | pattern: ^\d+\.\d+\.\d+$ | 三段式语义化版本。 |
| `kind` | `string` | ✓ | enum: core / builtin / third-party / container | core 不可禁用不可卸载； |
| `minKernel` | `string` |  | pattern: ^\d+\.\d+\.\d+$ | 要求的最低内核版本。 |
| `kernelApi` | `string` | ✓ | pattern: ^\^?\d+(\.\d+)?(\.\d+)?$ | 要求的内核接口版本（如 ^1）。 |

### 3.2 展示、入口与 API（8 个字段）

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|:--:|:--:|---|---|
| `icon` | `string` |  | — | 内核语义名（取值见 apps/web/src/kernel/icons/default-set.tsx 的键集合； |
| `description` | `string` |  | — | 一句话描述（20 字内）。 |
| `author` | `string` |  | — | — |
| `window` | `object` |  | 需: w, h；不许额外字段 | — |
| `entry` | `string` |  | pattern: ^(?:\|@/apps/[a-z][a-z0-9-]*\|/plugin-assets/[a-z][a-z0-9-]*/(?!\.\.(?:/\|$))(?!.*/\.\.(?:/\|$))[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\|https?://[^\s]+\|//[^/\s][^\s]*)$ | 前端入口，三种语义：① 空串 = 纯后端插件，没有前端 UI（ai-chat / summary / auth / countdown 等）； |
| `api` | `object` |  | 需: base；不许额外字段 | — |
| `tabs` | `array` |  | array<object> | ★ 路径 a：容器型模块（kind=container）的页签声明。 |
| `absorbs` | `array` |  | array<string> | 可选：被本容器吸收、不再单独出现在底端栏的模块 id 列表。 |

### 3.3 能力缝（定义 / 提供 / 依赖）（4 个字段）

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|:--:|:--:|---|---|
| `defines` | `array` |  | array<string> | 本插件**定义**（声明所有权）的能力，格式 <id>.<名词>.<动作>。 |
| `provides` | `array` | ✓ | array<string> | 本插件对外提供的能力，格式 <id>.<名词>.<动作>（至少一段点分）。 |
| `requires` | `array` | ✓ | array<string> | 本插件需要别人提供的能力；没写的能力调用会被拒。 |
| `optionalDependencies` | `array` |  | array<string> | 软依赖：缺了不阻塞启动，降级并记 reason_code。 |

### 3.4 扩展点与事件（4 个字段）

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|:--:|:--:|---|---|
| `slots` | `array` | ✓ | array<string[desktop.dock / desktop.dock-left / desktop.dock-right / desktop.widget]> | 本插件要挂到的扩展点（只能从已知 15 个里挑； |
| `emits` | `array` | ✓ | array<string> | 本插件会发出的事件，格式 <id>.<名词>.<过去式>。 |
| `consumes` | `array` | ✓ | array<string> | 本插件会监听的事件。 |
| `activates_on` | `array` |  | array<string> | 声明式激活事件。 |

### 3.5 权限、风险与执行（3 个字段）

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|:--:|:--:|---|---|
| `permissions` | `oneOf×2` | ✓ | oneOf: array ｜ object(3 字段) | 权限声明：支持旧数组格式（向后兼容）或新对象格式（声明式）。 |
| `riskLevel` | `string` |  | enum: L0 / L1 / L2 / L3 | 风险分级。 |
| `execution` | `object` |  | 不许额外字段 | 执行约束（沙箱 + 审批）。 |

### 3.6 副作用、状态与迁移（3 个字段）

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|:--:|:--:|---|---|
| `sideEffects` | `object` |  | 不许额外字段 | 副作用声明。 |
| `track` | `string` |  | enum: persistent / ephemeral | 状态跟踪方式。 |
| `migrations` | `string\|null` |  | — | 自带迁移目录（相对插件根）。 |

### 3.7 来源与并发（2 个字段）

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|:--:|:--:|---|---|
| `origin` | `object` |  | 不许额外字段 | 来源与安装者。 |
| `concurrency` | `object` |  | 不许额外字段 | 并发策略。 |

### 3.8 调度与输出（2 个字段）

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|:--:|:--:|---|---|
| `schedule` | `object` |  | 不许额外字段 | 定时任务声明。 |
| `outputPolicy` | `object` |  | 不许额外字段 | 大输出处理。 |

### 3.9 设置与生命周期（3 个字段）

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|:--:|:--:|---|---|
| `settingsSchema` | `string\|null` |  | — | 设置 JSON Schema 路径；无设置则写 null。 |
| `lifecycle` | `string\|null` |  | enum: hooks / None | 可选：本插件是否提供生命周期钩子。 |
| `deactivation` | `string` |  | enum: manual / auto | 依赖丧失时的处置策略：manual（默认）= 只有人为 disable 才停用； |

### 3.10 扩展位（1 个字段）

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|:--:|:--:|---|---|
| `extensions` | `object` |  | — | 扩展位。 |

### 3.11 未分组（1 个字段）

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|:--:|:--:|---|---|
| `skin` | `string` |  | pattern: ^(?!.*\.\.(?:/\|$))[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\.json$ | 皮肤文件指针（插件目录内的相对路径，如 skin.json）。 |

---

## 4 · 机器可读摘要

给门禁/脚本读的那一份。字段顺序 = schema 书写顺序；
`field_count` / `required_count` 由 schema 实数得出。

````json
{
  "source": "contracts/plugin.schema.json",
  "schema_lines": 625,
  "title": "Alongside 插件清单（manifest）契约",
  "field_count": 37,
  "required_count": 11,
  "required": [
    "id",
    "name",
    "version",
    "kind",
    "kernelApi",
    "provides",
    "requires",
    "slots",
    "emits",
    "consumes",
    "permissions"
  ],
  "conditional_required": [
    {
      "when": "{\"allOf\": [{\"properties\": {\"kind\": {\"not\": {\"const\": \"container\"}}}}, {\"not\": {\"properties\": {\"entry\": {\"const\": \"\"}, \"provides\": {\"maxItems\": 0}}, \"required\": [\"skin\", \"entry\", \"provides\"]}}]}",
      "required": [
        "api"
      ],
      "description": "★ 路径 a：可省 api 的插件只有两类 —— ① container（纯前端编排容器）；② 「纯皮肤插件」：声明了 skin、entry 为空串、provides 为空。后者没有接口可挂，逼它写一个假 api 只会挂出 404 路由（声明与实现不符）。其余 kind 必须声明 api。"
    }
  ],
  "additionalProperties": false,
  "patternProperties": [
    "^x_"
  ],
  "groups": [
    {
      "name": "身份与版本",
      "fields": [
        "id",
        "name",
        "version",
        "kind",
        "minKernel",
        "kernelApi"
      ]
    },
    {
      "name": "展示、入口与 API",
      "fields": [
        "icon",
        "description",
        "author",
        "window",
        "entry",
        "api",
        "tabs",
        "absorbs"
      ]
    },
    {
      "name": "能力缝（定义 / 提供 / 依赖）",
      "fields": [
        "defines",
        "provides",
        "requires",
        "optionalDependencies"
      ]
    },
    {
      "name": "扩展点与事件",
      "fields": [
        "slots",
        "emits",
        "consumes",
        "activates_on"
      ]
    },
    {
      "name": "权限、风险与执行",
      "fields": [
        "permissions",
        "riskLevel",
        "execution"
      ]
    },
    {
      "name": "副作用、状态与迁移",
      "fields": [
        "sideEffects",
        "track",
        "migrations"
      ]
    },
    {
      "name": "来源与并发",
      "fields": [
        "origin",
        "concurrency"
      ]
    },
    {
      "name": "调度与输出",
      "fields": [
        "schedule",
        "outputPolicy"
      ]
    },
    {
      "name": "设置与生命周期",
      "fields": [
        "settingsSchema",
        "lifecycle",
        "deactivation"
      ]
    },
    {
      "name": "扩展位",
      "fields": [
        "extensions"
      ]
    },
    {
      "name": "未分组",
      "fields": [
        "skin"
      ]
    }
  ],
  "fields": [
    {
      "name": "id",
      "type": "string",
      "required": true,
      "pattern": "^[a-z][a-z0-9-]*$"
    },
    {
      "name": "name",
      "type": "string",
      "required": true,
      "minLength": 1
    },
    {
      "name": "version",
      "type": "string",
      "required": true,
      "pattern": "^\\d+\\.\\d+\\.\\d+$"
    },
    {
      "name": "kind",
      "type": "string",
      "required": true,
      "enum": [
        "core",
        "builtin",
        "third-party",
        "container"
      ]
    },
    {
      "name": "minKernel",
      "type": "string",
      "required": false,
      "pattern": "^\\d+\\.\\d+\\.\\d+$"
    },
    {
      "name": "kernelApi",
      "type": "string",
      "required": true,
      "pattern": "^\\^?\\d+(\\.\\d+)?(\\.\\d+)?$"
    },
    {
      "name": "icon",
      "type": "string",
      "required": false
    },
    {
      "name": "description",
      "type": "string",
      "required": false
    },
    {
      "name": "author",
      "type": "string",
      "required": false
    },
    {
      "name": "window",
      "type": "object",
      "required": false,
      "required_sub": [
        "w",
        "h"
      ],
      "properties": [
        "w",
        "h",
        "minW",
        "minH",
        "singleton",
        "x",
        "y"
      ]
    },
    {
      "name": "entry",
      "type": "string",
      "required": false,
      "pattern": "^(?:|@/apps/[a-z][a-z0-9-]*|/plugin-assets/[a-z][a-z0-9-]*/(?!\\.\\.(?:/|$))(?!.*/\\.\\.(?:/|$))[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*|https?://[^\\s]+|//[^/\\s][^\\s]*)$"
    },
    {
      "name": "skin",
      "type": "string",
      "required": false,
      "pattern": "^(?!.*\\.\\.(?:/|$))[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\\.json$"
    },
    {
      "name": "api",
      "type": "object",
      "required": false,
      "required_sub": [
        "base"
      ],
      "properties": [
        "base",
        "openapi",
        "health",
        "tools"
      ]
    },
    {
      "name": "tabs",
      "type": "array",
      "required": false,
      "items_type": "object"
    },
    {
      "name": "absorbs",
      "type": "array",
      "required": false,
      "items_type": "string"
    },
    {
      "name": "provides",
      "type": "array",
      "required": true,
      "items_type": "string"
    },
    {
      "name": "requires",
      "type": "array",
      "required": true,
      "items_type": "string"
    },
    {
      "name": "optionalDependencies",
      "type": "array",
      "required": false,
      "items_type": "string"
    },
    {
      "name": "slots",
      "type": "array",
      "required": true,
      "items_type": "string",
      "items_enum": [
        "desktop.dock",
        "desktop.dock-left",
        "desktop.dock-right",
        "desktop.widget",
        "dashboard.card",
        "topbar.action",
        "calendar.block.renderer",
        "calendar.overlay",
        "inspector.panel",
        "settings.page",
        "search.provider",
        "ai.tool",
        "notification.channel",
        "command.palette",
        "window.sidecar"
      ]
    },
    {
      "name": "emits",
      "type": "array",
      "required": true,
      "items_type": "string"
    },
    {
      "name": "consumes",
      "type": "array",
      "required": true,
      "items_type": "string"
    },
    {
      "name": "permissions",
      "type": "oneOf×2",
      "required": true,
      "oneOf_count": 2
    },
    {
      "name": "activates_on",
      "type": "array",
      "required": false,
      "items_type": "string"
    },
    {
      "name": "migrations",
      "type": "string|null",
      "required": false
    },
    {
      "name": "settingsSchema",
      "type": "string|null",
      "required": false
    },
    {
      "name": "lifecycle",
      "type": "string|null",
      "required": false,
      "enum": [
        "hooks",
        null
      ]
    },
    {
      "name": "defines",
      "type": "array",
      "required": false,
      "items_type": "string"
    },
    {
      "name": "deactivation",
      "type": "string",
      "required": false,
      "enum": [
        "manual",
        "auto"
      ]
    },
    {
      "name": "riskLevel",
      "type": "string",
      "required": false,
      "enum": [
        "L0",
        "L1",
        "L2",
        "L3"
      ]
    },
    {
      "name": "execution",
      "type": "object",
      "required": false,
      "properties": [
        "sandbox",
        "approval"
      ]
    },
    {
      "name": "sideEffects",
      "type": "object",
      "required": false,
      "properties": [
        "external",
        "checkpoint"
      ]
    },
    {
      "name": "track",
      "type": "string",
      "required": false,
      "enum": [
        "persistent",
        "ephemeral"
      ]
    },
    {
      "name": "origin",
      "type": "object",
      "required": false,
      "properties": [
        "source",
        "signed",
        "signature",
        "installedBy"
      ]
    },
    {
      "name": "concurrency",
      "type": "object",
      "required": false,
      "properties": [
        "optimisticLock",
        "exclusive",
        "parallelSafe"
      ]
    },
    {
      "name": "schedule",
      "type": "object",
      "required": false,
      "properties": [
        "at",
        "after_seconds",
        "every_seconds",
        "daily",
        "weekly",
        "cron",
        "title",
        "catchUp"
      ]
    },
    {
      "name": "outputPolicy",
      "type": "object",
      "required": false,
      "properties": [
        "maxInlineTokens",
        "spillTo",
        "onStoreFailure"
      ]
    },
    {
      "name": "extensions",
      "type": "object",
      "required": false
    }
  ]
}
````

---

## 5 · 协议代码本体

本节是**协议本身**：九份文件逐字内嵌，内容由脚本从真源码取。
技术部拿到这份文档，不需要另外打开仓库，就能读到协议从契约到门禁的全部实现。

| # | 文件 | 角色 |
|:--:|---|---|
| 1 | `contracts/plugin.schema.json`（625 行） | ① 契约本体 |
| 2 | `services/api/core/manifest.py`（499 行） | ② 运行时数据模型 |
| 3 | `services/api/core/plugins/validate.py`（466 行） | ③ 校验器第一层（结构）+ 第二层（跨字段） |
| 4 | `services/api/core/plugins/crosscheck.py`（208 行） | ④ 校验器第三层（跨插件一致性） |
| 5 | `services/api/core/plugins/discover.py`（566 行） | ⑤ 接入点（发现与挂载） |
| 6 | `services/api/tests_protocol/test_protocol_gates.py`（358 行） | ⑥ 门禁测试网 |
| 7 | `tools/crosscheck_validator.py`（473 行） | ⑦ 校验器的外部裁判 |
| 8 | `tools/check_research_alignment.py`（373 行） | ⑧ 调研对齐核查器 |
| 9 | `services/api/tests_protocol/test_protocol_quality_gates.py`（630 行） | ⑨ 质量门禁的自检测试 |
| 10 | `services/api/core/events.py`（568 行） | ⑩ 事件契约执行点（声明即订阅 / 声明即许可） |
| 11 | `services/api/core/plugins/lifecycle.py`（179 行） | ⑪ 生命周期宣告契约（固定名钩子 + 声明校验） |

### 5.1 ① 契约本体：`contracts/plugin.schema.json`（625 行）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://alongside/contracts/plugin.schema.json",
  "title": "Alongside 插件清单（manifest）契约",
  "description": "总纲 §1.3.1 插件清单全部字段。任何插件（core / builtin / third-party）都必须满足本契约。内核用本文件强校验，错误精确到字段。",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "id",
    "name",
    "version",
    "kind",
    "kernelApi",
    "provides",
    "requires",
    "slots",
    "emits",
    "consumes",
    "permissions"
  ],
  "allOf": [
    {
      "description": "★ 路径 a：可省 api 的插件只有两类 —— ① container（纯前端编排容器）；② 「纯皮肤插件」：声明了 skin、entry 为空串、provides 为空。后者没有接口可挂，逼它写一个假 api 只会挂出 404 路由（声明与实现不符）。其余 kind 必须声明 api。",
      "if": {
        "allOf": [
          {
            "properties": {
              "kind": {
                "not": {
                  "const": "container"
                }
              }
            }
          },
          {
            "not": {
              "required": [
                "skin",
                "entry",
                "provides"
              ],
              "properties": {
                "entry": {
                  "const": ""
                },
                "provides": {
                  "maxItems": 0
                }
              }
            }
          }
        ]
      },
      "then": {
        "required": [
          "api"
        ]
      }
    }
  ],
  "properties": {
    "id": {
      "type": "string",
      "pattern": "^[a-z][a-z0-9-]*$",
      "description": "全局唯一，小写英文，等于目录名、等于 URL 前缀。"
    },
    "name": {
      "type": "string",
      "minLength": 1,
      "description": "界面显示的中文名。"
    },
    "version": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+\\.\\d+$",
      "description": "三段式语义化版本。"
    },
    "kind": {
      "type": "string",
      "enum": [
        "core",
        "builtin",
        "third-party",
        "container"
      ],
      "description": "core 不可禁用不可卸载；builtin 可禁用不可卸载；third-party 可禁用可卸载；container 为容器型模块（纯前端编排容器，如系统窗，可无 api 与 entry）。"
    },
    "minKernel": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+\\.\\d+$",
      "description": "要求的最低内核版本。"
    },
    "kernelApi": {
      "type": "string",
      "pattern": "^\\^?\\d+(\\.\\d+)?(\\.\\d+)?$",
      "description": "要求的内核接口版本（如 ^1）。不兼容时明确拒绝加载，不许静默失败。"
    },
    "icon": {
      "type": "string",
      "description": "内核语义名（取值见 apps/web/src/kernel/icons/default-set.tsx 的键集合；别名见 registry.ts 的 ALIASES；允许单个汉字/字母走字形兜底）。门禁 G19 会拦不在表里的名字。"
    },
    "description": {
      "type": "string",
      "description": "一句话描述（20 字内）。"
    },
    "author": {
      "type": "string"
    },
    "window": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "w",
        "h"
      ],
      "properties": {
        "w": {
          "type": "integer",
          "minimum": 240
        },
        "h": {
          "type": "integer",
          "minimum": 200
        },
        "minW": {
          "type": "integer",
          "minimum": 240
        },
        "minH": {
          "type": "integer",
          "minimum": 200
        },
        "singleton": {
          "type": "boolean"
        },
        "x": {
          "type": "integer"
        },
        "y": {
          "type": "integer"
        }
      }
    },
    "entry": {
      "type": "string",
      "pattern": "^(?:|@/apps/[a-z][a-z0-9-]*|/plugin-assets/[a-z][a-z0-9-]*/(?!\\.\\.(?:/|$))(?!.*/\\.\\.(?:/|$))[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*|https?://[^\\s]+|//[^/\\s][^\\s]*)$",
      "description": "前端入口，三种语义：① 空串 = 纯后端插件，没有前端 UI（ai-chat / summary / auth / countdown 等）；② 构建期别名 @/apps/<id> = UI 已编进前端树，Vite 在构建期按 glob 解析（存量 22 个内置模块与 plugins/pi-agent 都走这条，无需构建期收录之外的任何声明）；③ 运行时资产 URL = 第三方插件把 UI 产物放在插件目录的 web/dist/ 下，由后端资产端点 GET /plugin-assets/{id}/{path} 运行时服务，入口文件为 web/dist/index.js。③ 允许两种写法：根相对 /plugin-assets/<id>/index.js，或绝对/协议相对的 http(s) URL（https://... 或 //host/...）。★ 后端在组装 GET /api/v1/plugins 清单时会对第三方插件做 entry 规范化：只要插件目录下存在 web/dist/index.js，就把该项 entry 改写成 /plugin-assets/<id>/index.js —— 因此第三方插件写不写这段都可以，但写了就必须能对得上（路径里不得含 .. 段，见 pattern）。★ 前端拿到的是已规范化的值，直接 import()/动态加载，不会二次解释 @/apps 别名；非 http(s) 的 scheme（javascript: / data: / file: 等）一律拒绝 —— entry 是 import() 的入参，scheme 白名单是注入面的第一道闸。"
    },
    "skin": {
      "type": "string",
      "pattern": "^(?!.*\\.\\.(?:/|$))[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\\.json$",
      "description": "皮肤文件指针（插件目录内的相对路径，如 skin.json）。★ 这是**指针不是内容**：清单只索引，皮肤本体是独立文件，装载时用与用户导入、示例包**同一个** validateSkinPack 校验（判据不在第二处复刻）。省略 = 这个插件不带皮肤。不许 ../、不许绝对路径、必须 .json 结尾 —— 与 entry 的 /plugin-assets 穿越防护同一口径。"
    },
    "api": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "base"
      ],
      "properties": {
        "base": {
          "type": "string",
          "pattern": "^/api/v1/[a-z][a-z0-9-]*$",
          "description": "路由前缀，必须等于 /api/v1/<id>。"
        },
        "openapi": {
          "type": "string"
        },
        "health": {
          "type": "string"
        },
        "tools": {
          "type": "object",
          "additionalProperties": {
            "type": "string",
            "pattern": "^/"
          },
          "description": "ISSUE-008 方案 A：resource → 真实路由段 的显式映射（如 {\"entry\": \"/entries\"}）。键为 provides 倒数第二段；声明则 MCP 转发优先用它，未声明 resource 走机械「resource+s」推导。"
        }
      }
    },
    "tabs": {
      "type": "array",
      "description": "★ 路径 a：容器型模块（kind=container）的页签声明。一次定稿，形状与前端 SystemApp（70c0702）对接面一致：{key,label,entry?}。entry=模块 id（该页懒加载对应 App），该模块**同时被本容器吸收**（不再单独出底端栏按钮）；无 entry 的页 =「后端模块 · 仅服务」薄壳语义。",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "key",
          "label"
        ],
        "properties": {
          "key": {
            "type": "string",
            "minLength": 1,
            "description": "页签唯一键（容器内稳定标识）。"
          },
          "label": {
            "type": "string",
            "minLength": 1,
            "description": "页签显示名。"
          },
          "entry": {
            "type": "string",
            "description": "可选：该页关联的模块 id（懒加载对应 App）；省略则为「仅服务」薄壳页。"
          }
        }
      }
    },
    "absorbs": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      },
      "description": "可选：被本容器吸收、不再单独出现在底端栏的模块 id 列表。用于「本容器接管了某模块的入口」但该模块并无独立视图页的情况（如后端模块被更完整的容器取代）。声明了 tabs[].entry 的模块**不必**在此重复列出（entry 已隐含吸收）。"
    },
    "provides": {
      "type": "array",
      "items": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9-]*(\\.[a-z0-9-]+)+$"
      },
      "description": "本插件对外提供的能力，格式 <id>.<名词>.<动作>（至少一段点分）。"
    },
    "requires": {
      "type": "array",
      "items": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9-]*(\\.[a-z0-9-]+)+$"
      },
      "description": "本插件需要别人提供的能力；没写的能力调用会被拒。⚠️ 字段名拼错（如 requiers）会被当作未声明，能力一律 403 —— 请用校验器确认。"
    },
    "optionalDependencies": {
      "type": "array",
      "items": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9-]*(\\.[a-z0-9-]+)+$"
      },
      "description": "软依赖：缺了不阻塞启动，降级并记 reason_code。⚠️ 能力在时软依赖也算已授权（可直接调用）；但它不参与激活判定。与 requires 互斥。"
    },
    "slots": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": [
          "desktop.dock",
          "desktop.dock-left",
          "desktop.dock-right",
          "desktop.widget",
          "dashboard.card",
          "topbar.action",
          "calendar.block.renderer",
          "calendar.overlay",
          "inspector.panel",
          "settings.page",
          "search.provider",
          "ai.tool",
          "notification.channel",
          "command.palette",
          "window.sidecar"
        ]
      },
      "description": "本插件要挂到的扩展点（只能从已知 15 个里挑；desktop.dock-left/right 为 U3 桌面级侧栏，window.sidecar 为 E5 窗口内测栏，附着目标用 attachTo 声明）。"
    },
    "emits": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "本插件会发出的事件，格式 <id>.<名词>.<过去式>。"
    },
    "consumes": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "本插件会监听的事件。"
    },
    "permissions": {
      "oneOf": [
        {
          "type": "array",
          "items": {
            "type": "string"
          },
          "description": "【旧格式，向后兼容】权限字符串数组，如 [\"db:own\"]。新插件请用对象格式。"
        },
        {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "filesystem": {
              "type": "boolean",
              "default": false,
              "description": "允许访问本地文件系统"
            },
            "network": {
              "type": "boolean",
              "default": false,
              "description": "允许发起网络请求"
            },
            "subprocess": {
              "type": "boolean",
              "default": false,
              "description": "允许创建子进程"
            }
          },
          "required": [],
          "description": "【新格式】声明式权限：filesystem / network / subprocess，默认均为 false，需显式授权"
        }
      ],
      "description": "权限声明：支持旧数组格式（向后兼容）或新对象格式（声明式）。"
    },
    "activates_on": {
      "type": "array",
      "items": {
        "type": "string",
        "pattern": "^(startup:always|event:[a-z0-9-]+(\\.[a-z0-9-]+)+)$"
      },
      "description": "声明式激活事件。缺省或含 startup:always = 启动即激活（向后兼容）；event:<topic> = 事件命中触发懒加载（精确匹配）；未命中则保持注册未激活。"
    },
    "migrations": {
      "type": [
        "string",
        "null"
      ],
      "description": "自带迁移目录（相对插件根）。无表则写 null。"
    },
    "settingsSchema": {
      "type": [
        "string",
        "null"
      ],
      "description": "设置 JSON Schema 路径；无设置则写 null。"
    },
    "lifecycle": {
      "type": [
        "string",
        "null"
      ],
      "enum": [
        "hooks",
        null
      ],
      "description": "可选：本插件是否提供生命周期钩子。只有两个合法值 —— \"hooks\" 表示 api/lifecycle.py 里定义了**固定名**钩子（on_install / on_enable / on_disable / on_uninstall，签名 (db=None)）；null 或缺省表示不提供。★ 钩子名固定，不支持自定义映射（2026-11-10 裁决 b）：允许改名没有真实需求，只会多一层间接、并在写错名字时静默不执行。★ 声明与实现必须一致：声明了 hooks 却找不到文件，或写了钩子却没声明，启停时都会写进 plugin_state.last_error（readyz 可见）。钩子抛异常仍是软失败（effect 已生效、不回滚）；需要「要么全成、要么全不成」的初始化请放迁移（迁移失败会补偿）。"
    },
    "defines": {
      "type": "array",
      "items": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9-]*(\\.[a-z0-9-]+)+$"
      },
      "description": "本插件**定义**（声明所有权）的能力，格式 <id>.<名词>.<动作>。三角色的第三角色：defines（定义者）/ provides（实现者）/ requires（使用者）。一个能力最多一个 defines，可以有多个 provides（多 Provider 由配置选定）。兼容模式：若全系统没有任何插件写 defines，则 provides 隐含 defines；一旦有任一插件开始写 defines，整个系统切到严格模式（provides 必须有人 defines）。"
    },
    "deactivation": {
      "type": "string",
      "enum": [
        "manual",
        "auto"
      ],
      "default": "manual",
      "description": "依赖丧失时的处置策略：manual（默认）= 只有人为 disable 才停用；auto = requires 中任一能力被撤销时，内核自动把本插件转入 PAUSED。★ 注意：这不是插件自己写逻辑，而是内核在依赖变化时主动转移阶段；插件只声明希望被怎样对待。软依赖（optionalDependencies）永远只降级，不触发停用。"
    },
    "riskLevel": {
      "type": "string",
      "enum": [
        "L0",
        "L1",
        "L2",
        "L3"
      ],
      "description": "风险分级。未声明时按 kind 推断：core→L3, builtin→L2, third-party→L2, container→L1。★ 推断值可以显式降低，但**不得显式升高**（升高无意义且降低有免审批的诱惑）。门禁：kind=third-party 声明 L0 → 启动失败（免审批权不能交给被审批方自己声明）。"
    },
    "execution": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "sandbox": {
          "type": "string",
          "enum": [
            "read-only",
            "workspace-write",
            "full"
          ],
          "default": "read-only",
          "description": "沙箱等级。read-only = 只能写必要 sink（如 /dev/null）；workspace-write = 工作区根目录 + 显式声明的额外目录；full = 不限（绕过隔离）。★ 默认取最保守值：需要写的插件应显式声明，这是有意识的动作。约束：不得高于内核策略允许的上限。"
        },
        "approval": {
          "type": "string",
          "enum": [
            "ask",
            "never"
          ],
          "default": "ask",
          "description": "审批策略。ask = 需要人工确认；never = 确定性拒绝所有审批请求。★ 门禁：kind != core 且 approval=never → 启动失败。原因：never 会把插件锁死成永远不可用（可用性问题，不是安全问题），且报错而非自动修正，是为了让作者的语义误解暴露出来。"
        }
      },
      "description": "执行约束（沙箱 + 审批）。未声明时按上面的默认值。"
    },
    "sideEffects": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "external": {
          "type": "boolean",
          "default": false,
          "description": "是否产生外部副作用（发消息、改第三方系统、发请求等）。"
        },
        "checkpoint": {
          "type": "string",
          "enum": [
            "before-side-effect",
            "none"
          ],
          "default": "none",
          "description": "检查点策略。external=true 时**必须**为 before-side-effect，否则启动失败。检查点失败 → 阻止执行（不是记日志继续）：没有恢复依据却让操作发生 = 出事无法收拾。★ 崩溃恢复语义：中断的副作用以「未知结果」恢复，**禁止自动重试也禁止自动回滚**（两者都要求知道上次做没做成，而这个信息恰恰没有）。"
        }
      },
      "description": "副作用声明。AI 动态改造场景的刚需：让「出事能恢复」成为可机器验证的前提。"
    },
    "track": {
      "type": "string",
      "enum": [
        "persistent",
        "ephemeral"
      ],
      "default": "persistent",
      "description": "状态跟踪方式。persistent（默认）= 写配置，跨会话存在，只能由人或经审批的 AI 创建；ephemeral = 仅进程内，重启即消失，AI 可直接创建。★ ephemeral 的约束：不得声明 migrations（会留下无人管理的残留表）、不得 kind=core、进程退出前必须自动清理。有价值的数据必须显式「提升」为 persistent，不能自动发生。"
    },
    "origin": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "source": {
          "type": "string",
          "enum": [
            "builtin",
            "local",
            "remote"
          ],
          "description": "来源：builtin（随系统发布）/ local（本地开发）/ remote（远程拉取）。"
        },
        "signed": {
          "type": "boolean",
          "default": false,
          "description": "是否已签名。signed=true 但缺 signature → 启动失败。"
        },
        "signature": {
          "type": "string",
          "pattern": "^sha256:.+$",
          "description": "签名值。signed=true 时必填。"
        },
        "installedBy": {
          "type": "string",
          "pattern": "^(human\\.user|agent\\.[a-z0-9-]+)$",
          "description": "安装者。human.user = 人装的；agent.<id> = AI 装的。★ 必填，缺失则启动失败（无法审计）。为什么要区分人/AI：人装的能追溯到具体的人；AI 装的还要额外考虑「这个 AI 当时被允许做这件事吗」。混在一个字段里事后分不清。"
        }
      },
      "description": "来源与安装者。★ 为什么必填：已安装的插件代码在宿主进程内运行，**不受沙箱限制**——插件一旦装进来就和内核同权，沙箱管不了已加载进内存的代码。所以「谁装的、有没有签名、来自哪里」是现阶段唯一能给的信任信号。"
    },
    "concurrency": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "optimisticLock": {
          "type": "boolean",
          "default": true,
          "description": "是否启用乐观锁（带版本号写，避免后写覆盖先写的丢失更新）。默认开；不许为 false，除非 exclusive=true（独占就不需要乐观锁）。"
        },
        "exclusive": {
          "type": "boolean",
          "default": false,
          "description": "是否独占。声明了 migrations 时**必须**为 true（独占迁移，不许并发跑）。"
        },
        "parallelSafe": {
          "type": "array",
          "items": {
            "type": "string"
          },
          "description": "可并发执行的操作白名单。**只能列只读操作**——出现 write / delete / patch → 启动失败。★ 为什么只给只读：写操作即使作用于不同数据也可能有隐式依赖（如都改同一张表的行数统计），「可并发」的前提是「互不影响」。"
        }
      },
      "description": "并发策略。多个 AI 同时改同一插件配置时的丢失更新防护。"
    },
    "schedule": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "at": {
          "type": "string",
          "description": "严格未来的绝对时点（带显式时区），如 2026-11-01T09:00:00+08:00。"
        },
        "after_seconds": {
          "type": "integer",
          "minimum": 1,
          "description": "相对延迟（正安全整数秒数）。★ 不支持原地更新（更新只替换名称/指令/时间，保留 id 与记录）。"
        },
        "every_seconds": {
          "type": "integer",
          "minimum": 60,
          "description": "固定间隔，最小 60 秒，初始对齐创建时间。★ every_seconds:86400 **不能**替代 daily——固定间隔对齐创建时刻，daily 对齐墙上时间，是两种语义。"
        },
        "daily": {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "time",
            "timeZone"
          ],
          "properties": {
            "time": {
              "type": "string",
              "pattern": "^\\d{2}:\\d{2}:\\d{2}$"
            },
            "timeZone": {
              "type": "string",
              "description": "IANA 时区名，如 Asia/Shanghai。"
            }
          },
          "description": "每日按本地钟表时间。"
        },
        "weekly": {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "time",
            "timeZone",
            "weekdays"
          ],
          "properties": {
            "time": {
              "type": "string",
              "pattern": "^\\d{2}:\\d{2}:\\d{2}$"
            },
            "timeZone": {
              "type": "string"
            },
            "weekdays": {
              "type": "array",
              "items": {
                "type": "integer",
                "minimum": 1,
                "maximum": 7
              },
              "description": "ISO 星期集合（周一=1，周日=7）。"
            }
          },
          "description": "每周按本地钟表时间 + 星期集合。"
        },
        "cron": {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "expression",
            "timeZone"
          ],
          "properties": {
            "expression": {
              "type": "string",
              "description": "五字段 Vixie cron 表达式。"
            },
            "timeZone": {
              "type": "string"
            }
          },
          "description": "按 cron 表达式。"
        },
        "title": {
          "type": "string",
          "minLength": 1,
          "maxLength": 120,
          "description": "任务标题（去首尾空白后非空且 ≤120 字符）。"
        },
        "catchUp": {
          "type": "string",
          "enum": [
            "latest-only",
            "all"
          ],
          "default": "latest-only",
          "description": "重启后错过时点的补发策略。默认 latest-only（只补最近一次）——停机 3 天的每日任务补 3 次会刷屏。"
        }
      },
      "description": "定时任务声明。★ 六个选择器（at/after_seconds/every_seconds/daily/weekly/cron）**必须只给一个**，给多个 → 启动失败。★ 删除语义（必须显式）：删除定时任务时，连带移除任务行及其已保存的发送记录。破坏性行为不能靠「实现时自然如此」——协议要把隐式行为变成显式规定。"
    },
    "outputPolicy": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "maxInlineTokens": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 1,
          "description": "超过此 token 阈值触发溢出。默认 null（不溢出）。"
        },
        "spillTo": {
          "type": "string",
          "enum": [
            "own"
          ],
          "description": "溢出内容存到哪：own = 插件自己的临时目录。"
        },
        "onStoreFailure": {
          "type": "string",
          "enum": [
            "keep-raw"
          ],
          "description": "存储失败时的行为。**强制 keep-raw，不给选项**——存不下就把结果丢掉是静默数据损失，违反「不允许静默失败」铁律。"
        }
      },
      "description": "大输出处理。★ 做法：在模型上下文之外保存全文，返回定位信息与取回指引（既省 token 又防超限）。溢出文件在插件卸载时清理（属 effect）。"
    },
    "extensions": {
      "type": "object",
      "description": "扩展位。放行不报不拒。"
    }
  },
  "patternProperties": {
    "^x_": {
      "description": "扩展位：x_ 前缀字段一律放行。"
    }
  }
}
```

### 5.2 ② 运行时数据模型：`services/api/core/manifest.py`（499 行）

```python
# ── 协议在 Python 侧的运行时落点 ──
"""模块 manifest 模型 + 自动发现。

★ 这是 T03 的"模块自动发现"职责（总纲 §1.3 / 验收 #2-#3）。
  校验失败必须**明确指出是哪个模块、哪个字段**（不许静默跳过）。
  正式的 plugin.schema.json 归 T14；这里是内核运行期的最小校验。
"""
from __future__ import annotations

import importlib
import logging
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.errors import ManifestError

log = logging.getLogger("kernel.manifest")

_KINDS = {"core", "builtin", "third-party", "container"}
_ID_RE = __import__("re").compile(r"^[a-z][a-z0-9-]*$")
# activates_on 条目只允许 startup:always 或 event:<topic>
#（topic 点分小写、至少两段，如 event:note.created）——与 contracts/plugin.schema.json
# 的 pattern 保持字面一致，两层校验同一语义。
_ACTIVATES_ON_RE = __import__("re").compile(
    r"^(startup:always|event:[a-z0-9-]+(\.[a-z0-9-]+)+)$"
)


class ManifestApi(BaseModel):
    base: str = Field(..., description="路由前缀，必须为 /api/v1/<id>")
    openapi: str | None = None
    health: str | None = None
    # ISSUE-008 方案 A（2026-09-20）：resource → 真实路由段 的显式映射。
    # 键 = provides 倒数第二段（resource），值 = api.base 下的路由段（如
    # "/entries"、"/health-of-system"）。声明则 MCP 转发用它，未声明的
    # resource 走机械「resource+s」推导——T18「加工具零额外声明」对
    # 常规 REST 资源依旧成立，显式声明只用于不规则/语义命名路由。
    tools: dict[str, str] = {}


class Manifest(BaseModel):
    id: str
    name: str
    version: str
    kind: str
    minKernel: str | None = None
    kernelApi: str | None = None
    icon: str | None = None
    description: str | None = None
    author: str | None = None
    # ★ 路径 a（2026-09-25）：容器型模块（kind="container"）允许**无 api**
    #   —— 系统窗等纯前端编排容器没有后端路由，声明式注册即"一切皆插件"的正形态。
    #   非 container 型仍强制声明 api（校验见 _validate）。向后兼容：存量模块零影响。
    api: ManifestApi | None = None
    # ★ 路径 a：容器型模块的页签声明——一次定稿，形状与 Zcode
    #   SystemApp（70c0702）对接面一致：{key, label, entry?}，entry=模块 id
    #（无 entry 页 = 「后端模块 · 仅服务」薄壳语义）。非 container 型可忽略。
    tabs: list[dict[str, Any]] = []
    # ★ 本容器吸收的模块 id 列表：这些模块**不再单独出现在底端栏**，入口收进本容器。
    #   典型场景：某后端模块被更完整的容器取代（无独立视图页），由本容器接管其入口。
    #   声明了 tabs[].entry 的模块**不必**在此重复列出 —— entry 已隐含吸收。
    absorbs: list[str] = []
    provides: list[str] = []
    requires: list[str] = []
    # ★（#009 软依赖声明的静态半边）：缺了**不阻塞启动**的依赖。
    #   语义：硬依赖（requires）缺 = 起不来（T14 fail-fast 不变）；
    #        软依赖（本字段）缺 = 坞位挂 degraded 角标，功能降级但不死。
    #   与 requires 必须互斥（同一条目不得两处都写 —— #033 闸门规则 ④）。
    optionalDependencies: list[str] = []
    slots: list[str] = []
    emits: list[str] = []
    consumes: list[str] = []
    # ★ 2026-09-25（修与补）：schema 的 permissions 有**两种格式** ——
    #   字符串数组（旧，细粒度）与对象（新，filesystem/network/subprocess 粗开关）。
    #   此前模型只声明 list[str]，**对象格式会被 pydantic 直接判非法** → 与 schema 口径打架。
    #   此处放宽为联合类型；**归一化在 core/plugins/permissions.normalize_permissions**
    #（唯一入口），本模型只负责"看得见、不报错"。
    permissions: list[str] | dict[str, bool] = Field(default_factory=lambda: [])
    activates_on: list[str] = []
    migrations: str | None = None
    settingsSchema: str | None = None
    # ★ D′-1（2026-09-23 · 唯一读法之内核侧）：这两个字段**原文通道一直在用、模型通道
    #   却从未声明**（pydantic 默认 extra="ignore" 把它们静默吞掉）——
    #   实测：所有内置 manifest 都写着 `entry` 与 `window`，而 `Manifest` 模型看不见它们，
    #   导致同一份 manifest 两套口径（模型通道 / 原文通道）。此处补齐，让模型也"看得见"。
    #   前端侧（PluginContext 改读模型）**刻意留后**：那是 E5 在制区，改动必撞车。
    entry: str | None = None
    window: dict[str, Any] | None = None
    # ★ 生命周期钩子声明（2026-11-10 裁决 b · 降级为枚举）：
    #   原先这里是 `dict[str, Any]`，让 manifest 写 `{"onEnable": "on_enable", ...}`
    #   这类「钩子名映射」。但内核**从来不读它**——`core/plugins/lifecycle.py`
    #   按固定名取钩子。于是那层映射是纯装饰：写错名字不报错、不生效、也没人知道。
    #
    #   判断依据是「允许自定义钩子名有没有价值」：全仓唯一用到它的 pi-agent，
    #   三个值恰好等于固定名 —— 零信息。自定义名只会多一层间接，并在写错时
    #   **静默不执行**（fail-open），与本次 emits/consumes 的 fail-closed 相反。
    #
    #   ⇒ 字段保留，但降级成声明式枚举：`"hooks"` = 有固定名钩子，`None` = 没有。
    #     内核据此校验「声明 ↔ 实现」一致（见 `core/plugins/lifecycle.py`），
    #     不一致会写进 plugin_state.last_error —— 字段从装饰变成契约。
    lifecycle: str | None = None

    # ══════════════════════════════════════════════════════════════
    # 协议新增字段的运行时落点（2026-10-26）
    # ══════════════════════════════════════════════════════════════
    # ★ 为什么补这一段：协议 Schema 已有 35 个字段，但这个模型只声明了 24 个。
    #   剩下 10 个（下面这组）此前**只能从 model_extra 里摸**——校验层认真对待它们，
    #   运行层却没有任何具名落点，调用方得写 m.model_extra.get("riskLevel")。
    #   结果是：插件作者声明了 riskLevel / concurrency / schedule，
    #   内核读得到值却从不消费——**声明了等于没声明**。
    #   这是比「字段被静默吞掉」更隐蔽的一类协议失效：值没丢，但没人用。
    #
    # ★ 补法：给每个协议字段一个具名落点 + 明确默认值。
    #   默认值取「内核推断的保守默认」——与协议兼容策略一致：
    #   字段缺失不拦，按保守默认走；只有显式声明错才失败。
    #   这样存量插件零改动通过，新插件又能真正被内核读懂。

    # 能力定义（三角色的 Service Definition 侧）：本插件「定义」了哪些能力。
    # ★ 一个能力最多一个 defines，可以有多个 provides（多 Provider 由配置选定）。
    #   兼容模式：若全系统没有任何插件写 defines，则 provides 隐含 defines。
    defines: list[str] = []
    # 依赖丧失时的降级策略：manual（人工介入）/ auto（自动转 PAUSED）。
    # ★ auto = requires 中任一能力被撤销时，内核**主动**把本插件转入 PAUSED。
    #   这不是插件自己写逻辑，而是内核在依赖变化时转移阶段——
    #   插件只声明希望被怎样对待。
    # ★ 默认必须是 manual：若默认 auto，那么内核升级后所有老插件
    #   都会在依赖丢失时被停掉，而它们甚至不知道自己有这个声明。
    deactivation: str = "manual"
    # 风险等级 L0~L3：决定审批与沙箱强度。third-party 不得声明 L0。
    riskLevel: str | None = None
    # 执行约束：沙箱等级 + 审批策略。
    execution: dict[str, Any] = Field(default_factory=dict)
    # 副作用声明：是否触碰外部系统、是否需要检查点。
    sideEffects: dict[str, Any] = Field(default_factory=dict)
    # 生命周期轨道：persistent（留数据）/ ephemeral（不留）。
    track: str = "persistent"
    # 来源与审计：谁装的、是否签名。
    origin: dict[str, Any] = Field(default_factory=dict)
    # 并发契约：乐观锁、独占、并行安全操作。
    concurrency: dict[str, Any] = Field(default_factory=dict)
    # 定时调度：六种选择器之一（at/after_seconds/every_seconds/daily/weekly/cron）。
    schedule: dict[str, Any] = Field(default_factory=dict)
    # 大输出策略：内联上限、溢出去处、失败兜底。
    outputPolicy: dict[str, Any] = Field(default_factory=dict)
    # 扩展位：协议向前兼容的容器。放行不报不拒——先让插件写上，
    # 等内核开始解释它再收紧校验。之所以要给具名落点（而不是留在 model_extra），
    # 是为了让「协议字段 ↔ 运行时落点」的对应关系可机器核对。
    extensions: dict[str, Any] = Field(default_factory=dict)

    # ── D′-2（2026-09-23）：未知字段必须**被报告**，不再静默吞 ──
    # 为什么是 class-level：pydantic v2 的 extra 策略在模型级生效，而我们要按 manifest 的
    # kind **分档**（内置严、第三方宽）→ 故模型级放 allow（不丢信息），分档判定放到 _validate。
    model_config = ConfigDict(extra="allow")

    @property
    def unknown_fields(self) -> dict[str, Any]:
        """本 manifest 里模型不认识的顶层字段（`extra="allow"` 会收在这里）。

        ★ 静默失败防御：旧行为下这些字段被**直接丢弃且不留痕**——
          `requires` 拼成 `requiers` 时，作者以为声明了硬依赖，实际 `requires` 仍是空表。
        """
        return dict(self.model_extra or {})

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


def _validate(module_id: str, raw: dict[str, Any], dir_name: str) -> Manifest:
    """校验单个 manifest，失败抛 ManifestError（带模块名+字段）。"""
    try:
        m = Manifest(**raw)
    except ValidationError as exc:
        locs = "; ".join(
            f"{'/'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"
            for e in exc.errors()
        )
        raise ManifestError(
            f"模块「{module_id}」manifest 校验失败：{locs}"
        ) from exc

    if m.id != dir_name:
        raise ManifestError(
            f"模块「{dir_name}」manifest.id={m.id!r} 必须等于目录名 {dir_name!r}"
        )
    if not _ID_RE.match(m.id):
        raise ManifestError(
            f"模块「{m.id}」id 必须是小写英文/数字/连字符，且以字母开头"
        )
    if m.kind not in _KINDS:
        raise ManifestError(
            f"模块「{m.id}」kind={m.kind!r} 必须是 {sorted(_KINDS)} 之一"
        )
    # ★ 路径 a：container 型可无 api；非 container 型必须声明 api
    if m.api is None:
        if m.kind != "container":
            raise ManifestError(
                f"模块「{m.id}」kind={m.kind!r} 必须声明 api（仅 kind=\"container\" 可省）"
            )
    else:
        expected_base = f"/api/v1/{m.id}"
        if m.api.base != expected_base:
            raise ManifestError(
                f"模块「{m.id}」api.base 必须是 {expected_base!r}，实际为 {m.api.base!r}"
            )
    # ★ 路径 a：tabs 形状校验（一次定稿）——每项必须含 key/label，entry 可选
    for idx, tab in enumerate(m.tabs):
        if not isinstance(tab, dict):
            raise ManifestError(
                f"模块「{m.id}」tabs[{idx}] 必须是对象 {{key,label,entry?}}"
            )
        missing = [k for k in ("key", "label") if not tab.get(k)]
        if missing:
            raise ManifestError(
                f"模块「{m.id}」tabs[{idx}] 缺必填字段 {missing}（形状 {{key,label,entry?}}）"
            )
        if "entry" in tab and tab["entry"] is not None and not isinstance(tab["entry"], str):
            raise ManifestError(
                f"模块「{m.id}」tabs[{idx}].entry 必须是字符串（模块 id）或省略"
            )
    # ★ absorbs 校验：被本容器吸收的模块 id 列表。
    #   ① 必须是字符串 id，不能是自己（自己吸收自己无意义）；
    #   ② 与 tabs[].entry 不得重复 —— entry 已隐含吸收，重复写两遍会让
    #      "吸收关系"有两个来源，埋下分叉。
    for aid in m.absorbs:
        if not isinstance(aid, str) or not aid:
            raise ManifestError(
                f"模块「{m.id}」absorbs 条目 {aid!r} 非法：必须是非空字符串（被吸收的模块 id）"
            )
        if aid == m.id:
            raise ManifestError(f"模块「{m.id}」不能吸收自己（absorbs 含自身 id）")
    entry_ids = {t.get("entry") for t in m.tabs if isinstance(t, dict)}
    dup = sorted(set(m.absorbs) & {e for e in entry_ids if isinstance(e, str)})
    if dup:
        raise ManifestError(
            f"模块「{m.id}」absorbs 与 tabs[].entry 重复声明 {dup} —— entry 已隐含吸收，不必重复写"
        )
    for act in m.activates_on:
        if not _ACTIVATES_ON_RE.match(act):
            raise ManifestError(
                f"模块「{m.id}」activates_on 条目 {act!r} 非法：只支持 "
                "startup:always 或 event:<topic>（点分小写、至少两段，如 event:note.created）"
            )

    # ★ D′-2（2026-09-23）：未知字段分档处理 —— 不再静默吞
    #   留扩展位：`x_` 前缀 与 `extensions` 对象一律放行（对齐 Agent Plugins 的 extensions
    #   命名空间思路：客户端特定数据有明确的落脚处，不必挤进顶层）。
    unknown = {
        k: v
        for k, v in m.unknown_fields.items()
        if not k.startswith("x_") and k != "extensions"
    }
    if unknown:
        names = sorted(unknown)
        if m.kind == "builtin":
            # 内置插件由我们自己写 → 拼错必须当场拒（这正是 D 提案的原始 bug：
            # `requires` 拼成 `requiers` 时，作者以为声明了硬依赖，实际是空表）
            raise ManifestError(
                f"模块「{m.id}」manifest 含内核不认识的字段 {names}；"
                "内置插件的字段名必须字面正确（拼错会静默失效）。"
                "确需携带客户端私有数据请用 `extensions` 对象或 `x_` 前缀。"
            )
        # 第三方插件：报告但继续（对齐 Agent Plugins Spec「未知字段 MUST report」）
        log.warning(
            "模块 %s（kind=%s）manifest 含未知字段 %s —— 已忽略（第三方允许扩展）",
            m.id,
            m.kind,
            names,
        )

    return m


# ── · 依赖声明闸门（#033 四条静态规则）─────────────────────────
# 依据：Home Assistant hassfest「Reject manifest dependencies on core integrations」
#（PR #169425）+ 我方显式声明线（#019→#021→#020）。**依赖声明错误必须在闸门层
# 拦截，不能留给运行时** —— 等加载期撞墙时模块已经挂了一半，现场很难定位。
#
# ★ 语义锚点（2026-09-23 实装时被真实目录打回后校准）：`requires` /
#   `optionalDependencies` 里写的是**能力名**（如 `web.entry.read`），**不是模块 id** ——
#   闸门判据是「该能力必须被某个模块 provides」。第一版按"模块 id 必须实存"判，
#   上线即被 catalog（它依赖的是 web 的能力，不是 web 这个模块）拦下 ——
#   这次校准本身就是闸门价值的证明。
#
# 四条规则：① 依赖的能力必须被某模块 provides ② 禁止反向依赖内核内部能力
#           ③ 循环依赖检测（能力→提供者模块，只沿硬依赖）④ 软依赖不得同时是硬依赖
KERNEL_RESERVED_CAPABILITY_PREFIXES: tuple[str, ...] = ("core.", "kernel.", "_kernel.")


def _scan_third_party_provides() -> dict[str, str]:
    """★ 2026-09-25：把 `plugins/` 里第三方插件的 provides 也纳入
    能力映射，否则内置插件**无法声明对第三方能力的依赖**（闸门会误判"没人 provides"）。

    ★ 边界说明：第三方插件是**可卸载**的，所以内置插件对它的依赖**只应写软依赖**
      （optionalDependencies）—— 见下方软依赖放宽处理。此函数只负责"看得见"。
    """
    provider: dict[str, str] = {}
    try:
        import json as _json

        plug_dir = Path(__file__).resolve().parents[2] / "plugins"
        if not plug_dir.is_dir():
            return provider
        for mf in sorted(plug_dir.glob("*/manifest.json")):
            try:
                raw = _json.loads(mf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for cap in raw.get("provides") or []:
                provider.setdefault(str(cap), str(raw.get("id") or mf.parent.name))
    except Exception:  # noqa: BLE001 —— 尽力而为，绝不让它拖垮启动
        pass
    return provider


def _gate_dependency_declarations(found: dict[str, tuple[Manifest, Path]]) -> None:
    """依赖声明闸门：全部模块发现后统一校验，失败抛 ManifestError（启动即失败）。"""
    # 能力名 → 提供者模块 id
    provider: dict[str, str] = {}
    # 能力名 → 全部提供者（发现第二个就记下来，不静默忽略）
    multi_providers: dict[str, list[str]] = {}

    def _record(cap: str, mid: str) -> None:
        if cap in provider:
            multi_providers.setdefault(cap, [provider[cap]]).append(mid)
        else:
            provider[cap] = mid

    for mid, (m, _p) in found.items():
        for cap in m.provides:
            _record(cap, mid)
    # ★ 第三方插件的 provides 也纳入（否则内置无法依赖第三方能力）
    for cap, mid in _scan_third_party_provides().items():
        _record(cap, mid)

    # ★ 发现第二个提供者 ⇒ **记录并告警**，不许静默忽略。
    #   旧实现是 `provider.setdefault(cap, mid)`：先到先得，
    #   后提供者被**静默忽略**——这正是跨插件校验规则 13 自己的注释
    #   批评的行为（「违反『不许静默失败』铁律」）。
    #   新口径（2026-10-28）：多 Provider **不阻断启动**，
    #   但**必须留痕**——它是「待裁决冲突」，
    #   由包层用 `providerChoice` 裁决；没包裁决则该能力标 unavailable。
    #   ⇒ 这里既不抛错（抛错等于让内核替包层做业务裁决），也不静默。
    for cap, mids in sorted(multi_providers.items()):
        log.warning(
            "能力 %s 有多个提供者（%s）：已选 %s，其余待裁决。"
            "如需固定选谁，请在包的 pack.json 的 providerChoice 中声明。",
            cap,
            ", ".join(sorted(set(mids))),
            provider[cap],
        )

    # 模块 → 其硬依赖所指向的模块（经「能力 → 提供者」映射；软依赖不入图）
    hard_edges: dict[str, set[str]] = {mid: set() for mid in found}

    for mid, (m, _p) in found.items():
        hard, soft = list(m.requires), list(m.optionalDependencies)

        # ④ 软硬互斥（同一能力不得两处都写）
        both = sorted(set(hard) & set(soft))
        if both:
            raise ManifestError(
                f"模块「{mid}」的能力 {both} 同时出现在 requires 与 optionalDependencies"
                "（软硬必须互斥：要么缺了会死，要么缺了降级，不能两头都占）"
            )

        for cap in hard + soft:
            field = "requires" if cap in hard else "optionalDependencies"
            # ② 不得反向依赖内核内部能力
            if cap.startswith(KERNEL_RESERVED_CAPABILITY_PREFIXES):
                raise ManifestError(
                    f"模块「{mid}」{field} 依赖内核内部能力 {cap!r}"
                    "（内核是宿主、不对外 provides —— 反向依赖禁止）"
                )
            # ① 依赖的能力必须真的有人 provides
            #   ★ 2026-09-25：**软依赖放宽** ——
            #     optionalDependencies 的语义本就是「缺了降级不死」（见 Manifest 注释），
            #     故找不到提供者时**只告警**；硬依赖仍 fail-fast。
            if cap not in provider:
                if field == "optionalDependencies":
                    log.warning(
                        "模块 %s 的软依赖 %r 当前没有提供者（允许：缺了降级不死）", mid, cap
                    )
                    continue
                raise ManifestError(
                    f"模块「{mid}」{field} 声明的能力 {cap!r} 没有任何模块 provides"
                )
            if field == "requires":
                owner = provider[cap]
                if owner != mid:  # 依赖自己提供的能力不构成环
                    hard_edges[mid].add(owner)

    # ③ 循环依赖：沿「能力 → 提供者模块」的硬边遍历（软依赖缺了不死，不参与）
    white, gray, black = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(found, white)

    def visit(node: str, path: list[str]) -> None:
        color[node] = gray
        for nxt in sorted(hard_edges.get(node, ())):
            if nxt not in found:  # 规则①已拦，此处防御性跳过
                continue
            if color[nxt] == gray:
                raise ManifestError("检测到循环硬依赖：" + " → ".join([*path, node, nxt]))
            if color[nxt] == white:
                visit(nxt, [*path, node])
        color[node] = black

    for mid in sorted(found):
        if color[mid] == white:
            visit(mid, [])


# ── · 软依赖缺失的运行时判定（#009 的 degraded 半边）────────────
# 硬依赖缺失 = 启动期 fail-fast（T14 语义），**运行时不会出现** —— 所以运行期只判软依赖。
# 产出 degraded + reason_code，供 O1 四态面板消费（#009：「增加 reason_code 字段即可
# 对齐，不动 schema 主结构」）。
#
# health / O1 侧接入示例（无需改本模块即可用）：
#     avail = {cap for m, _ in discovered for cap in m.provides}
#     st = dependency_status(manifest, avail)
#     # st.state ∈ {"ok", "degraded"}
#     # st.reason_code 形如 "optional_dependency_missing:finance.beeccount.read"
@dataclass(frozen=True)
class DependencyStatus:
    """模块依赖态判定结果（运行期；只覆盖软依赖半边）。"""

    state: str  # "ok" | "degraded"
    reason_code: str | None
    missing: tuple[str, ...]


def dependency_status(m: Any, available: Collection[str]) -> DependencyStatus:
    """按软依赖声明 + 当前可用能力集合，判定模块依赖态。

    ★ 只判软依赖（`optionalDependencies`）：硬依赖缺失已由启动期 fail-fast 拦下，
      运行期不会出现 —— 此处不重复判定，避免两处语义打架。
    """
    missing = tuple(sorted(c for c in m.optionalDependencies if c not in available))
    if not missing:
        return DependencyStatus(state="ok", reason_code=None, missing=())
    return DependencyStatus(
        state="degraded",
        reason_code="optional_dependency_missing:" + ",".join(missing),
        missing=missing,
    )


def discover_modules(modules_dir: str | Path) -> list[tuple[Manifest, Path]]:
    """扫描 modules/*/manifest.json，返回 (manifest, 目录) 列表。

    同 id 重复 -> 报错。无 router.py -> 报错。
    """
    root = Path(modules_dir)
    found: dict[str, tuple[Manifest, Path]] = {}
    results: list[tuple[Manifest, Path]] = []

    if not root.is_dir():
        return results

    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        manifest_path = sub / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            raw = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
        except __import__("json").JSONDecodeError as exc:
            raise ManifestError(
                f"模块「{sub.name}」manifest.json 不是合法 JSON：{exc}"
            ) from exc

        m = _validate(sub.name, raw, sub.name)
        if m.id in found:
            raise ManifestError(f"模块 id 重复：{m.id!r}（{sub.name} 与已发现模块冲突）")
        found[m.id] = (m, sub)
        results.append((m, sub))

    # ★ 全部发现后统一过「依赖声明闸门」（①实存 ②禁内核 ③环 ④软硬互斥）
    _gate_dependency_declarations(found)

    return results


def load_router(module_id: str) -> Any:
    """动态导入 modules.<id>.router 并取 router 对象。"""
    try:
        mod = importlib.import_module(f"modules.{module_id}.router")
    except ModuleNotFoundError as exc:
        raise ManifestError(
            f"模块「{module_id}」缺少 router.py（无法 import modules.{module_id}.router）：{exc}"
        ) from exc
    router = getattr(mod, "router", None)
    if router is None:
        raise ManifestError(f"模块「{module_id}」router.py 未定义 router = APIRouter()")
    return router
```

### 5.3 ③ 校验器第一层（结构）+ 第二层（跨字段）：`services/api/core/plugins/validate.py`（466 行）

```python
"""用 contracts/plugin.schema.json 对 manifest 做强校验（总纲 §1.3.1）。

★ 这是"一切皆插件"的机器守门人之一：校验失败必须精确到
  「哪个插件 · 哪个字段 · 期望什么 · 实际什么」，不许静默跳过。

★ 原则：代码/Schema 为协议，文档为说明。
  本文件实现的是**协议本身**：字段形状由 contracts/plugin.schema.json 定义，
  跨字段一致性规则由本文件的 _check_consistency 实现。
  协议技术报告（Alongside_插件协议技术报告_v1.0.md）是这份代码的说明文档。

校验分三层（协议报告 §19）：
  ① 结构校验（JSON Schema 层）—— 类型、格式、必填、枚举、条件必填
  ② 跨字段一致性（单插件内）—— requires ∩ optionalDependencies = ∅ 等
  ③ 跨插件一致性（需全量插件）—— 见 core/plugins/crosscheck.py

实现说明：不引入 jsonschema 依赖，自实现一个覆盖本项目用到的 JSON Schema
关键字子集（type / required / enum / pattern / minLength / minimum /
maxLength / items / properties / additionalProperties / patternProperties /
allOf / anyOf / oneOf / if-then-else / const / 可空联合），足够支撑契约校验，
且错误信息完全可控。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.errors import ManifestError

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[4] / "contracts" / "plugin.schema.json"
)

_SCHEMA: dict[str, Any] = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))

# 能力名格式：<id>.<名词>.<动作>，至少一段点分
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z0-9-]+)+$")

# 定时选择器：六个，且必须只给一个
_SCHEDULE_SELECTORS = (
    "at",
    "after_seconds",
    "every_seconds",
    "daily",
    "weekly",
    "cron",
)

# parallelSafe 只允许只读操作
_READ_ONLY_OPS = {"read", "list"}

# 运行时资产端点前缀：GET /plugin-assets/{id}/{path}（第三方插件 UI 由后端运行时服务）
_PLUGIN_ASSET_PREFIX = "/plugin-assets/"

# entry 允许的 URL scheme。★ 用白名单而不是黑名单：entry 会被前端 import()，
# 未知 scheme 的默认处置只能是拒绝，列黑名单永远漏一个。
_ALLOWED_ENTRY_SCHEMES = ("http", "https")


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _match_type(value: Any, typ: Any) -> bool:
    """支持单类型与 [a, b, ...] 联合（本项目用于可空联合 [string, null]）。"""
    if isinstance(typ, list):
        return any(_match_type(value, t) for t in typ)
    name = _type_name(value)
    if typ == "integer":
        # JSON 里整数是 int；bool 不是 integer
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if typ == "boolean":
        return name == "boolean"
    if typ == "string":
        return name == "string"
    if typ == "array":
        return name == "array"
    if typ == "object":
        return name == "object"
    if typ == "null":
        return value is None
    return name == typ


def _validate_node(
    value: Any,
    schema: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    """递归校验一个节点。errors 里追加的是「人话」错误，不含栈信息。"""
    if not isinstance(schema, dict):
        return

    # ── const：值必须完全相等 ──
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path or '<root>'}: 期望常量 {schema['const']!r}，实际为 {value!r}")

    # ── type ──
    typ = schema.get("type")
    if typ is not None and not _match_type(value, typ):
        errors.append(
            f"{path or '<root>'}: 期望类型 {typ}，实际为 {_type_name(value)}"
        )
        # 类型都不对，后续细项校验无意义
        return

    # ── 枚举 ──
    if "enum" in schema and value not in schema["enum"]:
        errors.append(
            f"{path}: 取值 {value!r} 不在允许集合 {schema['enum']} 内"
        )

    # ── 否定：not ──
    #   注意方向：not 的内层 schema **通过** 时，not 本身**失败**。
    if "not" in schema:
        not_errors: list[str] = []
        _validate_node(value, schema["not"], path, not_errors)
        if not not_errors:
            errors.append(f"{path}: 不应满足被否定的约束 {schema['not']!r}")

    # ── 组合关键字：allOf / anyOf / oneOf ──
    if "allOf" in schema:
        for sub in schema["allOf"]:
            _validate_node(value, sub, path, errors)

    if "anyOf" in schema:
        branch_errors: list[list[str]] = []
        for sub in schema["anyOf"]:
            sub_errors: list[str] = []
            _validate_node(value, sub, path, sub_errors)
            if not sub_errors:
                break
            branch_errors.append(sub_errors)
        else:
            detail = " | ".join(
                "{" + "; ".join(e) + "}" for e in branch_errors
            )
            errors.append(f"{path}: 不满足任何一个允许的分支（{detail}）")

    if "oneOf" in schema:
        matched = 0
        for sub in schema["oneOf"]:
            one_errors: list[str] = []
            _validate_node(value, sub, path, one_errors)
            if not one_errors:
                matched += 1
        if matched != 1:
            errors.append(
                f"{path}: 必须恰好满足 oneOf 中的 1 个分支，实际满足 {matched} 个"
            )

    # ── 条件必填：if / then / else ──
    if "if" in schema:
        probe: list[str] = []
        _validate_node(value, schema["if"], path, probe)
        branch = "then" if not probe else "else"
        if branch in schema:
            _validate_node(value, schema[branch], path, errors)

    # ── 对象 ──
    if isinstance(value, dict):
        required = schema.get("required", [])
        for field in required:
            if field not in value:
                errors.append(f"{path}.{field}: 缺少必填字段（契约要求：{field}）")
        props = schema.get("properties", {})
        for field, sub in props.items():
            if field in value:
                _validate_node(value[field], sub, f"{path}.{field}", errors)
        # patternProperties：正则命名的额外属性
        pattern_props = schema.get("patternProperties", {})
        for pattern, sub in pattern_props.items():
            try:
                rx = re.compile(pattern)
            except re.error:
                continue
            for field, val in value.items():
                if field in props:
                    continue
                if rx.search(field):
                    _validate_node(val, sub, f"{path}.{field}", errors)
        addl = schema.get("additionalProperties", True)
        if addl is False:
            for field in value:
                if field in props:
                    continue
                if any(
                    re.search(p, field)
                    for p in pattern_props
                    if _safe_compile(p)
                ):
                    continue
                errors.append(
                    f"{path}.{field}: 未知字段（契约不允许，请删除或走 RFC 流程）"
                )
        elif isinstance(addl, dict):
            for field, val in value.items():
                if field in props:
                    continue
                if any(re.search(p, field) for p in pattern_props if _safe_compile(p)):
                    continue
                _validate_node(val, addl, f"{path}.{field}", errors)

    # ── 数组 ──
    elif isinstance(value, list):
        items_schema = schema.get("items")
        if items_schema is not None:
            for idx, item in enumerate(value):
                _validate_node(item, items_schema, f"{path}[{idx}]", errors)

    # ── 字符串（pattern 用 search 语义：JSON Schema 规范，正则不隐式锚定）──
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        errors.append(
            f"{path}: 值 {value!r} 不匹配格式 {schema['pattern']}"
        )
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        errors.append(
            f"{path}: 字符串长度至少为 {schema['minLength']}，实际为 {len(value)}"
        )
    if "maxLength" in schema and isinstance(value, str) and len(value) > schema["maxLength"]:
        errors.append(
            f"{path}: 字符串长度最多为 {schema['maxLength']}，实际为 {len(value)}"
        )

    # ── 数值 ──
    if "minimum" in schema and isinstance(value, int | float) and value < schema["minimum"]:
        errors.append(
            f"{path}: 数值至少为 {schema['minimum']}，实际为 {value}"
        )
    if "maximum" in schema and isinstance(value, int | float) and value > schema["maximum"]:
        errors.append(
            f"{path}: 数值最多为 {schema['maximum']}，实际为 {value}"
        )


def _safe_compile(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


# ══════════════════════════════════════════════════════════════════════
# ② 跨字段一致性（单插件内）—— 协议报告 §19 第②层 / §20 规则表
# ══════════════════════════════════════════════════════════════════════


def _check_consistency(raw: dict[str, Any]) -> list[str]:
    """单 manifest 内部的跨字段一致性校验。

    ★ 每一项都对应协议报告 §20 规则表里的一条。规则的特征是：
      单看每个字段都合法，但组合起来语义矛盾。
    """
    errors: list[str] = []

    kind = raw.get("kind")
    req = raw.get("requires") or []
    opt = raw.get("optionalDependencies") or []

    # 规则 2：requires ∩ optionalDependencies = ∅
    dup = sorted(set(req) & set(opt))
    if dup:
        errors.append(
            f"manifest.requires 与 manifest.optionalDependencies 重复声明 {dup}："
            "同一能力不得既是硬依赖又是软依赖（从 optionalDependencies 里删掉它）"
        )

    # 规则 3：kind != core 时 approval != never
    execution = raw.get("execution") or {}
    approval = execution.get("approval")
    if kind != "core" and approval == "never":
        errors.append(
            f"manifest.execution.approval = 'never' 仅允许 kind='core' 使用"
            f"（当前 kind={kind!r}）：never 会把插件锁死成永远不可用，"
            "请改为 'ask'"
        )

    # 规则 4：third-party 不得声明 riskLevel L0
    if kind == "third-party" and raw.get("riskLevel") == "L0":
        errors.append(
            "manifest.riskLevel = 'L0' 不允许 kind='third-party' 声明："
            "免审批的收益归插件自己，代价归用户，因此这一项只能由内核推断"
        )

    # 规则 5：signed ⟹ signature
    origin = raw.get("origin") or {}
    if origin.get("signed") is True and not origin.get("signature"):
        errors.append(
            "manifest.origin.signed = true 但缺少 manifest.origin.signature"
        )
    # 规则 6：installedBy 必填
    # ★ 同上：仅当 origin 存在时才要求完整（存量插件没有 origin 字段）。
    #   一旦声明了 origin，它就是一份「来源承诺」，缺字段就等于没承诺 → 拒。
    if "origin" in raw and not origin.get("installedBy"):
        errors.append(
            "manifest.origin.installedBy 必填（缺失则无法审计谁装了它）"
        )

    # 规则 7：migrations ⟹ concurrency.exclusive
    # 规则 8：migrations ⟹ track == persistent
    # ★ 缺失 vs 显式错的区分（协议落地关键）：
    #   字段**缺失** → 按内核推断的保守默认值处理（迁移独占天然 = true）。
    #     否则 18 个存量插件会因「没写新字段」而集体起不来 —— 那不是协议收紧，
    #     是协议升级把存量系统打死了。
    #   字段**显式声明错** → 启动失败（这才是真正要拦的语义矛盾）。
    has_migrations = raw.get("migrations") not in (None, "", [])
    concurrency = raw.get("concurrency") or {}
    if has_migrations:
        if "exclusive" in concurrency and concurrency.get("exclusive") is not True:
            errors.append(
                "manifest.migrations 非空时必须声明 manifest.concurrency.exclusive = true"
                "（迁移必须独占，否则并发跑迁移会损坏数据）"
            )
        if "track" in raw and raw.get("track") != "persistent":
            errors.append(
                "manifest.migrations 非空时 manifest.track 必须为 'persistent'"
                "（临时插件不该改数据库结构，否则会留下无人管理的残留表）"
            )

    # 规则 9：sideEffects.external ⟹ checkpoint == before-side-effect
    side = raw.get("sideEffects") or {}
    if side.get("external") is True and side.get("checkpoint") != "before-side-effect":
        errors.append(
            "manifest.sideEffects.external = true 时必须声明 "
            "manifest.sideEffects.checkpoint = 'before-side-effect'"
            "（检查点是出事之后能恢复的前提）"
        )

    # 规则 10：parallelSafe 只能含只读操作
    parallel_safe = concurrency.get("parallelSafe") or []
    bad_ops = sorted(
        op for op in parallel_safe if op not in _READ_ONLY_OPS
    )
    if bad_ops:
        errors.append(
            f"manifest.concurrency.parallelSafe 含非只读操作 {bad_ops}："
            "只有 read / list 可以标为可并发"
        )

    # 规则 11：optimisticLock 不许 false（除非 exclusive）
    if (
        concurrency.get("optimisticLock") is False
        and concurrency.get("exclusive") is not True
    ):
        errors.append(
            "manifest.concurrency.optimisticLock = false 仅在 "
            "manifest.concurrency.exclusive = true 时允许"
            "（非独占状态下关掉乐观锁 = 允许丢失更新）"
        )

    # 规则 14：schedule 选择器互斥
    schedule = raw.get("schedule") or {}
    given = [s for s in _SCHEDULE_SELECTORS if s in schedule]
    if len(given) > 1:
        errors.append(
            f"manifest.schedule 同时给了多个选择器 {given}："
            "at / after_seconds / every_seconds / daily / weekly / cron 必须只给一个"
        )
    if schedule and not given:
        errors.append(
            "manifest.schedule 必须声明且只声明一个选择器"
            "（at / after_seconds / every_seconds / daily / weekly / cron）"
        )
    if "every_seconds" in schedule and schedule["every_seconds"] == 86400:
        errors.append(
            "manifest.schedule.every_seconds = 86400 不能替代 daily："
            "固定间隔对齐创建时刻，daily 对齐墙上时间，是两种语义"
        )

    # 规则 15：entry 指向 /plugin-assets/<id>/... 时，<id> 必须等于 manifest.id
    # ★ 第三方插件的 UI 由后端资产端点 GET /plugin-assets/{id}/{path} 运行时服务，
    #   路径里的 {id} 决定读哪个插件目录下的 web/dist。若允许指向别人的 id，
    #   A 插件就能把自己的界面挂到 B 插件已发布的资产上 —— 既越权读取对方的产物，
    #   又让用户看到的界面来源无法辨别（以为是 B，其实由 A 触发加载）。
    entry = raw.get("entry")
    if isinstance(entry, str) and entry.startswith(_PLUGIN_ASSET_PREFIX):
        asset_id = entry[len(_PLUGIN_ASSET_PREFIX):].split("/", 1)[0]
        owner = raw.get("id")
        if asset_id != owner:
            errors.append(
                f"manifest.entry 指向 {_PLUGIN_ASSET_PREFIX}{asset_id}/... "
                f"但 manifest.id = {owner!r}：插件只能引用自己 id 下的运行时资产"
                "（指向别人的 id 等于越权读取对方 web/dist，来源也不可辨）"
            )

    # 规则 16：entry 若是 URL，scheme 只能是 http / https
    # ★ entry 会被前端直接 import()/动态加载，scheme 白名单是注入面的第一道闸：
    #   javascript: 是可执行注入、data: 可内联任意脚本、file: 能读本地文件。
    #   schema 的 pattern 已经拦下这些形状，这条是**纵深防御** ——
    #   将来若为兼容而放宽 pattern，这条规则不会跟着失效。
    if isinstance(entry, str) and entry:
        if entry.startswith("//"):
            # 协议相对 URL：继承页面 scheme，实际只可能是 http(s)；但没有主机名就是空指向
            if not entry[2:].split("/", 1)[0]:
                errors.append(
                    f"manifest.entry = {entry!r} 是协议相对 URL 却缺少主机名"
                    "（应形如 //cdn.example.com/ui/index.js）"
                )
        else:
            scheme_match = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*):", entry)
            if scheme_match and scheme_match.group(1).lower() not in _ALLOWED_ENTRY_SCHEMES:
                errors.append(
                    f"manifest.entry 的 URL scheme 为 {scheme_match.group(1).lower()!r}，"
                    "只允许 http / https：entry 会被前端 import()，"
                    "javascript: / data: / file: 等 scheme 属于代码注入面"
                )

    return errors


# ══════════════════════════════════════════════════════════════════════
# 公开入口
# ══════════════════════════════════════════════════════════════════════


def validate_manifest(raw: dict[str, Any]) -> dict[str, Any]:
    """校验 manifest 字典。

    顺序（协议报告 §19）：① 结构校验失败即停；② 跨字段一致性继续查。
    ② 不因为 ① 失败而跳过——但 ① 失败时 ② 的结论多半无意义，
    故只有 ① 通过才跑 ②（结构错了往下查没意义）。
    """
    errors: list[str] = []
    _validate_node(raw, _SCHEMA, "manifest", errors)
    if errors:
        raise ManifestError(
            "manifest 校验失败（对照 contracts/plugin.schema.json）：\n  - "
            + "\n  - ".join(errors)
        )

    consistency = _check_consistency(raw)
    if consistency:
        raise ManifestError(
            "manifest 跨字段一致性校验失败：\n  - "
            + "\n  - ".join(consistency)
        )
    return raw


def validate_manifest_text(text: str, source: str = "manifest.json") -> dict[str, Any]:
    """从文本校验（顺便检查 JSON 是否合法）。"""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{source} 不是合法 JSON：{exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{source} 根必须是对象")
    return validate_manifest(raw)
```

### 5.4 ④ 校验器第三层（跨插件一致性）：`services/api/core/plugins/crosscheck.py`（208 行）

```python
# ── ③ 跨插件层 ──
"""跨插件一致性校验（协议报告 §19 第③层）。

★ 与 core/plugins/validate.py 的分工：
  validate.py  →  ①②：单插件的形状与内部一致性（不需要知道别的插件）
  crosscheck.py →  ③：跨插件一致性（必须等全部插件发现完才能判）

★ 为什么必须单独一层、且必须等全量：
  「这个能力有没有人提供」「这个能力是不是被两个人提供了」这类问题，
  答案可能出现在扫描顺序的后面。边发现边校验会漏判。

★ 与 core/manifest.py 的旧闸门的关系：
  旧闸门（_gate_dependency_declarations）已实现①有人 provides、③无循环硬依赖、
  ④软硬互斥。本模块补上它缺的、并按协议收紧的：
    - 规则 12：provides 的每个能力必须有人 defines（三角色的严格模式）
    - 规则 13：同一能力被多个插件 provides（**不阻断启动**，见下）
  旧的①③④继续沿用，本模块不重复实现。

★★ 规则 13 的口径（2026-10-28 修订，取代旧口径）：
  旧口径是「同一能力被两个插件 provides ⇒ 硬报错、启动失败」。
  修订后**不再阻断启动**，改为「登记为待裁决冲突」。

  为什么改（旧口径会死锁）：
    真实启动顺序是「发现插件 → 跑本模块 → 激活插件 → 包层装配」。
    而「谁提供这个能力、选谁」这个决定，只有**包层**能做
    （包是唯一知道「包里所有插件」的实体）。
    ⇒ 旧口径等于让内核在包层说话之前就把系统判死，
      报错还指向一个「尚未被读到」的字段。

  三条判据必须分清（这是本模块最重要的一段）：
  | 情形 | 处置 | 理由 |
  |---|---|---|
  | 同一能力被多个插件 provides | **登记待裁决，不阻断** | 内核对「多 Provider」没有立场；有包就由包裁决 |
  | 某能力没有任何 provider，但有人 requires 它 | **阻断** | 这是真缺能力，没有第二种解释 |
  | 一个能力被两个插件 defines | **阻断** | 能力缝二义，无人能裁决 |

  ⇒ 本模块**只返回违规列表**（阻断性的那些）；
    「待裁决冲突」走 `provider_conflicts()` 单独出口，不混进违规列表。
"""
from __future__ import annotations

from typing import Any

from core.errors import ManifestError
from core.logging import get_logger

log = get_logger("kernel.manifest")


def _defines_of(manifest: dict[str, Any]) -> list[str]:
    return [str(c) for c in (manifest.get("defines") or [])]


def _provides_of(manifest: dict[str, Any]) -> list[str]:
    return [str(c) for c in (manifest.get("provides") or [])]


def crosscheck_manifests(
    manifests: list[dict[str, Any]],
    *,
    selection: dict[str, str] | None = None,
) -> list[str]:
    """跨插件一致性校验。返回违规描述列表（空列表 = 全部通过）。

    ★ 这是 `crosscheck_detailed` 的「只要人话」视图（存量调用方与协议测试
      都按 list[str] 断言，签名与语义保持不变）。需要**归因到插件**的调用方
      （发现层要把违规落成某个插件的错误项，而不是让全局炸掉）请用
      `crosscheck_detailed`——不要反过来解析这里的字符串。

    ★ 规则 12 / 13 的判定依赖「全局是否有插件写了 defines」：
      - 全系统**没有任何**插件写 defines → 兼容模式（provides 隐含 defines）
      - 一旦有任一插件写了 defines → 全系统切严格模式
    ★ 为什么是「全系统一次性切换」而不是「按插件决定」：
      如果允许 A 兼容模式、B 严格模式，那「B 的 provides 需不需要有人 defines」
      就有两个答案，校验结果取决于检查顺序 —— 不可接受。

    ★ `selection` 是**能力路由的显式选定**（能力名 → 选中的提供者插件 id）。
      它由**包层**提供（`pack.json` 的 `providerChoice`），
      因为包是唯一知道「包里所有插件」的实体。
      作用：让「多 Provider」这件事在**装配期**有答案，
      从而使规则 13 不必在启动期就判死。
      ★ 它**不改变**规则 13 的判定逻辑，只多一个「已被显式选定」的出口。
    """
    return [msg for _ids, msg in crosscheck_detailed(manifests, selection=selection)]


def crosscheck_detailed(
    manifests: list[dict[str, Any]],
    *,
    selection: dict[str, str] | None = None,
) -> list[tuple[tuple[str, ...], str]]:
    """跨插件一致性校验的**结构化**出口：`[(涉事插件 id 元组, 人话描述)]`。

    ★ 为什么要有它（r5 热插拔 · 层0.1）：
      发现层此前把违规直接 `raise`，于是「用户往 plugins/ 丢一个坏插件」
      会让 `GET /plugins`、`/slots`、enable/disable/install **全部 500** ——
      单插件故障被放大成整个管理面瘫痪。热插拔的前提是「插件坏了只坏它自己」，
      而要做到这一点，发现层必须知道**这条违规该记在谁头上**：
      解析人话字符串去猜插件名是不可接受的（措辞一改就静默错归因）。
      ⇒ 归因信息在这里一次算准，随违规一起交出去。

    ★ 判定逻辑与 `crosscheck_manifests` 完全同源（后者就是本函数的字符串视图），
      不存在第二套规则。
    """
    violations: list[tuple[tuple[str, ...], str]] = []

    # ── 收集 defines / provides ──
    definer_of: dict[str, str] = {}  # 能力 → 定义者插件 id
    providers_of: dict[str, list[str]] = {}  # 能力 → 提供者插件 id 列表
    strict_mode = False

    for mf in manifests:
        pid = str(mf.get("id") or "?")
        for cap in _defines_of(mf):
            strict_mode = True
            if cap in definer_of:
                other = definer_of[cap]
                violations.append(
                    (
                        (other, pid),
                        f"能力 {cap!r} 被两个插件 defines（{other} 与 {pid}）："
                        "一个能力最多一个 defines",
                    )
                )
            else:
                definer_of[cap] = pid
        for cap in _provides_of(mf):
            providers_of.setdefault(cap, []).append(pid)

    if not strict_mode:
        # 兼容模式：provides 隐含 defines
        for cap, pids in providers_of.items():
            if cap not in definer_of:
                definer_of[cap] = pids[0]
        return violations

    # ── 规则 12：provides 必须有人 defines（严格模式）──
    for cap, pids in sorted(providers_of.items()):
        if cap not in definer_of:
            violations.append(
                (
                    tuple(pids),
                    f"插件 {pids[0]} provides 了能力 {cap!r}，但没有任何插件 defines 它"
                    "（严格模式：能力必须有明确的所有者）",
                )
            )

    # ── 规则 13：同一能力被多个插件 provides（登记待裁决，不阻断启动）──
    #   ★ 与规则 12 的区别：12 是「能力没有主人」（阻断），
    #     13 是「能力有两个可能的主人」（登记，由包层裁决）。
    #   ★ 为什么不阻断：包层的 `providerChoice` 裁决发生在插件发现**之后**，
    #     在这里判死等于让内核在包层说话之前就把系统判死。
    #   ⇒ 判定实现在 `provider_conflicts()`。
    return violations


def provider_conflicts(
    manifests: list[dict[str, Any]],
    *,
    selection: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """多 Provider 冲突 —— **登记，不阻断**（规则 13 的新口径）。

    返回：能力名 → 该能力的全部提供者插件 id（已按 id 升序）。

    ★ 被 `selection` 显式选定的能力**不在返回里**：
      那个决定已经做出（由包层做），不算冲突。
    ★ 只有一个提供者的能力**不在返回里**：不是冲突。

    ★ 为什么单独一个出口而不是塞进 `crosscheck_manifests` 的违规列表：
      违规列表 = 「启动失败的理由」。而多 Provider 在没有包裁决时
      **不是启动失败的理由**（能力标 unavailable 而已）。
      两者混在一起 ⇒ 调用方无法区分「必须停」与「必须记」。
    """
    # 已被显式选定的能力：只保留选中的那一个作为「有效提供者」，
    # 其余不再构成冲突。
    chosen = dict(selection) if selection else {}

    providers_of: dict[str, list[str]] = {}
    for mf in manifests:
        pid = str(mf.get("id") or "?")
        for cap in _provides_of(mf):
            providers_of.setdefault(cap, []).append(pid)

    conflicts: dict[str, list[str]] = {}
    for cap, pids in sorted(providers_of.items()):
        if cap in chosen:
            # 已被选定：其余提供者本期不激活（能力级处置，不改插件状态）
            continue
        if len(pids) > 1:
            conflicts[cap] = sorted(pids)
    return conflicts


def assert_crosscheck(
    manifests: list[dict[str, Any]],
    *,
    selection: dict[str, str] | None = None,
) -> None:
    """跨插件一致性校验；有任何**阻断性**违规则抛 ManifestError（★ 列出全部违规）。

    ★ 多 Provider 不在阻断之列（见 `provider_conflicts`）。
    """
    violations = crosscheck_manifests(manifests, selection=selection)
    if violations:
        raise ManifestError(
            "跨插件一致性校验失败：\n  - " + "\n  - ".join(violations)
        )
```

### 5.5 ⑤ 接入点（发现与挂载）：`services/api/core/plugins/discover.py`（566 行）

```python
# ── 三层校验的串接点 ──
"""插件发现：扫描内置(services/api/modules/*) + 第三方(plugins/*) 的 manifest（总纲 §1.3）。

★ 加载器对两者一视同仁——除了 uninstall 只对 third-party 开放（见 manager）。
本模块负责"找到 + 校验 + 标记来源"，并提供**统一挂载入口** mount_plugin()：
幂等检查 + 统一路由加载 + 委托 ModuleRegistry 落挂。前置小步——
启动全量挂载（create_app）与运行时启停（enable/install）共用同一入口，
不再各写一份。

校验三层：
  1. JSON 合法；
  2. 对照 contracts/plugin.schema.json 强校验（精确到字段）；
  3. kernelApi 与当前内核接口版本兼容（不兼容明确拒绝，不许静默）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.logging import get_logger
from core.errors import ManifestError
from core.manifest import load_router
from core.plugins.crosscheck import crosscheck_detailed
from core.plugins.validate import validate_manifest

log = get_logger("kernel.plugins.discover")

_API_ROOT = Path(__file__).resolve().parents[2]  # services/api/
_DEFAULT_MODULES_DIR = _API_ROOT / "modules"
_DEFAULT_PLUGINS_DIR = _API_ROOT.parent.parent / "plugins"  # 项目根/plugins

#: 用户数据里的插件根子目录名（相对数据目录）。
#: ★ 与包层 `packs/roots.py` 的 `DATA_DIRNAME="packs"` 对称：
#:   发行版自带的东西在仓库里（只读），用户自己装的东西在数据目录里（可写）。
#:   容器部署只挂 `./data`，装进仓库根的东西重启即丢 —— 这条对称不是美学，
#:   是「装上去的插件能活过重启」的前提。
DATA_DIRNAME = "plugins"


def data_plugins_root(data_dir: Path) -> Path:
    """用户数据里的插件根（`<data_dir>/plugins/`）——运行时安装的落点。"""
    return Path(data_dir) / DATA_DIRNAME


def default_data_plugins_root() -> Path:
    """默认数据插件根：数据根 = 数据库所在目录（与包层同一真相源）。

    ★ 为什么不新增 `PLUGINS_DIR` 配置项：`packs.boot.resolve_data_dir` 的
      理由同样适用 —— 数据库/备份/日志/包/插件本来就在同一个 `data/` 下，
      各配各的迟早分叉（改了 DB_PATH 忘了改插件目录 ⇒ 插件在一个地方、
      状态在另一个地方）。⇒ 一个真相源。
    """
    from core.config import get_settings
    from packs.boot import resolve_data_dir

    return data_plugins_root(resolve_data_dir(get_settings().db_path))


@dataclass(frozen=True)
class PluginInfo:
    """一个被发现、且通过校验的插件。"""

    id: str
    kind: str  # core | builtin | third-party
    source: str  # "builtin"(modules/) | "third-party"(plugins/)
    manifest: dict[str, Any]
    directory: Path
    compat_status: str = "ok"  # "ok" | "degraded" | "failed"


@dataclass(frozen=True)
class PluginError:
    """一个被发现、但校验失败的插件（id 可能拿不到）。"""

    directory: Path
    source: str
    message: str


class DiscoveryResult:
    def __init__(
        self, plugins: list[PluginInfo], errors: list[PluginError]
    ) -> None:
        self.plugins = plugins
        self.errors = errors

    @property
    def ok(self) -> bool:
        return not self.errors


def _read_manifest(manifest_path: Path, source: str) -> tuple[dict[str, Any], PluginError | None]:
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return {}, PluginError(manifest_path.parent, source, f"manifest 不是合法 JSON：{exc}")
    except OSError as exc:
        return {}, PluginError(manifest_path.parent, source, f"读取 manifest 失败：{exc}")

    if not isinstance(raw, dict):
        return {}, PluginError(manifest_path.parent, source, "manifest 根必须是对象")

    try:
        validate_manifest(raw)
    except Exception as exc:  # validate_manifest 抛 ManifestError；也兜底其它
        return raw, PluginError(manifest_path.parent, source, f"契约校验失败：{exc}")

    pid = raw["id"]
    dir_name = manifest_path.parent.name
    if pid != dir_name:
        return raw, PluginError(
            manifest_path.parent, source,
            f"manifest.id={pid!r} 必须等于目录名 {dir_name!r}",
        )

    # ★ kind ↔ 来源一致性（r5 热插拔 · 层1.5）
    #   此前内核不校验这两者是否自洽：把一个 `kind=builtin` 的目录丢进
    #   plugins/ 下，`source` 仍是 third-party，但 install()/uninstall()
    #   都按 kind 拒掉它 ⇒ **既装不了也卸不掉**，只能靠 enable 挂上，
    #   成为一条没人管的缝。热插拔必须堵住它：外来目录只许自称 third-party。
    #   （container 允许：容器型是纯前端编排，放在用户目录里同样合法。）
    if source == "third-party" and raw["kind"] in ("core", "builtin"):
        return raw, PluginError(
            manifest_path.parent, source,
            f"第三方插件目录里的 manifest.kind={raw['kind']!r} 不合法："
            "core/builtin 随系统发布，只能住在 services/api/modules/；"
            "放在插件根下的插件必须声明 kind='third-party'（或 container）",
        )

    # ★ G1：minKernel 校验（内核版本不满足时降级，不拒绝）
    min_kernel = raw.get("minKernel")
    if min_kernel:
        # ★ 2026-09-25 修：minKernel 是"最低版本"语义（>=），不是 caret/精确。
        #   此前误用 satisfies() → 所有 minKernel="0.1.0" 的插件在 1.0.0 内核上
        #   一律 degraded → 跳过激活 → 路由不挂载（踩到）。
        from core.plugins.version import KERNEL_API_VERSION, meets_min_kernel
        try:
            if not meets_min_kernel(min_kernel):
                raw["_compat_status"] = "degraded"
                raw["_compat_reason"] = f"minKernel={min_kernel} 不满足，当前 {KERNEL_API_VERSION}"
        except Exception:
            raw["_compat_status"] = "degraded"
            raw["_compat_reason"] = f"minKernel={min_kernel} 校验失败"

    # ★ G2：kernelApi 不兼容时降级而非 fail-fast（台账 #026 要求：个人系统一插件坏不应带崩全坞）
    try:
        from core.plugins.version import check_compatibility
        check_compatibility(raw["kernelApi"])
    except Exception as exc:
        raw["_compat_status"] = "degraded"
        raw["_compat_reason"] = f"kernelApi 不兼容：{exc}"

    return raw, None


def _scan(root: Path, source: str) -> tuple[list[PluginInfo], list[PluginError]]:
    plugins: list[PluginInfo] = []
    errors: list[PluginError] = []
    if not root.is_dir():
        return plugins, errors
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest_path = sub / "manifest.json"
        if not manifest_path.is_file():
            continue
        raw, err = _read_manifest(manifest_path, source)
        if err is not None:
            errors.append(err)
            continue
        plugins.append(
            PluginInfo(
                id=raw["id"],
                kind=raw["kind"],
                source=source,
                manifest=raw,
                directory=sub,
                compat_status=raw.get("_compat_status", "ok"),
            )
        )
    return plugins, errors


def discover_plugins(
    modules_dir: Path | None = None,
    plugins_dir: Path | None = None,
    *,
    data_plugins_dir: Path | None = None,
) -> DiscoveryResult:
    """扫描内置与第三方插件，返回通过校验的插件列表 + 失败清单。

    ★ 校验三层（协议报告 §19）：
      ① 结构校验   —— core/plugins/validate.py（单插件形状）
      ② 跨字段一致性 —— 同上（单插件内部矛盾）
      ③ 跨插件一致性 —— core/plugins/crosscheck.py（需全量插件，故在此处统一跑）
    ★ ③ 必须等全量扫描结束才能判：「这个能力有没有人 provides / defines」
      的答案可能出现在扫描顺序的后面。

    ★★ 三根发现（r5 热插拔 · 层1.1）：
      | 根 | source | 可写性 | 用途 |
      |---|---|---|---|
      | `services/api/modules/` | builtin | 只读（随系统发布） | 内置模块 |
      | `<data_dir>/plugins/` | third-party | **可写** | 运行时安装的落点 |
      | 仓库根 `plugins/` | third-party | 只读兼容 | 发行版自带 / 开发者手放 |
      数据根**优先**：同一个 id 在两处都有时，用户自己装的那份赢
      （与 `packs.roots.resolve_pack_dir` 同口径）。仓库根那份被跳过并
      留一条 info 日志 —— 静默丢弃会让「我明明放了却没生效」变成悬案。

    ★★ ③ 的违规**不再 raise**（r5 热插拔 · 层0.1）：
      旧实现在这里直接抛 ManifestError，而 `PluginManager.discover()` /
      `_require()` 全部裸调它 ⇒ 用户往插件根丢一个坏插件，
      `GET /plugins`、`/slots`、enable/disable/install **全部 500**。
      单插件故障被放大成整个管理面瘫痪，这与热插拔的前提正好相反。
      现在违规按**涉事插件**落成 `errors` 项（归因由 crosscheck_detailed
      结构化给出，不解析人话字符串），涉事插件被剔出 plugins 列表，
      其余插件照常工作。⇒ 「插件坏了只坏它自己」。
      ★ 为什么不选「保留插件但标 degraded」：规则 12/13 的违规是
        **能力归属不明**，让它继续挂载等于让一个没有主人的能力对外服务，
        调用方会拿到看似成功实则无主的结果。剔出 + 明示错误更诚实。
    """
    root = Path(modules_dir) if modules_dir else _DEFAULT_MODULES_DIR
    plug = Path(plugins_dir) if plugins_dir else _DEFAULT_PLUGINS_DIR
    data_root = (
        Path(data_plugins_dir)
        if data_plugins_dir is not None
        else default_data_plugins_root()
    )
    builtins, b_errs = _scan(root, "builtin")
    # 数据根优先扫；仓库根里与它同 id 的目录让位（见 docstring）。
    data_party, d_errs = _scan(data_root, "third-party")
    bundled_party, p_errs = _scan(plug, "third-party")
    taken = {p.id for p in data_party}
    third_party: list[PluginInfo] = list(data_party)
    for p in bundled_party:
        if p.id in taken:
            log.info(
                "插件 %s 在数据根与发行根各有一份，采用数据根那份（用户安装的优先）",
                p.id,
                extra={"module": p.id, "shadowed": str(p.directory)},
            )
            continue
        third_party.append(p)
    plugins = builtins + third_party

    # ── ③ 跨插件一致性（规则 12 / 13）──
    #   违规按涉事插件归因，落成错误项而不是全局抛（层0.1）。
    errors = b_errs + d_errs + p_errs
    if plugins:
        detailed = crosscheck_detailed([p.manifest for p in plugins])
        if detailed:
            by_id = {p.id: p for p in plugins}
            blamed: set[str] = set()
            for ids, message in detailed:
                # 归因到具体插件；找不到对应目录时（理论上不该发生）
                # 仍要留下痕迹，绝不静默吞掉违规。
                hit = [by_id[i] for i in ids if i in by_id]
                if not hit:
                    errors.append(
                        PluginError(root, "builtin", f"跨插件一致性：{message}")
                    )
                    continue
                for info in hit:
                    blamed.add(info.id)
                    errors.append(
                        PluginError(info.directory, info.source, f"跨插件一致性：{message}")
                    )
            plugins = [p for p in plugins if p.id not in blamed]

    return DiscoveryResult(plugins=plugins, errors=errors)


def load_python_module_from_file(path: Path, module_name: str) -> Any:
    """按文件路径加载一个 .py 模块（用于第三方插件的 router / models，不走包 import）。"""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载插件模块：{path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001 — 插件自己的代码出错，一律归成「插件加载失败」
        # ★ 为什么必须包一层：插件代码抛什么（ModuleNotFoundError、SyntaxError、
        #   业务异常……）内核管不了，但**对外必须是一个统一的应用级错误**。
        #   否则调用方要区分「插件坏了」和「内核坏了」，而后者根本不是。
        #   原始异常类型与信息都带在消息里，不丢诊断线索。
        raise ManifestError(
            f"插件模块加载失败：{path}（{type(exc).__name__}: {exc}）"
        ) from exc
    return mod


def thirdparty_package_name(plugin_id: str) -> str:
    """第三方插件的**包名**（`api/` 目录作为一个包）。

    ★ 连字符保留原样：包名只被 importlib 的字符串运算使用（相对导入解析
      走 `__package__` 字符串），不经 `import` 语句的词法分析，故 `pi-agent`
      这类 id 可以安全做包名。换成下划线反而会与「id == 目录名」的口径分叉。
    """
    return f"plugin_thirdparty_{plugin_id}"


def _ensure_thirdparty_package(info: PluginInfo) -> str:
    """把插件的 `api/` 目录注册成一个**包**，返回包名（幂等）。

    ★ 为什么必须这么做（r5 热插拔 · 层3.6）：
      旧实现用 `spec_from_file_location(name, router.py)` 加载路由，
      **没有给 submodule_search_locations** ⇒ 模块不属于任何包 ⇒
      `from .models import X` 抛「attempted relative import with no known
      parent package」。于是第三方插件作者被迫手写路径加载
      （`plugins/countdown/api/router.py` 把这条写成红线给下一个人抄），
      而**多文件插件根本写不出来** —— 学长的 sdu-feeds 就是多文件的。
      ⇒ 热插拔要正经起来，必须让第三方与内置一样能用相对导入。

    ★ 做法：先造一个父包模块（`__path__` 指向 api/），再让 router 以
      `<pkg>.router` 的身份加载。这样相对导入由 importlib 正常解析，
      兄弟模块（models/schema/service）各自只加载一次并进 sys.modules ——
      SQLAlchemy 的 Table 也因此只有一份（重复定义会炸）。
    """
    pkg_name = thirdparty_package_name(info.id)
    api_dir = info.directory / "api"
    existing = sys.modules.get(pkg_name)
    if existing is not None and getattr(existing, "__path__", None) == [str(api_dir)]:
        return pkg_name
    spec = importlib.util.spec_from_file_location(
        pkg_name,
        api_dir / "__init__.py",
        submodule_search_locations=[str(api_dir)],
    )
    if spec is None or spec.loader is None:
        # 没有 __init__.py 也能成包：spec_from_file_location 对不存在的
        # __init__ 仍会给出带 search_locations 的 spec；真拿不到才报错。
        raise ManifestError(f"插件「{info.id}」无法把 {api_dir} 注册为包")
    pkg = importlib.util.module_from_spec(spec)
    pkg.__path__ = [str(api_dir)]  # type: ignore[attr-defined]
    pkg.__package__ = pkg_name
    sys.modules[pkg_name] = pkg
    init_py = api_dir / "__init__.py"
    if init_py.is_file():
        try:
            spec.loader.exec_module(pkg)
        except Exception as exc:  # noqa: BLE001
            sys.modules.pop(pkg_name, None)
            raise ManifestError(
                f"插件包初始化失败：{init_py}（{type(exc).__name__}: {exc}）"
            ) from exc
    return pkg_name


def unload_thirdparty_modules(plugin_id: str) -> None:
    """把某第三方插件在 sys.modules 里的包与子模块清掉（幂等）。

    ★ 为什么卸载必须清（r5 · 层3.5）：目录已被 rmtree，但模块对象还在
      sys.modules 里 —— 旧代码仍可被调用，且同 id 重装时会拿到**上一版的
      兄弟模块**（router 每次重新 exec，models 却复用缓存）⇒ 新路由配旧表模型，
      是最难查的一类不一致。清掉之后重装是一次干净的新加载。

    ★ reload 也用这个（r8）：不清就等于「只重载了 router.py，models/service
      还是旧的」，而那恰恰是升级最常改的文件。
      清之前请先 `detach_plugin_tables()`，否则重新 exec models.py 会撞
      「表已定义」——见那个函数的说明。
    """
    pkg_name = thirdparty_package_name(plugin_id)
    prefix = pkg_name + "."
    for name in [n for n in sys.modules if n == pkg_name or n.startswith(prefix)]:
        sys.modules.pop(name, None)
    # 旧命名（层3.6 之前）留下的条目也一并清，避免升级后残留。
    sys.modules.pop(f"{pkg_name}_router", None)


def _plugin_module_names(plugin_id: str) -> list[str]:
    """该第三方插件当前在 sys.modules 里的全部模块名（包 + 子模块）。"""
    pkg_name = thirdparty_package_name(plugin_id)
    prefix = pkg_name + "."
    return [n for n in list(sys.modules) if n == pkg_name or n.startswith(prefix)]


def detach_plugin_tables(plugin_id: str) -> dict[str, Any]:
    """把该插件已注册到 `SQLModel.metadata` 里的表摘下来，返回可恢复的现场。

    ★ 为什么重新加载前必须摘（r8 实测，不是推理）
    ------------------------------------------------
    `SQLModel.metadata` 是**进程级全局**的（db/base.py 的单一 metadata 设计）。
    `api/models.py` 里每个 `class X(table=True)` 在 exec 时把表注册进去。
    于是「清 sys.modules + 重新 exec models.py」会撞：

        InvalidRequestError: Table 'xxx' is already defined for
        this MetaData instance.

    ⇒ 热重载在**改动了 models.py 的插件**上一律失败，而那正是升级最需要的场景。
      实测复现：造一个两表（带外键）的插件连续 exec 两次 → 第二次必炸；
      先 `metadata.remove(table)` 再 exec → 成功，且外键目标重建正确。

    ★ 归属判据为什么用「模块归属」而不是「表名前缀」
    ------------------------------------------------
    表名前缀（`table_prefix(plugin_id)`）有包含关系：插件 `foo` 的前缀 `foo_`
    会匹配到插件 `foo-bar` 的表 `foo_bar_x` —— 摘错人的表，比不摘更糟。
    而 `cls.__module__ == plugin_thirdparty_<id>.models` 是**精确**归属，
    与「代码是谁 exec 出来的」这一事实同源。

    ★ 返回值的用途：装载失败时把表放回去（`restore_plugin_tables`），
      否则 metadata 里永久少几张表（`create_all` / 字典校验会静默漏掉它们）。
    """
    saved: dict[str, Any] = {"tables": {}, "module_names": _plugin_module_names(plugin_id)}
    names = set(saved["module_names"])
    if not names:
        return saved
    try:
        from sqlmodel import SQLModel
    except Exception:  # noqa: BLE001 —— sqlmodel 不在场时无表可摘（不该发生，但不拦）
        return saved
    meta = SQLModel.metadata
    for mod_name in names:
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for attr in list(vars(mod).values()):
            table = getattr(attr, "__table__", None)
            # ★ 只收「归属这个插件、且真的在这个 metadata 里」的表。
            if table is None or getattr(table, "metadata", None) is not meta:
                continue
            if getattr(attr, "__module__", None) not in names:
                continue
            if table.name in saved["tables"]:
                continue
            saved["tables"][table.name] = table
    for table_name, table in list(saved["tables"].items()):
        try:
            meta.remove(table)
        except Exception as exc:  # noqa: BLE001 —— 一张摘不掉不该阻断其余
            log.warning(
                "插件表从 metadata 摘除失败（已跳过该表）",
                extra={"plugin_id": plugin_id, "table": table_name, "error": str(exc)},
            )
            saved["tables"].pop(table_name, None)
    return saved


def restore_plugin_tables(saved: dict[str, Any]) -> None:
    """把 `detach_plugin_tables` 摘下来的表放回 `SQLModel.metadata`（幂等）。

    ★ 只在**重新装载失败**时调用：那是唯一「旧代码还要继续用这些表描述」
      的场合。装载成功时新代码已经把自己的表注册好了，再放回去就是重复注册
      （`to_metadata` 对同名表会抛）。
    """
    tables = saved.get("tables") or {}
    if not tables:
        return
    try:
        from sqlmodel import SQLModel
    except Exception:  # noqa: BLE001
        return
    meta = SQLModel.metadata
    for table_name, table in tables.items():
        if table_name in meta.tables:
            continue
        try:
            table.to_metadata(meta)
        except Exception as exc:  # noqa: BLE001 —— 恢复失败只记，不掩盖原始错误
            log.warning(
                "插件表恢复到 metadata 失败",
                extra={"table": table_name, "error": str(exc)},
            )


def purge_plugin_bytecode(directory: Path) -> int:
    """删掉插件目录树里的 `__pycache__`（reload 前置），返回删掉的文件数。

    ★ 为什么必须删（r8 实测，不是推理）
    ----------------------------------
    Python 的源加载器按「mtime(秒) + 字节数」判断 .pyc 是否新鲜。
    于是「同一秒内、且字节数恰好相同」的改写（最典型：改一个返回值 `v1` →
    `v2`）会**命中过期的字节码**：

        v1 → v2（同秒同长度）→ 重新 exec → 拿到的还是 v1 的 .pyc
        `importlib.invalidate_caches()` 救不了（它只清 finder 的目录缓存），
        只有把 __pycache__ 删掉才认新源码。

    这是「改了代码没生效」的经典表象，而且**几乎必踩**：
    用户改一行常量再点升级，编辑器写盘和 reload 请求常落在同一秒。

    ★ 为什么放在 reload 而不是 uninstall：卸载是删整棵目录，
      .pyc 跟着一起走。reload 是「原地换血」，目录还在，
      旧字节码不删就一直在 —— 所以这条纪律必须由 reload 自己执行。
    """
    import shutil

    removed = 0
    try:
        for pyc_dir in sorted(directory.rglob("__pycache__")):
            shutil.rmtree(pyc_dir, ignore_errors=True)
            removed += 1
    except OSError as exc:
        log.warning(
            "插件字节码缓存清理失败（已记，不阻断）",
            extra={"directory": str(directory), "error": str(exc)},
        )
    import importlib

    importlib.invalidate_caches()
    return removed


def load_plugin_router(info: PluginInfo) -> Any:
    """加载某插件的 APIRouter。

    builtin/core：走内核的包 import（modules.<id>.router）。
    third-party：把 `api/` 注册成包后加载 `<pkg>.router`（层3.6）——
      于是 `from .models import X` 可用，多文件第三方插件写得出来。
    """
    if info.source == "third-party":
        router_path = info.directory / "api" / "router.py"
        if not router_path.is_file():
            raise ManifestError(f"插件「{info.id}」缺少 api/router.py")
        pkg_name = _ensure_thirdparty_package(info)
        mod = load_python_module_from_file(router_path, f"{pkg_name}.router")
        # 旧模块名保留一个别名：存量插件里有按 `plugin_thirdparty_<id>_router`
        # 取 sys.modules 的写法（countdown/pi-agent 的注释即以它为约定）。
        sys.modules[f"{pkg_name}_router"] = mod
        router = getattr(mod, "router", None)
        if router is None:
            raise ManifestError(f"插件「{info.id}」router.py 未定义 router = APIRouter()")
        return router
    # builtin / core：复用内核的包 import 逻辑（与 core.manifest.load_router
    # 同一实现，不再各写一份；对 modules/ 下的模块两者逐字等价）
    return load_router(info.id)


def mount_plugin(info: PluginInfo, registry: Any) -> None:
    """统一挂载入口（前置小步）：幂等 + 统一路由加载 + 委托挂载。

    启动全量挂载（create_app）与运行时启停（PluginManager.enable/install）
    共用本入口。已挂载时静默返回（幂等），重复调用安全。

    ★ 数据作用域只给 `third-party`（2026-11-09）：
      内置模块与内核是系统的一部分，模块间本来就有跨模块读；
      给它们挂作用域依赖会把正常功能拦死（详见 `core/registry.py:mount` 的说明）。
    """
    if info.id in registry.mounted():
        return
    # ★ 纯数据插件（皮肤插件 v1）：清单可以没有 api —— 它没有任何接口要暴露。
    #   契约层已限定只有「container」或「有 skin、entry=""、provides 为空」的插件
    #   可以省 api，所以这里不是给任意插件开后门。
    #   仍然登记为「已挂载」是必须的：否则每次 enable 都会重复尝试挂载、
    #   每次都撞同一个错（「缺少 api/router.py」），phase 永远起不来。
    #   空 router + 空 prefix 不产生任何路由，OpenAPI 里也不会多出东西。
    if "api" not in info.manifest:
        from fastapi import APIRouter as _APIRouter

        registry.mount(info.id, _APIRouter(), prefix="", scoped=False)
    else:
        router = load_plugin_router(info)
        registry.mount(
            info.id,
            router,
            prefix=info.manifest["api"]["base"],
            scoped=(info.kind == "third-party"),
        )
    # ★ 挂载即登记发布端声明（2026-11-10）：运行期 enable / install 的第三方插件
    #   走这条路径进系统，它们的 `emits` 必须在这时进总线注册表，
    #   否则第一条事件就会撞上 UndeclaredPublication。幂等。
    from core.events import event_bus  # 局部 import：避免 discover ⇄ events 的导入期循环

    event_bus.declare_publisher(info)
```

### 5.6 ⑥ 门禁测试网：`services/api/tests_protocol/test_protocol_gates.py`（358 行）

```python
# ── 规则的存活证明 ──
"""协议门禁回归测试（Alongside 插件协议 v1.0 · 规则表 §20）。

★ 意义：协议报告里的每条规则都必须有对应的机器测试。
  没有测试的规则 = 下次重构就会被改掉。

★ 分两部分：
  第一部分 · 单插件（①②层）—— core/plugins/validate.py
  第二部分 · 跨插件（③层）—— core/plugins/crosscheck.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

# ── 直接加载内核模块（绕开 core.plugins.__init__ 的 sqlmodel 依赖）──
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))


def _load(module_name: str, relpath: str) -> Any:
    """按文件加载内核模块（不触发包 __init__ 的重依赖）。

    ★ 注意：这里**不能**把假的 core.errors 永久留在 sys.modules 里。
      同一 pytest 进程里其它测试会 import core.errors（要 AppError 等真类），
      留一个只有 ManifestError 的假模块进去会污染它们，导致
      「cannot import name 'AppError'」这类与本文件无关的失败。
      所以：加载完立刻把 sys.modules 恢复原状。
    """
    err_mod = types.ModuleType("core.errors")

    class ManifestError(Exception):
        pass

    # ★ 动态挂属性：给 ModuleType 挂 ManifestError 是测试夹具的常规做法。
    #   mypy 报「ModuleType 没有该属性」——它不知道 ModuleType 允许动态属性。
    #   这里用 type: ignore 精确豁免这一处，而不是改 ruff（ruff 反而反对 setattr）。
    err_mod.ManifestError = ManifestError  # type: ignore[attr-defined]
    real_errors = sys.modules.get("core.errors")
    sys.modules["core.errors"] = err_mod
    try:
        spec = importlib.util.spec_from_file_location(module_name, relpath)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        # 恢复现场：装了真的就还回去，没装过就删掉
        if real_errors is None:
            sys.modules.pop("core.errors", None)
        else:
            sys.modules["core.errors"] = real_errors
    return mod


_validate = _load("gate_validate", str(_API_ROOT / "core/plugins/validate.py"))
_crosscheck = _load("gate_crosscheck", str(_API_ROOT / "core/plugins/crosscheck.py"))

validate_manifest = _validate.validate_manifest
crosscheck_manifests = _crosscheck.crosscheck_manifests
provider_conflicts = _crosscheck.provider_conflicts


BASE: dict[str, Any] = {
    "id": "demo",
    "name": "演示插件",
    "version": "1.0.0",
    "kind": "builtin",
    "kernelApi": "^1",
    "api": {"base": "/api/v1/demo"},
    "provides": [],
    "requires": [],
    "slots": [],
    "emits": [],
    "consumes": [],
    "permissions": [],
}


def _mf(**mutate: Any) -> dict[str, Any]:
    m = copy.deepcopy(BASE)
    m.update(mutate)
    return m


# ═══════════════ 第一部分 · 单插件规则（①②层）═══════════════════════


def test_baseline_manifest_passes() -> None:
    """基线：最小合法 manifest 必须通过。"""
    validate_manifest(_mf())


# ── 规则 2：requires ∩ optionalDependencies = ∅ ──
def test_rule2_hard_and_soft_must_be_disjoint() -> None:
    with pytest.raises(Exception, match="重复声明"):
        validate_manifest(_mf(requires=["a.b.c"], optionalDependencies=["a.b.c"]))


# ── 规则 3：kind != core 时 approval != never ──
def test_rule3_non_core_cannot_use_approval_never() -> None:
    with pytest.raises(Exception, match="approval"):
        validate_manifest(_mf(execution={"approval": "never"}))


def test_rule3_core_may_use_approval_never() -> None:
    validate_manifest(_mf(kind="core", execution={"approval": "never"}))


# ── 规则 4：third-party 不得声明 riskLevel L0 ──
def test_rule4_third_party_cannot_claim_l0() -> None:
    with pytest.raises(Exception, match="riskLevel"):
        validate_manifest(_mf(kind="third-party", riskLevel="L0"))


def test_rule4_builtin_may_lower_risk_level() -> None:
    """推断值可以显式降低（builtin 推断 L2 → 声明 L0 允许）。"""
    validate_manifest(_mf(kind="builtin", riskLevel="L0"))


# ── 规则 5 / 6：origin 完整性 ──
def test_rule5_signed_requires_signature() -> None:
    with pytest.raises(Exception, match="signature"):
        validate_manifest(_mf(origin={"signed": True, "installedBy": "human.user"}))


def test_rule6_origin_requires_installed_by() -> None:
    with pytest.raises(Exception, match="installedBy"):
        validate_manifest(_mf(origin={"source": "local"}))


def test_rule6_complete_origin_passes() -> None:
    validate_manifest(
        _mf(origin={"source": "local", "signed": False, "installedBy": "agent.tester"})
    )


# ── 规则 7 / 8：migrations ⟹ exclusive / track=persistent ──
def test_rule7_migrations_must_be_exclusive() -> None:
    with pytest.raises(Exception, match="exclusive"):
        validate_manifest(
            _mf(migrations="api/migrations", concurrency={"exclusive": False})
        )


def test_rule7_migrations_without_concurrency_is_backward_compatible() -> None:
    """★ 存量插件没有 concurrency 字段 —— 不许因此起不来。

    「字段缺失」按内核推断的保守默认值处理；「显式声明错」才启动失败。
    """
    validate_manifest(_mf(migrations="api/migrations"))


def test_rule8_ephemeral_must_not_declare_migrations() -> None:
    with pytest.raises(Exception, match="persistent"):
        validate_manifest(_mf(migrations="api/migrations", track="ephemeral"))


def test_rule7_and_8_correct_declaration_passes() -> None:
    validate_manifest(
        _mf(
            migrations="api/migrations",
            concurrency={"exclusive": True},
            track="persistent",
        )
    )


# ── 规则 9：sideEffects.external ⟹ checkpoint ──
def test_rule9_external_requires_checkpoint() -> None:
    with pytest.raises(Exception, match="checkpoint"):
        validate_manifest(_mf(sideEffects={"external": True}))


def test_rule9_external_with_checkpoint_passes() -> None:
    validate_manifest(
        _mf(sideEffects={"external": True, "checkpoint": "before-side-effect"})
    )


# ── 规则 10 / 11：并发策略 ──
def test_rule10_parallel_safe_only_read_operations() -> None:
    with pytest.raises(Exception, match="parallelSafe"):
        validate_manifest(_mf(concurrency={"parallelSafe": ["read", "write"]}))


def test_rule11_cannot_disable_optimistic_lock_unless_exclusive() -> None:
    with pytest.raises(Exception, match="optimisticLock"):
        validate_manifest(_mf(concurrency={"optimisticLock": False}))


def test_rule11_exclusive_may_disable_optimistic_lock() -> None:
    validate_manifest(
        _mf(concurrency={"optimisticLock": False, "exclusive": True})
    )


# ── 规则 14：schedule 选择器互斥 ──
def test_rule14_schedule_selectors_are_mutually_exclusive() -> None:
    with pytest.raises(Exception, match="选择器"):
        validate_manifest(
            _mf(
                schedule={
                    "at": "2026-11-01T09:00:00+08:00",
                    "daily": {"time": "09:00:00", "timeZone": "Asia/Shanghai"},
                    "title": "日报",
                }
            )
        )


def test_rule14_every_86400_cannot_replace_daily() -> None:
    """固定间隔对齐创建时刻，daily 对齐墙上时间 —— 是两种语义。"""
    with pytest.raises(Exception, match="86400"):
        validate_manifest(_mf(schedule={"every_seconds": 86400, "title": "日报"}))


def test_rule14_single_schedule_selector_passes() -> None:
    validate_manifest(
        _mf(
            schedule={
                "daily": {"time": "23:00:00", "timeZone": "Asia/Shanghai"},
                "title": "日报",
            }
        )
    )


# ── 结构层：未知字段必须报错（字段名拼错 = 当作没写）──
def test_unknown_field_is_rejected() -> None:
    """★ 协议报告 §21 说明一：requires 拼成 requiers 必须当场报错。

    旧行为是「当作未声明」—— 而「未声明」是合法的，于是没有任何提示，
    排查方向会被误导到权限配置上。
    """
    with pytest.raises(Exception, match="未知字段"):
        validate_manifest(_mf(requiers=["a.b.c"]))


def test_x_prefixed_extension_is_allowed() -> None:
    """扩展位放行（不报不拒）。"""
    validate_manifest(_mf(x_custom="anything"))


def test_container_kind_may_omit_api() -> None:
    """容器型（纯前端编排）可无 api。"""
    m = _mf(kind="container")
    m.pop("api")
    validate_manifest(m)


def test_non_container_without_api_is_rejected() -> None:
    """非 container 型必须声明 api（缺则拒，防静默漏配）。"""
    m = _mf()
    m.pop("api")
    with pytest.raises(Exception, match="api"):
        validate_manifest(m)


# ═══════════════ 第二部分 · 跨插件规则（③层）═════════════════════════


def test_rule12_provides_must_have_definer_in_strict_mode() -> None:
    """一旦有任一插件写了 defines，全系统切严格模式。"""
    manifests = [
        {"id": "a", "defines": ["x.y.z"], "provides": ["x.y.z"]},
        {"id": "b", "provides": ["q.r.s"]},
    ]
    violations = crosscheck_manifests(manifests)
    assert any("defines" in x for x in violations)


def test_rule13_capability_provided_twice_is_registered_not_blocking() -> None:
    """★ 同一能力被两个插件 provides → **登记为待裁决冲突，不阻断启动**。

    ★ 为什么不再阻断（2026-10-28 修订）：
      旧口径是硬报错、进程起不来。但「谁提供这个能力、选谁」这个决定
      **只有包层能做**（包是唯一知道「包里所有插件」的实体），
      而包层的装配发生在**插件发现之后**。
      ⇒ 旧口径等于让内核在包层说话之前就把系统判死，
        报错还指向一个「尚未被读到」的字段。
    ⇒ 新口径：内核登记冲突、不阻断；包层用 `providerChoice` 裁决；
      没裁决 ⇒ 该能力标 unavailable，系统照常起来。
    """
    manifests = [
        {"id": "a", "defines": ["x.y.z"], "provides": ["x.y.z"]},
        {"id": "b", "defines": ["q.r.s"], "provides": ["x.y.z"]},
    ]
    # 不再是启动失败的理由
    assert crosscheck_manifests(manifests) == []
    # 但必须被登记下来（不许静默）
    conflicts = provider_conflicts(manifests)
    assert conflicts == {"x.y.z": ["a", "b"]}


def test_rule13_selection_removes_the_conflict() -> None:
    """★ 已被显式选定（`providerChoice`）⇒ 不再算冲突。

    选定由**包层**提供（包是唯一知道「包里所有插件」的实体）。
    """
    manifests = [
        {"id": "a", "defines": ["x.y.z"], "provides": ["x.y.z"]},
        {"id": "b", "defines": ["q.r.s"], "provides": ["x.y.z"]},
    ]
    conflicts = provider_conflicts(manifests, selection={"x.y.z": "b"})
    assert conflicts == {}


def test_capability_cannot_be_defined_twice() -> None:
    """★ 两个插件 defines 同一能力 → **仍然阻断**。

    能力缝二义，没有任何一层能裁决 ⇒ 必须停。
    """
    manifests = [
        {"id": "a", "defines": ["x.y.z"], "provides": ["x.y.z"]},
        {"id": "b", "defines": ["x.y.z"], "provides": ["x.y.z"]},
    ]
    violations = crosscheck_manifests(manifests)
    assert any("defines" in x for x in violations)


def test_compatibility_mode_when_nobody_defines() -> None:
    """全系统无人写 defines → 兼容模式（provides 隐含 defines），不报错。"""
    manifests = [
        {"id": "a", "provides": ["x.y.z"]},
        {"id": "b", "provides": ["q.r.s"]},
    ]
    assert crosscheck_manifests(manifests) == []


def test_strict_mode_all_defines_and_provides_match() -> None:
    manifests = [
        {"id": "a", "defines": ["x.y.z"], "provides": ["x.y.z"]},
        {"id": "b", "defines": ["q.r.s"], "provides": ["q.r.s"]},
    ]
    assert crosscheck_manifests(manifests) == []


# ═══════════════ 第三部分 · 存量不破（回归硬判据）══════════════════════


def test_all_real_manifests_still_pass() -> None:
    """★ 硬判据：全部真实插件必须在新协议下通过（不误杀存量）。"""
    root = Path(__file__).resolve().parents[1]
    paths = sorted(root.glob("modules/*/manifest.json")) + sorted(
        (root.parent.parent / "plugins").glob("*/manifest.json")
    )
    assert len(paths) >= 18, f"真实插件数异常减少：{len(paths)}"
    for p in paths:
        raw = json.loads(p.read_text(encoding="utf-8"))
        validate_manifest(raw)  # 不抛异常即通过
```

### 5.7 ⑦ 校验器的外部裁判：`tools/crosscheck_validator.py`（473 行）

```python
# ── 校验器可信度的外部证据 ──
#!/usr/bin/env python3
"""交叉验证：自研校验器 vs 标准 JSON Schema 校验器（jsonschema 4.26.0）。

★ 为什么需要这个：
  `core/plugins/validate.py` 是**自己实现**的 JSON Schema 子集。
  自研实现最危险的情况是「看起来对，实际和标准不一致」——
  它会放行标准校验器拒绝的 manifest，或拒绝标准允许的 manifest。
  那种错误在测试里很难被发现：测试用的是同一套实现，自证。

  所以用**标准实现**做裁判：同一份 Schema、同一批样本，
  两边结论必须一致。不一致的地方就是 bug。

★ 判定标准：
  · 标准说合法、我们说合法 → ✓
  · 标准说非法、我们说非法 → ✓
  · 两边结论不同 → ✗ 差异（要么改我们，要么改 Schema）

用法：
    python3 tools/crosscheck_validator.py
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
import types
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO / "contracts" / "plugin.schema.json"
VALIDATE_PATH = REPO / "services/api/core/plugins/validate.py"


def load_our_validator(path: "Path | None" = None):
    """把校验器模块加载起来。``path`` 用于自检时加载**沙箱副本**（绝不碰真源码）。"""
    target = Path(path) if path is not None else VALIDATE_PATH
    err_mod = types.ModuleType("core.errors")

    class ManifestError(Exception):
        pass

    err_mod.ManifestError = ManifestError
    saved = sys.modules.get("core.errors")
    sys.modules["core.errors"] = err_mod
    try:
        spec = importlib.util.spec_from_file_location("_v", target)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载校验器：{target}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            sys.modules.pop("core.errors", None)
        else:
            sys.modules["core.errors"] = saved
    return mod


# ── 样本生成：在合法 manifest 上做随机变异，产生大量「可能非法」的样本 ──

def base_manifest() -> dict[str, Any]:
    return {
        "id": "demo",
        "name": "演示插件",
        "version": "1.0.0",
        "kind": "builtin",
        "kernelApi": "^1",
        "api": {"base": "/api/v1/demo"},
        "provides": [],
        "requires": [],
        "slots": [],
        "emits": [],
        "consumes": [],
        "permissions": {},
    }


# 变异器：每个都制造一类确定的违规
MUTATIONS: list[tuple[str, Any]] = [
    ("id 非法字符", lambda m: m.update(id="Demo_Bad")),
    ("id 数字开头", lambda m: m.update(id="1demo")),
    ("id 空", lambda m: m.update(id="")),
    ("version 非三段", lambda m: m.update(version="1.0")),
    ("version 带前缀", lambda m: m.update(version="v1.0.0")),
    ("kernelApi 非法", lambda m: m.update(kernelApi="~1")),
    ("kind 非法", lambda m: m.update(kind="bogus")),
    ("api.base 非法", lambda m: m.update(api={"base": "api/v1/demo"})),
    ("api.base 不带版本段", lambda m: m.update(api={"base": "/api/demo"})),
    ("api 整体缺", lambda m: m.pop("api")),
    ("provides 项格式错", lambda m: m.update(provides=["bad"])),
    ("provides 项空", lambda m: m.update(provides=[""])),
    ("provides 非常量", lambda m: m.update(provides=[{"a": 1}])),
    ("requires 项格式错", lambda m: m.update(requires=["a"])),
    ("slots 非法", lambda m: m.update(slots=["not-a-slot"])),
    ("slots 非常量", lambda m: m.update(slots=[123])),
    ("emits 格式错", lambda m: m.update(emits=["X"])),
    ("consumes 格式错", lambda m: m.update(consumes=["X"])),
    ("permissions 非对象", lambda m: m.update(permissions="all")),
    ("permissions 值非法", lambda m: m.update(permissions={"network": "yes"})),
    ("permissions 未知键", lambda m: m.update(permissions={"nope": True})),
    ("name 非字符串", lambda m: m.update(name=123)),
    ("name 太长", lambda m: m.update(name="x" * 300)),
    ("version 非字符串", lambda m: m.update(version=1.0)),
    ("顶层数组", lambda m: None),
    ("顶层字符串", lambda m: None),
]


def targeted_cases() -> list[tuple[str, Any]]:
    """定向样本：覆盖随机变异**碰不到**的组合。

    ★ 为什么需要：
      随机变异只会乱改字段，生成不出「kind=container 且缺 api」这类
      需要**多个字段协同**才可观测的场景。而 Schema 里的 const 恰好
      只出现在 `allOf[0].if.properties.kind.not` —— 只有该组合下
      const 的效果才可观测。样本覆盖不到，裁判就永远抓不到那类 bug。
      这是「零差异」可能骗人的一个真实例子。

    做法：手工构造关键字段的组合，逐个断言。
    """
    cases: list[tuple[str, Any]] = []

    # ① kind=container 且缺 api → 应放行（container 可省 api）
    m = base_manifest()
    m["kind"] = "container"
    m.pop("api")
    cases.append(("container 缺 api（应放行）", m))

    # ② kind=container 但 api.base 非法 → 应拦（const 分支虽不触发，base 仍要合法）
    m = base_manifest()
    m["kind"] = "container"
    m["api"] = {"base": "bad-base"}
    cases.append(("container + 非法 api.base（应拦）", m))

    # ③ kind 非 container 且缺 api → 应拦（then.required api）
    m = base_manifest()
    m.pop("api")
    cases.append(("非 container 缺 api（应拦）", m))

    # ④ kind=container + 合法 api → 应放行
    m = base_manifest()
    m["kind"] = "container"
    cases.append(("container + 合法 api（应放行）", m))

    # ⑤ kind 取各种枚举值
    for k in ["builtin", "container", "service", "ui", "tool", "integration"]:
        m = base_manifest()
        m["kind"] = k
        cases.append((f"kind={k}", m))

    # ⑥ 顶层字段全缺
    cases.append(("仅 id", {"id": "demo"}))
    cases.append(("空对象", {}))

    # ⑧ minLength 的目标样本 ★ 这组是「裁判自检」能抓到
    #   「minLength 判断反转」注入的唯一依据。
    #   ★ 为什么必须有：Schema 里 minLength=1 约束着 name / tabs[].key /
    #     tabs[].label / absorbs[] / schedule.title。
    #     若样本里这些字段**全是合法非空串**，把 `<` 反转成 `>`
    #     在样本上**完全不可观测**——裁判不会报警，
    #     而「零差异」会被误读成「裁判在工作」。
    #     ⇒ 只有让样本覆盖「该空却没空」，注入的 bug 才会显形。
    #     这正是本函数存在的意义：覆盖随机变异碰不到的组合。
    for field in ("name", "absorbs", "schedule"):
        m = base_manifest()
        m[field] = "" if field != "absorbs" else [""]
        cases.append((f"{field} 空串（违反 minLength=1）", m))
    m = base_manifest()
    m["tabs"] = [{"key": "", "label": "ok"}]
    cases.append(("tabs[].key 空串（违反 minLength=1）", m))
    m = base_manifest()
    m["tabs"] = [{"key": "k", "label": ""}]
    cases.append(("tabs[].label 空串（违反 minLength=1）", m))

    # ⑦ api.tools 的键（pattern ^/ 是唯一未完全锚定的一处）
    tools_samples: list[dict[str, Any]] = [
        {"/a": {}},
        {"a": {}},
        {"": {}},
        {"/a": {}, "b": {}},
    ]
    for tools_map in tools_samples:
        m = base_manifest()
        m["api"] = {"base": "/api/v1/demo", "tools": tools_map}
        cases.append((f"api.tools={json.dumps(tools_map, ensure_ascii=False)}", m))

    return cases


def ours_says_ok(v, m: dict[str, Any]) -> bool:
    try:
        v.validate_manifest(m)
        return True
    except Exception:
        return False


def ours_layers(v, m: dict[str, Any]) -> tuple[bool, bool]:
    """返回 (结构层是否通过, 整体是否通过)。

    ★ 归因必须分层精确：
      之前把「整体失败」直接记成「②层规则拦的」，是**归因错误**——
      结构层失败也会被误标成②层意图。注入 bug 实测就会看到
      「合法基线」被标成「②层设计意图」，纯属胡说。
      现在分别取两层的结论，逐条归因。
    """
    errors: list[str] = []
    try:
        v._validate_node(m, v._SCHEMA, "manifest", errors)
    except Exception:
        return False, False
    structure_ok = not errors
    overall_ok = structure_ok and not v._check_consistency(m)
    return structure_ok, overall_ok


def standard_says_ok(schema: dict[str, Any], m: Any) -> bool:
    import jsonschema

    try:
        jsonschema.validate(m, schema)
        return True
    except jsonschema.ValidationError:
        return False


def main() -> int:
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        # ★ 刻意**不**返回 0：没装裁判就等于「没验证过」，
        #   报绿是比报错更危险的事——门禁静默失效，外表却是全绿。
        #   依赖已写进 pyproject 的 dev 组（jsonschema），正常环境不会走到这里。
        print("✗ 没装 jsonschema，无法进行交叉验证。")
        print("  装它：cd services/api && .venv/bin/pip install jsonschema")
        print("  （或按 services/api/pyproject.toml 的 dev 依赖组统一安装）")
        return 2

    if "--selfcheck" in sys.argv:
        return selfcheck()

    return run_crosscheck()


# ★ 反向验证用的注入点：(说明, 原代码, 注入后的错误代码)
INJECTIONS: list[tuple[str, str, str]] = [
    (
        "minLength 判断反转",
        'len(value) < schema["minLength"]',
        'len(value) > schema["minLength"]',
    ),
    (
        "const 判断反转",
        'if "const" in schema and value != schema["const"]:',
        'if "const" in schema and value == schema["const"]:',
    ),
    (
        "required 判断反转",
        "if field not in value:",
        "if field in value:",
    ),
    (
        "enum 放行一切",
        'if "enum" in schema and value not in schema["enum"]:',
        'if "enum" in schema and False:',
    ),
    (
        "type 校验失效",
        "if typ is not None and not _match_type(value, typ):",
        "if typ is not None and False:",
    ),
]


def selfcheck() -> int:
    """反向验证：注入已知 bug，确认裁判**抓得到**。

    ★ 为什么必须做：
      「零差异」有两种可能——①代码真的对 ②裁判根本没在工作。
      光看「零差异」无法区分这两者。必须**故意弄坏**一次，
      看裁判是否报警；报警了，才能相信它没报警时的沉默。

    手法：把源码**复制到沙箱文件** → 在副本上改 → 跑裁判 → 断言抓到。
    ★ 绝不改真源码：早先的实现直接改 `validate.py` 再还原，一旦中途异常
      就留下「注入残留」，而且下一轮的 `original` 读到的是污染版
      （连锁污染：曾实测到 `if field in value` 与 `... and False` 两处残留）。
      沙箱副本没有这个面——真源码在自检期间一个字节都不动。
    """
    import shutil
    import tempfile

    print("=" * 68)
    print("裁判自检（反向验证）")
    print("=" * 68)
    print("逻辑：对每种已知 bug 注入（沙箱副本）→ 裁判必须报警")
    print()

    original = VALIDATE_PATH.read_text(encoding="utf-8")
    sandbox_dir = Path(tempfile.mkdtemp())
    # ★ 沙箱必须**复制成真源码的目录深度**：validate.py 用
    #   `Path(__file__).resolve().parents[4] / "contracts"` 定位 schema。
    #   裸放在临时目录里会算到 C:\Users\<user>\contracts ⇒ FileNotFoundError。
    sandbox = sandbox_dir / "services" / "api" / "core" / "plugins" / "validate_sandbox.py"
    sandbox.parent.mkdir(parents=True, exist_ok=True)
    (sandbox_dir / "contracts").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SCHEMA_PATH, sandbox_dir / "contracts" / "plugin.schema.json")

    passed = 0
    failed: list[str] = []
    try:
        for label, old, new in INJECTIONS:
            if old not in original:
                failed.append(f"{label}：未找到注入锚点（源码变了？）")
                print(f"  ✗ {label}：锚点不存在")
                continue
            # ★ newline="\n"：Windows 下默认会把 \n 写成 \r\n，让沙箱副本与真源码不一致
            sandbox.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
            # 直接跑判定逻辑，不看输出；用小样本集 + 定向样本快速判定
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            v = load_our_validator(sandbox)
            caught = 0
            probe = targeted_cases()
            rng = random.Random(20261027)
            fields = list(schema["properties"].keys())
            junk = [None, "", "x", 0, 1, True, [], {}, ["a"], {"a": 1}, "BAD VALUE", 3.14]
            for _ in range(400):
                m = base_manifest()
                for _ in range(rng.randint(1, 3)):
                    m[rng.choice(fields)] = rng.choice(junk)
                probe.append((f"rand{m}", m))
            for _name, m in probe:
                structure_ok, _ = ours_layers(v, m)
                std = standard_says_ok(schema, m)
                if structure_ok != std:
                    caught += 1
            if caught > 0:
                print(f"  ✓ {label}：裁判抓到 {caught} 处差异")
                passed += 1
            else:
                print(f"  ✗ {label}：裁判没抓到 ★ 裁判可能失效")
                failed.append(f"{label}：裁判没抓到")
    finally:
        # ★ 只清沙箱；真源码全程没被写过（写回 original 反而会把污染固化）。
        shutil.rmtree(sandbox_dir, ignore_errors=True)

    print()
    print(f"自检结果：{passed}/{len(INJECTIONS)} 种注入被成功捕获")
    if failed:
        for f in failed:
            print(f"  ★ {f}")
        return 1
    print("✓ 裁判有效：能抓到已知 bug，因此「零差异」是可信的")
    return 0


def run_crosscheck(verbose: bool = True) -> int:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    v = load_our_validator()

    cases: list[tuple[str, Any]] = [("合法基线", base_manifest())]
    for name, fn in MUTATIONS:
        m = base_manifest()
        fn(m)
        cases.append((name, m))

    # 定向样本：多字段协同的场景，随机变异碰不到
    cases.extend(targeted_cases())

    # 随机变异：多轮随机改字段，制造更多样本
    rng = random.Random(20261027)
    fields = list(schema["properties"].keys())
    junk = [None, "", "x", 0, 1, True, [], {}, ["a"], {"a": 1}, "BAD VALUE", 3.14]
    for i in range(1200):
        m = base_manifest()
        for _ in range(rng.randint(1, 3)):
            f = rng.choice(fields)
            m[f] = rng.choice(junk)
        cases.append((f"随机变异 #{i}", m))

    # 嵌套字段变异：深入 api / origin / execution / concurrency 等子对象
    nested_paths = [
        ("api", "base"),
        ("api", "tools"),
        ("origin", "installedBy"),
        ("origin", "signature"),
        ("origin", "signed"),
        ("execution", "mode"),
        ("execution", "parallelSafe"),
        ("execution", "checkpoint"),
        ("concurrency", "exclusive"),
        ("concurrency", "optimisticLock"),
        ("schedule", "cron"),
        ("schedule", "time"),
        ("lifecycle", "onEnable"),
        ("permissions", "network"),
        ("permissions", "filesystem"),
        ("sideEffects", "external"),
        ("sideEffects", "checkpoint"),
        ("track", "level"),
        ("riskLevel", "level"),
    ]
    nested_junk: list[Any] = [
        None, "", "x", 0, 1, True, [], {}, ["a"], "BAD", 3.14,
        "read", "L0", "before-side-effect",
    ]
    for i in range(1200):
        m = base_manifest()
        for _ in range(rng.randint(1, 2)):
            parent, child = rng.choice(nested_paths)
            cur = m
            cur[parent] = {} if not isinstance(cur.get(parent), dict) else cur[parent]
            cur[parent][child] = rng.choice(nested_junk)
        cases.append((f"嵌套变异 #{i}", m))

    # 结构层 vs 标准：真正的 bug 判定
    danger: list[str] = []          # 我们放行、标准拦截 → 危险（必须是 bug）
    suspect: list[str] = []         # 我们拦截、标准放行，且**非**②层所拦 → 可疑
    layer2_only: list[str] = []     # 结构层过、仅②层拦 → 设计意图，正常

    for name, m in cases:
        structure_ok, overall_ok = ours_layers(v, m)
        std = standard_says_ok(schema, m)
        if structure_ok == std:
            # 结构层结论一致。标准放行但我们整体拦 → ②层额外严格（设计意图）
            if structure_ok and not overall_ok:
                layer2_only.append(name)
            continue
        # 结构层结论不一致
        if structure_ok and not std:
            danger.append(name)      # 我们放行、标准拦截 ★ 危险
        else:
            suspect.append(name)     # 我们拦截、标准放行 → 可疑

    if not verbose:
        # 静默模式：只返回结论码，不打印
        return 1 if (danger or suspect) else 0

    print("=" * 68)
    print("校验器交叉验证（自研结构层 vs jsonschema 标准实现）")
    print("=" * 68)
    print(f"样本数：{len(cases)}")
    print(f"★ 危险差异（我们放行 / 标准拦截）：{len(danger)}")
    print(f"△ 可疑差异（我们拦截 / 标准放行）：{len(suspect)}")
    print(f"  其中仅因②层跨字段规则而拦（设计意图）：{len(layer2_only)}")
    print()

    if danger:
        print("★★ 危险：不合规 manifest 会被放行 —— 必须是 bug")
        for name in danger:
            print(f"  {name}")
        print()

    if suspect:
        print("△ 可疑差异（结构层实现可能过头了）：")
        for name in suspect[:20]:
            print(f"  {name}")
        if len(suspect) > 20:
            print(f"  …（其余 {len(suspect) - 20} 条同类）")
        print()

    if danger or suspect:
        print("结论：结构层与标准 JSON Schema 存在偏差，必须修。")
        return 1
    print("✓ 结构层与标准 JSON Schema 完全一致——自研实现无偏差")
    print(f"  （另有 {len(layer2_only)} 个样本结构层合法、被②层跨字段规则额外拦下，属设计意图）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### 5.8 ⑧ 调研对齐核查器：`tools/check_research_alignment.py`（373 行）

```python
# ── 协议与调研产物的对齐判据 ──
#!/usr/bin/env python3
"""协议 ↔ 调研产物 对齐性核查器。

★ 为什么需要这个：
  「协议已经覆盖了调研产物」这句话，人说会漏、会说错。
  本脚本把调研产物里的**机制名词**逐条映射到协议代码
  （Schema 字段 / 校验规则 / 运行时消费点），机器判定覆盖情况。

  覆盖判定分三档：
    ✓ 已体现   —— 协议代码里有明确对应（字段或规则）
    △ 部分     —— 有相关字段，但缺独立表达（弱体现）
    ✗ 缺失     —— 协议代码里完全没有对应 ★ 这是要补的

用法：
    python3 tools/check_research_alignment.py
    python3 tools/check_research_alignment.py --json
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# ★ 路径来自 tools/paths.py（唯一真相源）
_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from paths import REPO  # noqa: E402

SCHEMA_PATH = REPO / "contracts/plugin.schema.json"
VALIDATE_PATH = REPO / "services/api/core/plugins/validate.py"
CROSSCHECK_PATH = REPO / "services/api/core/plugins/crosscheck.py"


# ★ 调研产物 → 协议字段/规则的映射表
#   每条：机制（调研里的说法）→ 协议侧应当落到的字段/规则关键词
#   fields：Schema 里应当存在的字段名（任一命中即可）
#   rules：校验器/跨插件校验里应当出现的关键词（任一命中即可）
ALIGNMENT: list[dict[str, Any]] = [
    {
        "mechanism": "能力缝三角色：Service Definition",
        "source": "内核重构 / 插件协议调研",
        "fields": ["defines"],
        "rules": ["defines"],
        "why": "定义者与提供者分离——defines 声明「我定义这个能力」",
    },
    {
        "mechanism": "能力缝三角色：Service Provider",
        "source": "内核重构 / 插件协议调研",
        "fields": ["provides"],
        "rules": ["provides"],
        "why": "提供者——实现能力；规则 13 保证同一能力不被两个插件提供",
    },
    {
        "mechanism": "能力缝三角色：Consumer",
        "source": "内核重构 / 插件协议调研",
        "fields": ["requires", "optionalDependencies"],
        "rules": ["requires"],
        "why": "消费者——声明依赖；规则 2 保证依赖集不相交",
    },
    {
        "mechanism": "capability（能力）作为一等公民",
        "source": "论文调研（能力请求）",
        "fields": ["defines", "provides", "requires"],
        "rules": ["capability", "能力"],
        "why": "能力寻址而非进程寻址",
    },
    {
        "mechanism": "Phase Machine（插件生命周期状态机）",
        "source": "内核重构/PhaseMachine_插件生命周期状态机.md",
        "fields": ["lifecycle", "deactivation"],
        "rules": ["lifecycle"],
        "why": "生命周期钩子与停用策略是状态机的对外表达",
    },
    {
        "mechanism": "kernel.effect()（注册即带逆）",
        "source": "内核重构/kernel.effect_注册即带逆.md",
        "fields": ["sideEffects", "track", "execution"],
        "rules": ["sideEffects", "checkpoint"],
        "why": "副作用与检查点字段是 effect 原语的声明面",
    },
    {
        "mechanism": "Reactive Co-effect（依赖满足驱动）",
        "source": "内核重构/ReactiveCoeffect_依赖满足驱动.md",
        "fields": ["activates_on", "requires", "optionalDependencies"],
        "rules": ["activates_on"],
        "why": "激活条件与依赖共同构成 co-effect 的触发面",
    },
    {
        "mechanism": "拦截层（逐次拦截）",
        "source": "内核重构/内核边界与拦截层.md",
        "fields": ["permissions", "riskLevel", "execution"],
        "rules": ["permissions", "approval", "riskLevel"],
        "why": "权限/风险/执行模式是拦截层的判定依据",
    },
    {
        "mechanism": "Recover（补偿恢复）",
        "source": "内核重构/kernel.effect_注册即带逆.md",
        "fields": ["migrations", "sideEffects"],
        "rules": ["migrations", "concurrency"],
        "why": "迁移可逆性 + 独占执行是恢复语义的前提（规则 7/8）",
    },
    {
        "mechanism": "effect log（效果日志）",
        "source": "内核重构/kernel.effect_注册即带逆.md",
        "fields": ["track", "sideEffects"],
        "rules": ["track"],
        "why": "track 决定效果是否落日志、可否追溯",
    },
    {
        "mechanism": "身份域 L0~L3",
        "source": "内核重构/多AI并发改造的预设与防护.md",
        "fields": ["origin", "permissions", "concurrency"],
        "rules": ["origin", "installedBy", "signature"],
        "why": "插件来源、签名、权限即身份域的第一层表达",
    },
    {
        "mechanism": "Independence Condition（独立性条件）",
        "source": "论文调研（§4 组合语义）",
        "fields": ["slots", "provides"],
        "rules": ["slots"],
        "why": "扩展点插槽 + 能力独占是独立性约束的协议面",
    },
    {
        "mechanism": "扩展点 / slot（插件挂载位）",
        "source": "内核重构 / 插件协议调研",
        "fields": ["slots"],
        "rules": ["slots"],
        "why": "slot 白名单由 Schema enum 约束",
    },
    {
        "mechanism": "定时调度（schedule 六种选择器）",
        "source": "插件协议调研",
        "fields": ["schedule"],
        "rules": ["schedule"],
        "why": "调度是独立字段组",
    },
    {
        "mechanism": "输出策略（outputPolicy 溢出处理）",
        "source": "插件协议调研",
        "fields": ["outputPolicy"],
        "rules": [],
        "why": "输出裁剪与溢出策略",
    },
    {
        "mechanism": "并发控制（乐观锁 / 独占）",
        "source": "内核重构/多AI并发改造的预设与防护.md",
        "fields": ["concurrency"],
        "rules": ["exclusive", "optimisticLock", "parallelSafe"],
        "why": "规则 10/11 落在此处",
    },
    {
        "mechanism": "迁移（migrations）",
        "source": "插件协议调研",
        "fields": ["migrations"],
        "rules": ["migrations", "exclusive", "persistent"],
        "why": "规则 7/8 落在此处",
    },
    {
        "mechanism": "权限（permissions 声明式）",
        "source": "插件协议调研",
        "fields": ["permissions"],
        "rules": ["permissions", "approval"],
        "why": "声明式权限表，规则 3 关联审批",
    },
    {
        "mechanism": "设置（settingsSchema）",
        "source": "插件协议调研",
        "fields": ["settingsSchema", "settings"],
        "rules": [],
        "why": "插件自带设置的声明面",
    },
    {
        "mechanism": "API 契约（api.base / tools）",
        "source": "插件协议调研",
        "fields": ["api"],
        "rules": ["api"],
        "why": "对外 HTTP 契约面",
    },
    {
        "mechanism": "事件契约（emits / consumes）",
        "source": "插件协议调研",
        "fields": ["emits", "consumes"],
        "rules": [],
        "why": "事件发布与订阅的声明面",
    },
    {
        "mechanism": "版本兼容（minKernel / kernelApi）",
        "source": "插件协议调研",
        "fields": ["minKernel", "kernelApi"],
        "rules": ["kernelApi", "minKernel"],
        "why": "内核接口版本兼容判断",
    },
    {
        "mechanism": "插件来源与签名（origin）",
        "source": "插件协议调研 / 身份域",
        "fields": ["origin"],
        "rules": ["signed", "signature", "installedBy"],
        "why": "规则 5/6 落在此处",
    },
    {
        "mechanism": "风险分级（riskLevel）",
        "source": "插件协议调研",
        "fields": ["riskLevel"],
        "rules": ["riskLevel", "L0"],
        "why": "规则 4：第三方插件不得为 L0",
    },
]


def consumption_map() -> dict[str, list[str]]:
    """字段 → 真实消费点（跑 G13 消费门禁拿机器判定结果）。

    ★ G13 的 --json 输出结构是：
        {"consumers": {"<字段名>": ["<文件路径>", ...]}, ...}
      之前按 {"fields": [{...}]} 读，结果全部字段都判成「无消费方」——
      24/24 全报未消费，明显与 G13 自身的 28/35 矛盾。
      读错结构比没检查更危险：它会给出「全部待补」的错误结论。
    """
    script = REPO / "services/api/scripts/check_protocol_consumption.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--json"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    consumers = data.get("consumers", {})
    if not isinstance(consumers, dict):
        return {}
    return {k: v for k, v in consumers.items() if isinstance(v, list)}


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_fields(schema: dict[str, Any]) -> set[str]:
    return set(schema.get("properties", {}).keys())


def code_corpus() -> str:
    """协议代码语料：校验器 + 跨插件校验 + Schema 文本。"""
    parts = [
        VALIDATE_PATH.read_text(encoding="utf-8"),
        CROSSCHECK_PATH.read_text(encoding="utf-8"),
        SCHEMA_PATH.read_text(encoding="utf-8"),
    ]
    return "\n".join(parts)


def check() -> list[dict[str, Any]]:
    schema = load_schema()
    fields = schema_fields(schema)
    corpus = code_corpus()
    consumers = consumption_map()
    results: list[dict[str, Any]] = []

    for item in ALIGNMENT:
        want_fields = item["fields"]
        want_rules = item["rules"]

        hit_fields = [f for f in want_fields if f in fields]
        hit_rules = [r for r in want_rules if r in corpus]

        # 消费方：这些字段是否被运行时真读
        consumed_fields = [f for f in hit_fields if consumers.get(f)]
        unconsumed_fields = [f for f in hit_fields if not consumers.get(f)]

        # ★ 三档判定（加严版）：
        #   ✓ 协议完备 = 字段在 + 规则/实现在
        #     协议这一侧已经完整对齐调研产物。
        #   △ 协议侧缺一环（字段缺，或规则缺）
        #   ✗ 完全没有对应
        #
        # ★ 注意「有字段但没人消费」**不算**协议侧缺失——
        #   那是内核实现进度问题（协议先立、实现跟上），
        #   两者必须分开报告，不能混为一谈。
        if want_fields and not want_rules:
            status = "✓" if hit_fields else "✗"
        elif want_fields and want_rules:
            if hit_fields and hit_rules:
                status = "✓"
            elif hit_fields or hit_rules:
                status = "△"
            else:
                status = "✗"
        else:
            status = "✓" if hit_rules else "✗"

        results.append(
            {
                "mechanism": item["mechanism"],
                "source": item["source"],
                "status": status,
                "fields_hit": hit_fields,
                "fields_miss": [f for f in want_fields if f not in fields],
                "rules_hit": hit_rules,
                "rules_miss": [r for r in want_rules if r not in corpus],
                "consumed_fields": consumed_fields,
                "unconsumed_fields": unconsumed_fields,
                "why": item["why"],
            }
        )
    return results


def main() -> int:
    if "--json" in sys.argv:
        print(json.dumps(check(), ensure_ascii=False, indent=2))
        return 0

    results = check()
    full = [r for r in results if r["status"] == "✓"]
    part = [r for r in results if r["status"] == "△"]
    miss = [r for r in results if r["status"] == "✗"]

    # 有字段但运行时没人读的机制——这是**内核实现进度**，不是协议缺失
    pending_impl = [r for r in results if r["unconsumed_fields"]]

    print("=" * 72)
    print("协议 ↔ 调研产物 对齐性核查")
    print("=" * 72)
    print(f"调研机制总数：{len(results)}")
    print(f"✓ 协议侧已完备：{len(full)}    △ 协议侧缺一环：{len(part)}    ✗ 完全缺失：{len(miss)}")
    print()

    for r in results:
        mark = r["status"]
        print(f"{mark} {r['mechanism']}")
        if mark != "✓":
            if r["fields_miss"]:
                print(f"      缺字段：{r['fields_miss']}")
            if r["rules_miss"]:
                print(f"      缺规则/代码：{r['rules_miss']}")
    print()

    if part:
        print("△ 协议侧缺一环（要补）：")
        for r in part:
            print(f"   {r['mechanism']}")
        print()
    if miss:
        print("✗ 协议里完全没有对应 ★ 要补：")
        for r in miss:
            print(f"   {r['mechanism']}  ← {r['why']}")
        print()

    if pending_impl:
        print("─" * 72)
        print("内核实现待补（协议已立，运行时还没人读这些字段）：")
        for r in pending_impl:
            print(f"   {r['mechanism']}：{r['unconsumed_fields']}")
        print("   ↑ 这是实现进度，不是协议缺失——协议先立、内核跟上。")
        print()

    if miss or part:
        return 1
    print(f"✓ 全部 {len(results)} 项调研机制在协议侧都有完整对应（字段 + 规则）")
    if pending_impl:
        print(f"  另有 {len(pending_impl)} 项的字段尚无运行时消费方，属内核实现待补")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### 5.9 ⑨ 质量门禁的自检测试：`services/api/tests_protocol/test_protocol_quality_gates.py`（630 行）

```python
# ── 门禁自身的存活证明 ──
"""协议质量门禁的**自检测试**。

★ 为什么要有这个文件：
  这一轮给协议加了两道新门禁——
    ① 校验器交叉验证（自研 JSON Schema 子集 vs 标准 jsonschema 实现）
    ② 协议 ↔ 调研产物 对齐核查
  但门禁本身也可能有 bug。而门禁出 bug 是**最危险**的一种：
  它会一直报「全绿」，而实际早已放行了问题。

  所以门禁必须有反向测试：
    · 交叉验证必须能抓到**故意注入**的 bug
    · 对齐核查必须能抓到**故意删掉**的字段
  这些测试本身是代码，会被 CI 跑。门禁失效 → 测试报红。

  ★ 另一个纪律：不复用门禁脚本来算期望值。
    直接跑门禁 --json 拿结果 = 自证，永远绿。
    这里刻意**独立实现**一份最小判定，只用来做对照。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
CROSSCHECK = REPO / "tools" / "crosscheck_validator.py"
ALIGNMENT = REPO / "tools" / "check_research_alignment.py"
API_DOCS = REPO / "tools" / "check_api_docs.py"
PACK_INVARIANTS = REPO / "tools" / "gates" / "verify_pack_invariants.py"
T18_CRITERION = REPO / "services" / "api" / "scripts" / "verify_t18_criterion.py"
SCHEMA = REPO / "contracts" / "plugin.schema.json"
VALIDATE = REPO / "services/api/core/plugins/validate.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # ★ 必须先注册进 sys.modules 再 exec：模块里有 @dataclass 时，
    #   dataclasses 会通过 sys.modules[cls.__module__] 解析注解；
    #   未注册则 AttributeError（'NoneType' object has no attribute '__dict__'）。
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)  # 加载失败不留半个模块
        raise
    return mod


# ══════════════════════════════════════════════════════════════
# ① 交叉验证：门禁必须能抓到故意注入的 bug
# ══════════════════════════════════════════════════════════════


def test_crosscheck_catches_injected_bug(tmp_path: Path) -> None:
    """注入一个明显 bug，交叉验证必须报出差异。

    ★ 这是「门禁有效」的唯一硬证据。
      如果注入 bug 后门禁仍然报「零差异」，
      那它就抓不到任何 bug——它的「全绿」毫无意义。

    ★ 注入写在**沙箱副本**上：早先直接改写仓库里的 `validate.py`，进程一旦
      被打断（休眠/超时/kill）就留下注入残留——历史上真的留下过
      `if "enum" in schema and False:` 与 `if field in value:` 两处，
      线上校验逻辑被静默反转。与 `crosscheck_validator --selfcheck` 同一条纪律：
      **写操作的测试一律进沙箱，真源码一个字节都不动。**
    """
    cross = _load(CROSSCHECK, "_cc")
    original = VALIDATE.read_text(encoding="utf-8")
    # 注入：把 enum 校验整个废掉
    mutated = original.replace(
        'if "enum" in schema and value not in schema["enum"]:',
        'if "enum" in schema and False:',
        1,
    )
    assert mutated != original, "注入锚点不存在（源码变了？）"

    # 沙箱要镜像真源码的目录深度：validate.py 用 parents[4] 定位 contracts/
    sandbox = tmp_path / "services" / "api" / "core" / "plugins" / "validate_sandbox.py"
    sandbox.parent.mkdir(parents=True)
    (tmp_path / "contracts").mkdir(parents=True)
    shutil.copyfile(SCHEMA, tmp_path / "contracts" / "plugin.schema.json")
    sandbox.write_text(mutated, encoding="utf-8", newline="\n")

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    v = cross.load_our_validator(sandbox)

    # 独立构造一个「enum 违规」的 manifest
    m = cross.base_manifest()
    m["kind"] = "totally-not-a-valid-kind"

    structure_ok, _ = cross.ours_layers(v, m)
    std_ok = cross.standard_says_ok(schema, m)

    assert std_ok is False, "标准校验器应拒绝非法 enum"
    assert structure_ok is True, (
        "注入 bug 后自研校验器应放行——正是要抓的漏网之鱼"
    )
    assert structure_ok != std_ok, "两边结论必须出现差异，交叉验证才有得报"


def test_crosscheck_clean_code_has_no_dangerous_diff() -> None:
    """干净代码下，结构层与标准实现必须零差异。"""
    cross = _load(CROSSCHECK, "_cc2")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    v = cross.load_our_validator()

    # ★ 样本名要带进断言消息——否则失败时只看到一个巨大的 dict，
    #   不知道是哪个样本出的问题，排查成本极高。
    samples: list[tuple[str, Any]] = [("合法基线", cross.base_manifest())]
    for name, fn in cross.MUTATIONS:
        m = cross.base_manifest()
        fn(m)
        samples.append((name, m))
    samples.extend(cross.targeted_cases())

    for name, m in samples:
        structure_ok, _ = cross.ours_layers(v, m)
        std_ok = cross.standard_says_ok(schema, m)
        assert structure_ok == std_ok, (
            f"结构层与标准实现结论不一致：样本「{name}」"
            f"我们={'放行' if structure_ok else '拦截'} "
            f"标准={'放行' if std_ok else '拦截'}"
        )


def test_crosscheck_selfcheck_passes() -> None:
    """门禁自检模式必须全绿（5 种注入全部被抓到）。"""
    proc = subprocess.run(
        [sys.executable, str(CROSSCHECK), "--selfcheck"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        timeout=300,
    )
    assert proc.returncode == 0, f"裁判自检未通过：\n{proc.stdout}\n{proc.stderr}"
    assert "5/5" in proc.stdout, f"自检未覆盖全部注入点：\n{proc.stdout}"


# ══════════════════════════════════════════════════════════════
# ② 对齐核查：删掉字段必须被抓到
# ══════════════════════════════════════════════════════════════


def test_alignment_detects_missing_field(tmp_path: Path, monkeypatch: Any) -> None:
    """把 Schema 里的字段删掉，对齐核查必须报缺失。

    ★ 同样是「门禁有效」的硬证据。
      对齐核查如果对字段缺失无感，那它报「24/24 全覆盖」是假的。

    ★ 同样进沙箱：早先直接删仓库里的 `contracts/plugin.schema.json` 再写回，
      中断即留下「字段被删掉的契约」——那比测试红严重得多。
      这里改成**临时 schema + monkeypatch 模块常量**。
    """
    align = _load(ALIGNMENT, "_al")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    # 删掉一个对齐表里要求存在的字段
    victim = "defines"
    assert victim in schema["properties"], f"字段 {victim} 不存在，换一个"
    del schema["properties"][victim]
    tmp_schema = tmp_path / "plugin.schema.json"
    tmp_schema.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    monkeypatch.setattr(align, "SCHEMA_PATH", tmp_schema)

    results = align.check()
    defines_result = next(
        r for r in results if r["mechanism"].startswith("能力缝三角色：Service Definition")
    )
    assert defines_result["status"] != "✓", (
        f"删掉 {victim} 后仍报「协议侧已完备」★ 对齐核查失效"
    )
    assert victim in defines_result["fields_miss"], (
        f"应把 {victim} 报为缺字段，实际：{defines_result['fields_miss']}"
    )


def test_alignment_all_mechanisms_covered() -> None:
    """当前代码下，调研机制应当全部在协议侧有完整对应。"""
    align = _load(ALIGNMENT, "_al2")
    results = align.check()
    bad = [
        (r["mechanism"], r["fields_miss"], r["rules_miss"])
        for r in results
        if r["status"] != "✓"
    ]
    assert not bad, f"以下调研机制在协议侧没对齐：{bad}"


def test_alignment_mechanism_table_covers_research_keywords() -> None:
    """对齐表本身不能漏——它必须覆盖调研产物里出现的关键机制词。

    ★ 防止「对齐核查只查了自己表里的东西」这种自证循环：
      表里没列的机制，核查就管不到。
      所以这里独立于表，从调研笔记里抽关键词，断言表里都覆盖到。
    """
    align = _load(ALIGNMENT, "_al3")
    table_text = json.dumps(align.ALIGNMENT, ensure_ascii=False)

    # 调研产物里反复出现的机制关键词，必须在映射表里有对应条目
    for keyword in [
        "defines",
        "provides",
        "requires",
        "lifecycle",
        "sideEffects",
        "permissions",
        "riskLevel",
        "migrations",
        "concurrency",
        "schedule",
        "origin",
        "slots",
        "api",
        "emits",
        "consumes",
        "kernelApi",
        "minKernel",
        "outputPolicy",
        "track",
        "execution",
        "deactivation",
        "activates_on",
        "settingsSchema",
    ]:
        assert keyword in table_text, f"对齐表漏了字段 {keyword}"


# ══════════════════════════════════════════════════════════════
# ③ 校验器本身的规范符合性
# ══════════════════════════════════════════════════════════════


def test_validator_uses_search_semantics_for_pattern() -> None:
    """pattern 必须用 search 语义，不能用 match。

    ★ JSON Schema 规范：pattern 不隐式锚定（部分匹配）。
      用 re.match 是「从头匹配」——对未锚定的 pattern 会与标准实现分歧。
      当前 Schema 的 pattern 恰好都带锚点，属于巧合，不是保证。
      这条测试把「巧合」变成「约定」。
    """
    src = VALIDATE.read_text(encoding="utf-8")
    assert 're.search(schema["pattern"], value)' in src, "pattern 校验未用 re.search"
    assert 're.match(schema["pattern"], value)' not in src, (
        "pattern 校验仍在用 re.match——与 JSON Schema 规范不符"
    )


def test_validator_has_no_bare_except_and_no_todo() -> None:
    """校验器里不该有裸 except 和待办占位。"""
    src = VALIDATE.read_text(encoding="utf-8")
    import re

    assert not re.search(r"except\s*:", src), "校验器里有裸 except"
    for marker in ("TODO", "FIXME", "XXX", "NotImplementedError"):
        assert marker not in src, f"校验器里有待办标记 {marker}"


def test_schema_is_valid_json_with_expected_shape() -> None:
    """Schema 本身必须是合法 JSON，且有协议该有的结构。"""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema.get("type") == "object"
    assert "properties" in schema
    assert len(schema["properties"]) >= 30, "字段数明显偏少，Schema 是不是被改坏了"
    for required in schema.get("required", []):
        assert required in schema["properties"], f"required 里的 {required} 不在 properties 中"


def test_all_patterns_are_compilable() -> None:
    """Schema 里所有 pattern 都必须能被编译（写错正则会静默失效）。"""
    import re

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    patterns: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            p = node.get("pattern")
            if isinstance(p, str):
                patterns.append(p)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    assert patterns, "Schema 里一个 pattern 都没有？"
    for p in patterns:
        re.compile(p)  # 编译失败会直接抛异常


# ══════════════════════════════════════════════════════════════
# ④ api-docs 门禁：清单来源与计数逻辑必须能报红
# ══════════════════════════════════════════════════════════════
#
# ★ 2026-11-24（汐瑶）：这道门禁曾因**硬编码 26 个模块**而虚报 100% 两个多月。
#   它漏掉 12 个真实上线模块、冤枉 1 个已转第三方的插件，分母永远 26 ——
#   「覆盖率 100%」看着漂亮但不可信，而没人发现，因为它是绿的。
#
#   所以这里守的不是覆盖率数字，而是**门禁自己会不会坏**：
#   清单来源不可信时必须 fatal，不许静默退回硬编码。


def test_api_docs_selfcheck_passes() -> None:
    """api-docs 门禁自检必须全绿（四条判据各自都能报红）。"""
    proc = subprocess.run(
        [sys.executable, str(API_DOCS), "--selfcheck"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        timeout=120,
    )
    assert proc.returncode == 0, f"api-docs 自检未通过：\n{proc.stdout}\n{proc.stderr}"
    assert "四条判据各自都能报红" in proc.stdout, proc.stdout


def test_api_docs_has_no_hardcoded_module_list() -> None:
    """清单必须来自运行时（/readyz），**不许再出现硬编码的模块名清单**。

    这是本卡的根本判据：只要有人把 `MODULES = ["agents", "ai-chat", ...]`
    写回来，这里就红 —— 那次虚报 100% 的病根正是它。
    """
    src = API_DOCS.read_text(encoding="utf-8")
    # 找形如 MODULES = [...] 的硬编码清单（注释里提到 MODULES 不算）
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not stripped.startswith("MODULES = ["), (
            "出现硬编码模块清单 —— 这正是 2026-11-24 覆盖率虚报 100% 的病根。"
            "清单必须从 /readyz 动态取（见 _discover_modules）。"
        )


# ══════════════════════════════════════════════════════════════
# ④ 包层不变量门禁（pack-invariants）：逐条不变量反向自证
# ══════════════════════════════════════════════════════════════
# ★ 2026-11-24（比丢 · 工单 28）：这道门禁此前只有「写侧自测」——
#   它证明「代码写出的锁契约认」，但**没有任何东西证明门禁还认不认得坏包**。
#   团队纪律：反向自证 = 喂一次失效必须报红；断言到具体文案，不只看退出码。


def test_pack_invariants_selfcheck_passes() -> None:
    """pack-invariants 必须能逐条抓住注入的坏包（I1/I2/I3/I4/I5/I9/I11）。"""
    proc = subprocess.run(
        [sys.executable, str(PACK_INVARIANTS), "--selfcheck"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        timeout=180,
    )
    assert proc.returncode == 0, f"pack-invariants 自检未通过：\n{proc.stdout}\n{proc.stderr}"
    # 判据 4：断言到具体用例文案，不是只断言 returncode。
    for label in (
        "基线包必须全绿",
        "I1 拼错顶层字段",
        "I2 id 与目录名不符",
        "I3 official.* 被 community 占用",
        "I5 预算声明 L3（内核级）",
        "I9 锁比清单旧",
        "I11 包清单里写 provides",
        "I12 校验器自证",
    ):
        assert label in proc.stdout, f"自检少验了「{label}」：\n{proc.stdout}"
    assert "✓ 自检通过" in proc.stdout, proc.stdout


def test_pack_invariants_selfcheck_notices_a_blinded_gate(monkeypatch: Any) -> None:
    """自检必须对「门禁不再报红」敏感 —— 否则它自己也是「绿着但什么都没验」。

    ★ 这是「自检的自检」：把 `run()` 换成看不见 I11 的版本，
      `selfcheck()` 必须返回 1。它证明自检的绿 ≠ 自检被架空。
    """
    mod = _load(PACK_INVARIANTS, "_packinv")
    real_run = mod.run

    def blinded_run(extra: Any = None) -> Any:
        findings, meta = real_run(extra)
        # 模拟「I11 判据被改坏」：坏包不再被这条不变量抓住。
        return [f for f in findings if not (f.invariant == "I11" and f.level == "fail")], meta

    monkeypatch.setattr(mod, "run", blinded_run)
    assert mod.selfcheck() == 1, "门禁漏报 I11 时，自检仍然通过 —— 自检无效"


# ══════════════════════════════════════════════════════════════
# ⑤ T18 判据的静态面：provides 声明了，工具面上必须有去处
# ══════════════════════════════════════════════════════════════
# ★ 2026-11-24（比丢 · 工单 28）：T18 原来只有「起真服务 → 断言工具出现」那一段。
#   它的反面（provides 声明了但工具面上没有）从来没有判据 ——
#   `derive_tool` 遇到未知动词 / 缺 api.base / 两段形态会**静默丢弃**，
#   于是一条 provides 到底给不给 AI，取决于映射表恰好有没有。
#   这是 ISSUE-016（关系隐式化）+ 判据 41（安全不能靠巧合）。
#   静态面用 `--structural-only`，不需要端口与临时库。


def test_t18_structural_selfcheck_passes() -> None:
    """T18 静态判据必须能逐类抓住静默丢失，且干净样本不被误红。"""
    proc = subprocess.run(
        [sys.executable, str(T18_CRITERION), "--selfcheck"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(T18_CRITERION.parent.parent),
        timeout=180,
    )
    assert proc.returncode == 0, f"T18 静态自检未通过：\n{proc.stdout}\n{proc.stderr}"
    for label in (
        "扫到 7 条 provides（反空转）",
        "干净样本摊成工具",
        "三类静默丢失各报红且文案含具体原因",
        "陈旧登记报红",
        "空扫描根判红",
    ):
        assert label in proc.stdout, f"自检少验了「{label}」：\n{proc.stdout}"
    assert "✓ 自检通过" in proc.stdout, proc.stdout


def test_t18_real_repo_every_provides_has_a_home() -> None:
    """真仓库里每条 provides 都必须有去处（工具 / 人专属 / 能力声明 / 登记在案）。

    ★ 这条会随仓库演化而红：新加一条摊不出工具的 provides 且没登记 → 红。
      这正是要的 —— 「它去哪了」必须有人决定。
    """
    proc = subprocess.run(
        [sys.executable, str(T18_CRITERION), "--structural-only"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(T18_CRITERION.parent.parent),
        timeout=180,
    )
    assert proc.returncode == 0, f"T18 静态判据未通过：\n{proc.stdout}\n{proc.stderr}"
    assert "条 provides" in proc.stdout, proc.stdout
    assert "✓ 每条 provides 都有去处" in proc.stdout, proc.stdout
    # 待补缺口必须保持可见（不许因为「归档了」就当已解决）
    assert "待补缺口" in proc.stdout, proc.stdout


def test_t18_structural_flags_an_unregistered_capability(tmp_path: Path) -> None:
    """自检之外再验一次真路径：临时 manifest 里一条未知动词必须被判红。

    ★ 用脚本自己的 `scan_provides_tool_surface` 而不是复刻判定 ——
      复刻就是第二个真相源；这里验的是「接线对、函数能报」。
    """
    mod = _load(T18_CRITERION, "_t18c")
    pack = tmp_path / "demo-unregistered"
    pack.mkdir()
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "id": "demo-unregistered",
                "provides": ["demo.item.frobnicate"],
                "api": {"base": "/api/v1/demo"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = mod.scan_provides_tool_surface([("tmp", tmp_path)], {})
    assert report.manifests == 1, report
    assert report.provides == 1, report
    assert not report.ok, "未登记的静默 provides 没有被判红"
    assert [g.capability for g in report.gaps] == ["demo.item.frobnicate"], report.gaps
    assert "frobnicate" in report.gaps[0].problem, report.gaps[0].problem


# ══════════════════════════════════════════════════════════════
# ⑤ protocol-doc 门禁：契约文档过期必须被抓到
# ══════════════════════════════════════════════════════════════
#
# ★ 2026-11-24（汐瑶）：这道门禁在今天的体检里真红过 ——
#   契约文档写死「288 行」，实际文件 288 行时是对的，但任何人给测试文件加了内容
#   它就不再一致。它自己的 --selfcheck 已存在（篡改临时副本必须报红），
#   这里补的是「它真的能跑」+「文档过期时它真会红」。

PROTOCOL_DOC = REPO / "tools" / "check_protocol_doc.py"


def test_protocol_doc_selfcheck_passes() -> None:
    """protocol-doc 自检必须全绿（篡改临时副本必须报红，真文档零写入）。"""
    proc = subprocess.run(
        [sys.executable, str(PROTOCOL_DOC), "--selfcheck"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        timeout=300,
    )
    assert proc.returncode == 0, f"自检未通过：\n{proc.stdout}\n{proc.stderr}"
    assert "自证通过" in proc.stdout, proc.stdout


def test_protocol_doc_is_current() -> None:
    """真文档必须与 schema 当前生成结果逐字一致。

    ★ 这条红不代表代码坏了，只代表**文档过期** —— 修复只需
      `python tools/gen_protocol_doc.py --out docs/specs/plugin-protocol-contract.md`。
      之所以落成测试：文档过期是静默的，没人跑门禁就永远不知道。
    """
    proc = subprocess.run(
        [sys.executable, str(PROTOCOL_DOC)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"契约文档与 schema 不一致（重新生成即可）：\n{proc.stdout}\n{proc.stderr}"
    )


# ══════════════════════════════════════════════════════════════
# ⑥ kernel-purity 门禁：脏的会红、干净的不会误红
# ══════════════════════════════════════════════════════════════
#
# ★ 2026-11-24（汐瑶）：门禁失效有两种，第二种更坏 ——
#   ① 瞎了：脏的也不报；② 误红：干净的也报 ⇒ 人开始绕过它。
#   自检两条都钉。

KERNEL_PURITY = REPO / "services" / "api" / "scripts" / "check_kernel_purity.py"


def test_kernel_purity_selfcheck_passes() -> None:
    """kernel-purity 自检必须全绿（脏的红、干净的不误红、豁免生效、测试文件跳过）。"""
    proc = subprocess.run(
        [sys.executable, str(KERNEL_PURITY), "--selfcheck"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        timeout=300,
    )
    assert proc.returncode == 0, f"自检未通过：\n{proc.stdout}\n{proc.stderr}"
    assert "脏的会红、干净的不会误红" in proc.stdout, proc.stdout


def test_kernel_purity_backend_scope_is_clean() -> None:
    """后端内核 scope 必须干净（这是阻塞项：地基不许认识业务）。"""
    proc = subprocess.run(
        [sys.executable, str(KERNEL_PURITY), "--scope", "backend"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"后端内核出现业务词汇 —— 按总纲，为一件具体的事去动 framework 是框架的 bug：\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


# ══════════════════════════════════════════════════════════════
# ⑦ 部署与生产三件：契约变更 / 部署前守门 / 包预检 / 生产健康
# ══════════════════════════════════════════════════════════════
#
# ★ 2026-11-24（汐瑶）：这四道门禁原来都「没有测试守着」。
#   它们守着的是**上线那一步**——坏了最贵的不是漏一个 bug，是把旧包/半成品发上生产。
#   而它们自己坏了是静默的（绿着，但可能什么都没查）。

CONTRACT_CHANGELOG = REPO / "tools" / "check_contract_changelog.py"
PREDEPLOY = REPO / "tools" / "predeploy_guard.py"
PREFLIGHT = REPO / "tools" / "deploy_package_preflight.py"
PROD_HEALTH = REPO / "tools" / "prod_health_check.py"


def _run(script: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        timeout=timeout,
    )


def test_contract_changelog_selfcheck_passes() -> None:
    """契约变更门禁自检必须全绿（改了契约不写 changelog 必红、没改不许误红）。"""
    proc = _run(CONTRACT_CHANGELOG, "--selfcheck")
    assert proc.returncode == 0, f"自检未通过：\n{proc.stdout}\n{proc.stderr}"
    assert "该红的红、不该红的不误红" in proc.stdout, proc.stdout


def test_predeploy_guard_selfcheck_passes() -> None:
    """部署前守门自检必须全绿（dist 旧必 BLOCK、scope 外改动必 BLOCK、干净不许误红）。"""
    proc = _run(PREDEPLOY, "--selfcheck")
    assert proc.returncode == 0, f"自检未通过：\n{proc.stdout}\n{proc.stderr}"
    assert "该红的红、不该红的不误红" in proc.stdout, proc.stdout


def test_deploy_preflight_self_test_passes() -> None:
    """部署包预检自测必须全绿（全绿 / 缺失 / MD5 不符 三种 case）。"""
    proc = _run(PREFLIGHT, "--self-test")
    assert proc.returncode == 0, f"自测未通过：\n{proc.stdout}\n{proc.stderr}"
    assert "SELF_TEST PASS" in proc.stdout, proc.stdout


def test_prod_health_selfcheck_passes() -> None:
    """生产健康自检必须全绿（全 500 必红、**200 但内容是垃圾也必须红**）。

    ★ 第二条是重点：只看状态码的门禁会在生产已经坏掉时报绿 ——
      今天已经吃过一次这个亏（`/sight/now` 数据源失败也回 200）。
    """
    proc = _run(PROD_HEALTH, "--selfcheck", timeout=300)
    assert proc.returncode == 0, f"自检未通过：\n{proc.stdout}\n{proc.stderr}"
    assert "坏的一定红" in proc.stdout, proc.stdout
```

### 5.10 ⑩ 事件契约执行点（声明即订阅 / 声明即许可）：`services/api/core/events.py`（568 行）

```python
"""进程内事件总线 + SSE 订阅。

## 事件信封（Event Envelope）

★ 事件格式固定：
  `{ id, eid, topic, version, at, source, trace_id, correlation_id, payload }`

| 字段 | 含义 | 为什么有它 |
|---|---|---|
| `id` | **进程内递增序号**（int） | SSE 断线续传（`Last-Event-ID`）用它——续传语义要求单调可比较 |
| `eid` | 事件 UUID（str） | **跨进程唯一**的标识。`id` 只在本进程内有效，重启归零 |
| `topic` | 主题，`<域>.<实体>.<过去式>` | 路由键 |
| `version` | 主题版本（str） | 破坏性变更时 bump；minor 只加字段 |
| `at` | 发生时间（ISO8601 UTC） | |
| `source` | 发布者插件 id | 「谁发的」 |
| `trace_id` | 链路 id | 排障：一次请求引发的多个事件串成一条链 |
| `correlation_id` | 关联 id | 把「一件事的多个产物」串起来（如「装了包 → 成员 A 激活 → 成员 B 激活」） |
| `payload` | 业务数据 | |

★ **与 CloudEvents 1.0 的关系**（不是照抄，是**对齐**）：
  CloudEvents 的 REQUIRED 属性是 `id` / `source` / `specversion` / `type`。
  我们的映射：`topic`↔`type`、`source`↔`source`、`payload`↔`data`、
  `id`↔`id`，另加两个扩展（`eid` / `correlation_id`）。
  之所以不把 `id` 直接换成 UUID：SSE 的 `Last-Event-ID` 走的是**整数比较**，
  换成字符串会破坏断线续传。⇒ 两个 id 各司其职，不合并。

★ 为什么信封要**在总线这一层**统一，而不是让每个发布者自己拼：
  统一后，订阅方拿到的每条事件形状都相同；
  不统一的话，「字段增删不会在启动期暴露」这个问题会一直存在。

## 声明是订阅的唯一入口

★ `manifest.consumes` **必须**是订阅的唯一入口。
  早先订阅是插件直接往总线上挂回调（旧的 `add_listener`），
  声明完全不参与——于是出现过「插件实际监听了 3 个主题、manifest 只声明 1 个」。
  ⇒ **声明 ↔ 实际**可能不一致，而不一致**没有任何报错**。

⇒ 现在的规矩：
  ① 插件**不许**直接挂总线回调；那个挂点已改名为 `add_kernel_hook`
     （名字就是纪律：只给内核自己用，如激活器）；
  ② 插件必须走 `event_bus.subscribe_declared(info, handler, topics=...)`：
     内核从 `info`（PluginInfo / Manifest / 原始 manifest）读 `consumes`，
     处理器要的每个 topic 都必须落在声明内，**否则当场拒绝注册**；
     投递时再按声明过滤，**未声明的 topic 一律不投递**。
  ③ 于是「声明 ↔ 实际」的不一致在**结构上不可能发生**——
     声明少了会在启动时炸（fail-closed），不会拖到线上才表现为「没收到事件」。

★ 为什么 info 由调用方传入而不由内核去读 manifest：
  内核不该为了「读一份插件的声明」而认识插件的目录结构；
  而插件自己知道自己的 manifest。
  ⇒ 传进来是**单向依赖**（插件 → 内核），内核不反向依赖插件。

## 声明也是发布的唯一入口（2026-11-10）

★ `manifest.emits` 与 `manifest.consumes` 是**同一套契约的两端**：
  订阅端「未声明不许订阅」，发布端「未声明不许发布」。
  早先只有订阅端有约束，而 emits 完全没人读 —— 于是出现过
  「9 个模块声明了却从不发布、6 个发布了却没声明」，
  事件契约只成立一半（写着的没人执行，执行着的没人写）。

⇒ 现在的规矩：
  ① 模块**只能**用 `publish(topic, payload, source=<模块 id>)` 发布；
  ② 发布前内核按 `source` 查该模块登记过的 `emits` 声明，
     未登记 / 未覆盖该 topic ⇒ 抛 `UndeclaredPublication`（fail-closed，**不发出去**）；
  ③ 声明由发现层调用 `declare_publisher(info)` 登记（见 `create_app` 与 `mount_plugin`）；
  ④ 内核自己的系统事件（SSE 补发、激活器观测等）走 `publish_kernel_event()`：
     它固定 `source="kernel"`，**不**受模块声明体系约束 ——
     内核不是插件，没有 manifest，也不该为了发一条事件去编一份声明。

★ 为什么发布端要 fail-closed 而不是「记个警告继续发」：
  发布端是**契约的供给方**。没人声明的话题被发出去，订阅端要么收不到（契约漏了），
  要么靠通配符偷听（契约被绕过）——两种都让「事件契约」退化成注释。
  在发布点炸，问题会出现在**写错的那一行**；只警告，问题会出现在三个月后的排障现场。

★ 为什么要静态门禁再扫一遍：运行时只拦得住「跑到的代码」。
  `scripts/check_protocol_consumption.py` 会把「声明了不发布 / 发布了没声明」
  在**不开服务**的情况下判出来（双向），并强制模块发布时必须显式写 `source=<自身 id>`。

## 投递保证

★ 进程内总线是 **at-most-once**：发布即投，不重试、不持久化。
  订阅者崩了，事件就没了。
  ⇒ 这是**已知边界**，写在这里而不是假装它可靠。
  需要可靠投递的场景（跨进程、崩溃恢复）留给「outbox + dispatcher」，
  本期不实现——但边界必须先写清，免得有人以为它可靠。

## 其它

- publish：同步、线程安全（跨线程用 call_soon_threadsafe 通知订阅者）。
- subscribe：返回 (queue, 历史快照)，供 SSE 端点做断线重连（Last-Event-ID）。
- 历史保留最近 100 条（断线续传上限）。
- ★ 单进程内总线，不引入 Redis/Celery（总纲 §1.1 单用户场景足够）。
"""
from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import inspect
import json
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from core.logging import get_logger, trace_id_var

log = get_logger("kernel.events")

_BUS_MAXLEN = 100

#: 内核自身的**事件源**标识。
#:
#: ★ 这是一个受信值：`publish(source="kernel")` 跳过模块 emits 校验。
#:   所以**模块代码不许用它**（静态门禁会拦：模块里的发布必须显式写 source=<自身 id>）。
#:   内核要发系统事件，请走 `publish_kernel_event()` —— 名字即纪律，
#:   与 `add_kernel_hook` 同款思路：让越界写在 review 里一眼可见。
KERNEL_SOURCE = "kernel"

#: 主题的当前版本。破坏性变更时 bump。
DEFAULT_TOPIC_VERSION = "1"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class EventBus:
    def __init__(self, maxlen: int = _BUS_MAXLEN) -> None:
        self._history: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        # ★ 声明式订阅：插件 id → [(该处理器被允许的 topic 集合, 处理器), ...]
        #   一个插件可以有多个处理器（push 就同时挂提醒/待办/上课三路），
        #   但每一路都必须落在 manifest.consumes 声明之内。
        self._declared: dict[str, list[tuple[frozenset[str], Callable[[dict[str, Any]], None]]]] = {}
        # ★ 发布端声明：插件 id → manifest.emits 声明的 topic 集合。
        #   与 `_declared`（订阅端）对称：登记即声明，publish 时按它 fail-closed。
        #   由发现层在**发现时**登记（不是激活时）：注册表回答的是
        #   「这个模块声明了什么」，而「代码在不在跑」由激活表回答，两者不该混。
        self._declared_emits: dict[str, frozenset[str]] = {}
        self._seq = 0
        self._seq_lock = __import__("threading").Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def _next_id(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def publish(
        self,
        topic: str,
        payload: Any,
        source: str = KERNEL_SOURCE,
        *,
        version: str = DEFAULT_TOPIC_VERSION,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """发布事件（同步、线程安全）。返回事件对象。

        ★ `source` 必须写**发布者自己的模块 id**（模块代码里不许省略）。
          省略 = 走内核源，静态门禁会把它当越界。内核自己请用 `publish_kernel_event()`。

        ★ 模块发布者 fail-closed（2026-11-10）：`source` 不是内核时，
          先按该模块登记过的 `manifest.emits` 校验 topic；
          没登记 / 没覆盖 ⇒ 抛 `UndeclaredPublication`，**事件不会发出去**。
          与 `subscribe_declared` 是同一套语义的两端：
          订阅端「未声明不许订阅」，发布端「未声明不许发布」。

        ★ `trace_id` 从 `trace_id_var` 取（中间件已设好），
          没有请求上下文时为空串——**不编造**。
          编一个假 trace_id 会让排障时去查一条不存在的链路。
        """
        if source != KERNEL_SOURCE:
            self._assert_publish_declared(source, topic)
        event = {
            "id": self._next_id(),
            "eid": uuid.uuid4().hex,
            "topic": topic,
            "version": version,
            "at": _now_iso(),
            "source": source,
            "trace_id": trace_id_var.get() or "",
            "correlation_id": correlation_id or "",
            "payload": payload,
        }
        self._history.append(event)
        # 进程内回调监听（激活器挂点）：同步调用，单回调异常不影响广播。
        # publish 可能来自任意线程，回调必须自行保证线程安全。
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "事件回调监听器异常（已忽略，不影响广播）",
                    extra={"topic": topic, "error": str(exc)},
                )
        # ★ 声明式订阅：只投给**声明过**该 topic 的处理器。
        #   未声明的 topic 一律不投——这是「声明是唯一入口」的执行点。
        for plugin_id, entries in list(self._declared.items()):
            for topics, handler in list(entries):
                if not _topic_matches(topics, topic):
                    continue
                try:
                    handler(event)
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "声明式订阅者异常（已忽略，不影响广播）",
                        extra={"topic": topic, "plugin": plugin_id, "error": str(exc)},
                    )
                except BaseException as exc:  # noqa: BLE001
                    # ★ `except Exception` 兜不住 SystemExit / KeyboardInterrupt /
                    #   asyncio.CancelledError（3.8+ 属 BaseException）。它们会**打断
                    #   整条广播**，而原来的写法连一行日志都不留 —— 后面几个插件
                    #   静默漏事件，排障时什么都看不到。
                    #   这里不上抛也不吞：先留痕，再按原语义继续上抛（取消信号必须
                    #   抵达调用方，这是控制流，不是错误）。
                    log.warning(
                        "声明式订阅者抛 BaseException，广播中止",
                        extra={
                            "topic": topic,
                            "plugin": plugin_id,
                            "type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                    raise
        if self._subscribers and self._loop is not None:
            for q in list(self._subscribers):
                try:
                    self._loop.call_soon_threadsafe(q.put_nowait, event)
                except RuntimeError as exc:
                    # ★ loop 已关闭（`asyncio.run` 结束、uvicorn worker 重载、
                    #   优雅退出窗口）。此时**业务副作用已经发生**（上面各 handler
                    #   都跑完了），如果让 RuntimeError 冒给 publish 的调用方，
                    #   调用方会当成失败重试 ⇒ 同一条业务写两遍。
                    #   ⇒ 订阅者已死，摘掉它，降级为 warn。
                    log.warning(
                        "SSE 订阅者的 loop 已关闭，已摘除该订阅者",
                        extra={"topic": topic, "error": str(exc)},
                    )
                    with contextlib.suppress(ValueError):
                        self._subscribers.remove(q)
        return event

    def publish_kernel_event(
        self,
        topic: str,
        payload: Any,
        *,
        version: str = DEFAULT_TOPIC_VERSION,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """发布**内核自身**的系统事件（不受任何模块的 emits 声明约束）。

        ★ 为什么要有独立入口，而不是让内核也写 `publish(source="kernel")`：
          两者行为一样，但名字回答了一个问题——「这条事件是谁发的」。
          内核发的事件不该看起来像某个模块发的（`source` 必须是 `kernel`），
          也不该为了合规去编一份 manifest。名字即纪律（同 `add_kernel_hook`）。
        """
        return self.publish(
            topic,
            payload,
            source=KERNEL_SOURCE,
            version=version,
            correlation_id=correlation_id,
        )

    # ── 发布端声明（emits 契约的执行点）────────────────────────────

    def declare_publisher(self, info: Any) -> frozenset[str]:
        """登记某插件的 `manifest.emits` 声明（发布端的唯一登记入口，幂等）。

        `info` 与 `subscribe_declared` 同形：`PluginInfo` / `Manifest` 模型 / 原始 manifest dict。

        ★ 重复登记同一份声明是幂等的（发现层与挂载入口都会调一次）。
          两次声明**不一致**时不抛错、只告警：那说明同一个 id 有两份 manifest，
          根因在发现层（它已经保证 id == 目录名），不该由总线替它裁决；
          但必须留下痕迹，否则「两份真相」会静默并存。
        """
        plugin_id, declared = _declaration_of(info, field="emits")
        existing = self._declared_emits.get(plugin_id)
        if existing is not None and existing != declared:
            log.warning(
                "插件 %s 的 emits 声明被重复登记且内容不同（后一份生效）",
                plugin_id,
                extra={"before": sorted(existing), "after": sorted(declared)},
            )
        self._declared_emits[plugin_id] = declared
        return declared

    def withdraw_publisher(self, plugin_id: str) -> None:
        """撤销某插件的 emits 声明（幂等）。仅测试与诊断用。"""
        self._declared_emits.pop(plugin_id, None)

    def declared_emits(self, plugin_id: str) -> frozenset[str] | None:
        """某插件登记的 emits 声明；`None` = 从未登记（≠ 空声明）。"""
        return self._declared_emits.get(plugin_id)

    def publishers(self) -> dict[str, frozenset[str]]:
        """当前全部发布端声明快照（诊断用）。"""
        return dict(self._declared_emits)

    def _assert_publish_declared(self, source: str, topic: str) -> None:
        """`source` 是否有权发布 `topic`（fail-closed）。"""
        declared = self._declared_emits.get(source)
        if declared is None:
            raise UndeclaredPublication(
                f"发布者 {source!r} 没有登记 emits 声明：内核不知道它会发什么事件。"
                "模块必须在 manifest.emits 里声明，并由发现层调用 declare_publisher 登记；"
                "内核自身的事件请用 publish_kernel_event()。"
            )
        if not _declared_covers(declared, topic):
            raise UndeclaredPublication(
                f"模块 {source!r} 试图发布未声明的 topic {topic!r}；"
                f"manifest.emits = {sorted(declared)}。"
                "声明是发布的唯一入口——要发布，先把 topic 写进 manifest.emits。"
            )

    def add_kernel_hook(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """注册**内核自身**的回调挂点（激活器等；重复注册同一回调会被去重）。

        ★ 名字里的 `kernel` 是纪律，不是装饰：插件消费事件**只能**走
          `subscribe_declared`——插件的订阅面必须等于 manifest.consumes 声明面。
          这里挂的回调**不受 consumes 过滤**（内核不是插件，没有 manifest）。
          早先它叫 `add_listener`，名字看不出来「谁可以调」，于是 push 模块
          直接用它绕过了声明。改名的目的就是让越界写在 code review 里一眼可见。
        """
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_kernel_hook(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(callback)

    def subscribe_declared(
        self,
        info: Any,
        handler: Callable[[dict[str, Any]], None],
        topics: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> frozenset[str]:
        """★ 插件订阅事件的**唯一入口**：声明即订阅，未声明即拒绝。

        `info`：插件的身份 + 声明。接受三种形态（都只读 `id` 与 `consumes`）：
          · `PluginInfo`（发现层产出，`info.manifest` 是原始 manifest dict）
          · `Manifest` 模型（`core/manifest.py`，`consumes` 是属性）
          · 原始 manifest dict（插件自己从目录里读出来的那份）

        `topics`：这个**处理器**要订阅的 topic。缺省 = 订阅声明里的全部 topic。
        每一路都必须落在 `manifest.consumes` 之内：声明写 `todo.*` 可以覆盖
        `todo.item.due`；但处理器**不能发明**声明里没有的 topic，也不能拿通配
        去套一份只写了精确 topic 的声明。

        ★ 不满足就**拒绝注册并抛错**（fail-closed）——与「未声明 topic 不投递」
          是同一套语义的两端：投递端静默丢弃，注册端当场炸。区别在于后者能让人
          在启动时就发现自己漏写了声明，而不是上线后「怎么没收到事件」。

        ★ 为什么 `info` 由调用方传入而不由内核去读 manifest：
          内核不该为了「读一份插件的声明」而认识插件的目录结构；
          而插件自己知道自己的 manifest。
          ⇒ 传进来是**单向依赖**（插件 → 内核），内核不反向依赖插件。
        """
        plugin_id, declared = _declaration_of(info)
        if not callable(handler):
            raise TypeError(f"插件 {plugin_id!r} 的事件处理器必须可调用，收到 {type(handler)!r}")
        if inspect.iscoroutinefunction(handler):
            # ★ 注册期就拒，而不是运行期静默丢弃：总线是**同步投递**，
            #   `async def` 处理器只会产出一个没人 await 的 coroutine
            #   （实测：注册成功、publish 不报错、handler 一次没跑，
            #     只在日志里留一句 "coroutine ... was never awaited"）。
            #   这正是 fail-closed 要消灭的失败模式 —— 启动时炸，别上线后静默。
            raise TypeError(
                f"插件 {plugin_id!r} 的事件处理器不能是 async def："
                "事件总线是同步投递，async 处理器会被静默丢弃（永不执行）。"
                "请在 handler 内部自行调度到事件循环（如 asyncio.run_coroutine_threadsafe）。"
            )
        wanted = frozenset(declared if topics is None else _as_topics(topics))

        undeclared = sorted(t for t in wanted if not _declared_covers(declared, t))
        if undeclared:
            raise UndeclaredSubscription(
                f"插件 {plugin_id!r} 试图订阅未声明的 topic：{undeclared}；"
                f"manifest.consumes = {sorted(declared)}。"
                "声明是订阅的唯一入口——要订阅，先把 topic 写进 manifest.consumes。"
            )
        if not wanted:
            # 声明为空 = 不订阅任何事件。注册一个空处理器没有意义，
            # 但也不该报错（插件可能还没声明要消费什么）。
            log.debug("插件 %s 的 consumes 为空，不注册任何事件订阅。", plugin_id)
            return wanted

        entries = self._declared.setdefault(plugin_id, [])
        # ★ 同一个 handler 对象重复注册（比如先声明 `todo.*` 后来又补一条
        #   `todo.item.due`，或模块重载前的那份引用）**合并 topic**，
        #   而不是追加第二条 —— 追加会让同一条事件投递两次（实测 S2b）。
        #   插件只表达了一次「我要处理这个函数」，就不该被调用两次。
        for idx, (topics_i, handler_i) in enumerate(entries):
            if handler_i == handler:
                entries[idx] = (topics_i | wanted, handler_i)
                return wanted
        entries.append((wanted, handler))
        return wanted

    def unsubscribe_declared(self, plugin_id: str) -> None:
        """撤销某插件的**全部**声明式订阅（插件停用时用，幂等）。"""
        self._declared.pop(plugin_id, None)

    def declared_topics(self, plugin_id: str) -> frozenset[str]:
        """某插件当前所有处理器声明订阅的 topic 并集（供诊断与测试）。"""
        entries = self._declared.get(plugin_id, [])
        return frozenset().union(*(topics for topics, _h in entries)) if entries else frozenset()

    def subscribe(self) -> tuple[asyncio.Queue[dict[str, Any]], list[dict[str, Any]]]:
        """注册订阅者（必须在事件循环内调用）。"""
        self._loop = asyncio.get_running_loop()
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(q)
        return q, list(self._history)

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        with contextlib.suppress(ValueError):
            self._subscribers.remove(q)

    def sse_response(
        self, request: Request, patterns: list[str] | None = None
    ) -> StreamingResponse:
        """构造 SSE 响应：先看历史补发（Last-Event-ID 续传），再实时推送。"""
        q, history = self.subscribe()
        last = int(request.headers.get("Last-Event-ID") or 0)

        async def generator() -> AsyncIterator[str]:
            try:
                for ev in history:
                    if ev["id"] > last and _match(ev["topic"], patterns or []):
                        yield _format_sse(ev)
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=30)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if _match(ev["topic"], patterns or []):
                        yield _format_sse(ev)
            finally:
                self.unsubscribe(q)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )


def _topic_matches(topics: frozenset[str], topic: str) -> bool:
    """topic 是否落在声明集合内。

    ★ 支持 `fnmatch` 通配（`todo.*`）——声明可以写「我要所有 todo 事件」。
      精确匹配优先，但通配让「一个插件关心一个域的所有事件」不必逐条列。

    ★ 必须用 `fnmatchcase`（大小写敏感）而不是 `fnmatch`：
      `fnmatch` 在 Windows 上会先 `os.path.normcase`（两边转小写），
      与注册期 `_declared_covers`（用 `fnmatchcase`）语义不一致 ——
      实测声明写 `TODO.*`、topic 是 `todo.item.due` 时，
      Windows 投递、Linux 不投递。topic 是标识符，本就该大小写敏感。
    """
    if topic in topics:
        return True
    return any(fnmatch.fnmatchcase(topic, p) for p in topics)


class UndeclaredSubscription(ValueError):
    """插件试图订阅 `manifest.consumes` 之外的 topic（fail-closed）。"""


class UndeclaredPublication(ValueError):
    """插件试图发布 `manifest.emits` 之外的 topic（fail-closed，事件不发出去）。

    ★ 与 `UndeclaredSubscription` 是同一套语义的两端：
      订阅端在**注册时**炸，发布端在**发布时**炸；
      两边都是「声明是唯一入口」这句话的执行点，不是可选的 lint。
    """


def _as_topics(topics: Any) -> frozenset[str]:
    """把 set/list/tuple/单个字符串归一成 topic 集合。

    ★ 单个字符串要当成**一个** topic——`for t in "todo.item.due"` 会按字符拆。
    """
    if isinstance(topics, str):
        return frozenset({topics})
    return frozenset(str(t) for t in topics)


def _declared_covers(declared: frozenset[str], wanted: str) -> bool:
    """声明 `declared` 是否覆盖处理器想要的 `wanted`。

    · 声明里的通配（`todo.*`）可以覆盖具体 topic（`todo.item.due`）；
    · 处理器**不能**发明声明里没有的通配——`wanted` 带通配字符时必须
      与声明的某一条**原样相等**（否则「处理器要一整个域」而声明只给了一个 topic，
      两者的差集无法在注册期判定，只能在投递期静默漏掉——那正是要消灭的失败模式）。
    """
    if wanted in declared:
        return True
    if any(ch in wanted for ch in "*?["):
        return False
    return any(fnmatch.fnmatchcase(wanted, p) for p in declared)


#: 声明字段的读取器。★ 每个字段**各写一句**，刻意不写成 `manifest.get(field)`：
#: 协议字段的消费点必须能被 `check_protocol_consumption.py` 的 AST 扫描看见；
#: 用变量当键，扫描器看不到 —— 那会让 emits / consumes 被判成「没人读」，
#: 于是门禁的绿灯建立在「扫描器瞎了」上（正是那道门禁最怕的失效方式）。
_FIELD_READERS: dict[str, Callable[[Any], Any]] = {
    "consumes": lambda m: m.get("consumes") if isinstance(m, Mapping) else getattr(m, "consumes", None),
    "emits": lambda m: m.get("emits") if isinstance(m, Mapping) else getattr(m, "emits", None),
}


def _declaration_of(info: Any, field: str = "consumes") -> tuple[str, frozenset[str]]:
    """从 PluginInfo / Manifest 模型 / 原始 manifest dict 里取 `(插件 id, <field> 声明)`。

    三种形态都只读 `id` 与 `field`（默认 `consumes`；发布端传 `emits`）——
    内核不认识插件的目录结构，manifest 是调用方（插件自己或发现层）递进来的。
    """
    reader = _FIELD_READERS.get(field)
    if reader is None:
        raise ValueError(f"未知的声明字段：{field!r}（只认 {sorted(_FIELD_READERS)}）")
    manifest = getattr(info, "manifest", None)
    if manifest is None:
        manifest = info
    if isinstance(manifest, Mapping):
        plugin_id = getattr(info, "id", None) or manifest.get("id")
    else:
        plugin_id = getattr(info, "id", None) or getattr(manifest, "id", None)
    raw = reader(manifest)
    if not plugin_id:
        raise ValueError("声明式订阅/发布缺少插件 id：info 应带 id，或 manifest 里必须有 id")
    if raw is None:
        raw = ()
    if isinstance(raw, str):
        raise TypeError(f"插件 {plugin_id!r} 的 manifest.{field} 必须是 topic 列表，收到字符串")
    return str(plugin_id), frozenset(str(t) for t in raw)


def _match(topic: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    return any(fnmatch.fnmatch(topic, p) for p in patterns)


def _format_sse(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, default=str)
    return f"id: {event['id']}\nevent: {event['topic']}\ndata: {data}\n\n"


# 全局单例
event_bus = EventBus()
```

### 5.11 ⑪ 生命周期宣告契约（固定名钩子 + 声明校验）：`services/api/core/plugins/lifecycle.py`（179 行）

```python
"""生命周期钩子（总纲 §1.3 / 验收 #5）。

插件在 api/lifecycle.py 里可定义四个可选钩子（**名字固定，不可自定义**）：
    def on_install(db: Session) -> None
    def on_enable(db: Session) -> None
    def on_disable(db: Session) -> None
    def on_uninstall(db: Session) -> None

## 声明是契约（2026-11-10 裁决 b）

manifest 的 `lifecycle` 字段从「钩子名映射对象」**降级为枚举**：
`"hooks"` = 本插件提供固定名钩子；`null`/缺省 = 不提供。

为什么降级（而不是让内核去读那份映射、支持自定义名）：
  · 全仓唯一写过映射的 pi-agent，三个值恰好等于固定名 —— 零信息；
  · 自定义名唯一的作用是让作者换名字，代价是「写错名 = 静默不执行」（fail-open），
    与 emits/consumes 的 fail-closed 精神相反；
  · 一个字段如果允许配置却没人需要配置，它就是负担，不是能力。
  ⇒ 名字固定，声明只回答「有没有」，内核用它做**一致性校验**。

## 软失败语义（不变）

钩子内部抛异常 → 被 manager 捕获并写进 plugin_state.last_error，
不许拖垮整个安装/卸载流程。

★ 声明与实现不一致（声明了 hooks 却没文件 / 有钩子却没声明）也走**同一个通道**：
  写进 last_error 供 readyz 目击，但不拦启停 —— 因为钩子本来就是尽力而为的，
  为一句声明把插件挡在门外，代价比收益大。**看得见**比**拦得住**更适合这一层。
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from typing import Any

_STAGES = ("on_install", "on_enable", "on_disable", "on_uninstall")

_STAGE_ATTR = {
    "install": "on_install",
    "enable": "on_enable",
    "disable": "on_disable",
    "uninstall": "on_uninstall",
}

#: 已加载的 lifecycle 模块缓存：`{模块名: (mtime, 模块)}`。
#:
#: ★ 为什么必须缓存（实测 P0）：`_load_lifecycle_module` 原来每次调用都重新
#:   `exec_module`，而模块级代码里的副作用（典型是插件在 import 期调用
#:   `event_bus.subscribe_declared(...)`）会**逐次累积**。
#:   实测 enable→disable→enable 三次之后，事件总线里该插件的条目数 1→2→3，
#:   一条待办到期事件被投递给 3 个 handler —— 用户点五次停用/启用，
#:   同一条推送就会发五次。而且插件自己的计数器在新模块里，它看不见自己被调了三次。
#:
#: 按 mtime 失效：开发时改了 lifecycle.py 仍然会重新加载，不会「改了不生效」。
_LIFECYCLE_CACHE: dict[str, tuple[float, Any]] = {}


def invalidate_lifecycle_cache(plugin_id: str | None = None) -> None:
    """清 lifecycle 模块缓存。插件卸载时调用，避免旧模块的尸体留在内存里。

    `plugin_id=None` 表示全清（测试用）。
    """
    if plugin_id is None:
        _LIFECYCLE_CACHE.clear()
        return
    _LIFECYCLE_CACHE.pop(f"plugin_lifecycle_{plugin_id}", None)


def _load_lifecycle_module(info: Any) -> Any | None:
    path = info.directory / "api" / "lifecycle.py"
    if not path.is_file():
        return None
    name = f"plugin_lifecycle_{info.id}"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    hit = _LIFECYCLE_CACHE.get(name)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001
        return None
    _LIFECYCLE_CACHE[name] = (mtime, mod)
    return mod


def _declared(info: Any) -> str | None:
    """读 manifest 的 `lifecycle` 声明（只认 `"hooks"` 与 `None`）。

    ★ 三种 info 形态（PluginInfo / Manifest 模型 / 原始 dict）都支持 ——
      与 `event_bus._declaration_of` 同口径，内核不为此认识插件目录结构。
    """
    manifest = getattr(info, "manifest", None)
    if manifest is None:
        manifest = info
    if isinstance(manifest, dict):
        raw = manifest.get("lifecycle")
    else:
        raw = getattr(manifest, "lifecycle", None)
    if raw is None:
        return None
    return raw if isinstance(raw, str) else str(raw)


def declaration_problem(info: Any) -> str | None:
    """校验「manifest.lifecycle 声明」与「api/lifecycle.py 实现」是否一致。

    返回 None = 一致；返回字符串 = 不一致（调用方写进 last_error 供 readyz 目击）。

    四种情形：
      · 声明 hooks + 文件在 + 至少一个钩子可调用 ⇒ 一致
      · 声明 hooks + 文件不在 / 没有钩子         ⇒ 「假承诺」
      · 未声明 + 文件不在                        ⇒ 一致（绝大多数插件）
      · 未声明 + 文件里有钩子                     ⇒ 「隐形钩子」（声明漏了）
    """
    declared = _declared(info)
    plugin_id = getattr(info, "id", "?")
    mod = _load_lifecycle_module(info)
    hooks = []
    if mod is not None:
        hooks = [name for name in _STAGE_ATTR.values() if callable(getattr(mod, name, None))]

    if declared == "hooks":
        if mod is None:
            return (
                f"插件 {plugin_id!r} 声明 lifecycle=\"hooks\"，但 api/lifecycle.py 不存在"
                " —— 声明是契约，要么补文件，要么把声明去掉"
            )
        if not hooks:
            return (
                f"插件 {plugin_id!r} 声明 lifecycle=\"hooks\"，但 api/lifecycle.py 里"
                f"没有可调用的固定名钩子 {sorted(_STAGE_ATTR.values())}"
            )
        return None

    if declared is None:
        if hooks:
            return (
                f"插件 {plugin_id!r} 的 api/lifecycle.py 定义了钩子 {hooks}，"
                "但 manifest 未声明 lifecycle=\"hooks\" —— 隐形钩子没人知道会执行，"
                "请补声明（或删掉钩子）"
            )
        return None

    return f"插件 {plugin_id!r} 的 lifecycle 声明值非法：{declared!r}（只认 \"hooks\"）"


def run_lifecycle_hook(info: Any, stage: str, *, db: Any = None) -> str | None:
    """执行某阶段钩子。返回 None 表示成功，返回字符串表示捕获到的错误。

    ★ 声明与实现不一致时，**先返回那句不一致**（软失败，写进 last_error），
      但仍然执行钩子 —— 文件在、函数在，就没理由不跑。
      这样「声明写错了」会被看见，而不是把功能一起停掉。
    """
    if stage not in _STAGE_ATTR:
        raise ValueError(f"未知生命周期阶段：{stage}")
    problem = declaration_problem(info)
    mod = _load_lifecycle_module(info)
    if mod is None:
        return problem
    fn = getattr(mod, _STAGE_ATTR[stage], None)
    if not callable(fn):
        return problem
    try:
        if db is not None:
            fn(db=db)
        else:
            fn()
    except Exception:  # noqa: BLE001
        return traceback.format_exc()
    return problem
```

---

## 6 · 生成信息

```bash
# 重新生成（改 schema 后必须重跑）
python3 tools/gen_protocol_doc.py --out <目标路径>.md

# 只核对，不写盘（应当输出「与 schema 一致」）
python3 tools/gen_protocol_doc.py --check <目标路径>.md
```

- 生成器：`tools/gen_protocol_doc.py`
- 真相源：`contracts/plugin.schema.json`
- 顶层字段数：37　必填字段数：11　条件必填：1
- 默认输出：stdout（**不会**自动写进任何笔记目录，必须显式 `--out`）
