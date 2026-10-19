# Pi 智能体（插件 id：`pi-agent`）

> **把 CLI 编码 agent 接进 Alongside 的「适配器插件」。**
> 它通过 **RPC 子进程**桥接一个外部 agent，把「对话 + 会话管理」变成 Alongside 的一个插件，
> 并经 MCP 桥把能力暴露成工具、供外部 agent 调用。

> ★ **本仓库不含 pi 的源码。** pi（[`@earendil-works/pi-coding-agent`](https://github.com/earendil-works/pi)，MIT）
> 由使用者自行 `npm install`；本插件只负责「起进程、递消息、收结果」。
> 版权与许可声明见仓库根 [`NOTICE`](../../NOTICE)。

| 项 | 值 |
|:--|:--|
| id | `pi-agent` |
| kind | `third-party`（**可禁用、可卸载**）|
| API 前缀 | `/api/v1/pi-agent` |
| 能力 | `pi.chat.write` · `pi.session.read` · `pi.session.write` |
| 权限 | `fs:plugin` · `net:out:localhost` · `subprocess` |

---

## 1. 这是一个「适配器」，不是「唯一的 agent」

**pi 只是第一个被接进来的 CLI agent。** 桥接范式是通用的 —— 换一个 agent，只需换最下面那一层：

| 层 | 做什么 | 换 agent 要改吗 |
|:--|:--|:--|
| 插件骨架 | manifest / 权限 / 生命周期 / 设置 | 基本不动 |
| **桥接层**（`api/rpc.py`） | 起子进程、说它的协议（JSONL / RPC）、收流 | **要改**（各 agent 协议不同） |
| 会话池（`api/sessions.py`） | 一个 session = 一个子进程，可锁定 / 切换 | 复用 |
| 对外暴露 | `provides` → MCP 工具（ADR-0003 桥） | 复用 |
| 对话 UI | `apps/web/src/apps/pi-agent/` | 复用 |

⇒ **opencode、deepseek harness 等任何 CLI agent，都可以照这个结构各写一个适配器插件。**
本插件不是内核的一部分 —— 禁用 / 卸载它，不影响系统其它任何能力。

---

## 2. 它提供什么

### 2.1 HTTP 端点（前缀由内核按 `manifest.api.base` 挂，代码内不写 prefix）

| 方法 | 路径 | 说明 |
|:--|:--|:--|
| GET | `/health` | 健康探测（含降级等级） |
| GET | `/manifest` | 回显本插件 manifest 原文 |
| GET | `/status` | 运行状态（进程数 / 会话数 / 熔断状态） |
| POST | `/reset-circuit` | 手动复位熔断 |
| POST | `/chat` | 对话 |
| POST | `/chat/stream` | 对话（SSE 流式） |
| GET | `/sessions` | 会话列表 |
| POST | `/sessions` | 新建 / 切换会话 |
| GET | `/sessions/history` | 会话历史 |

### 2.2 经 MCP 暴露的工具（外部 agent 可调）

`pi.chat.write` / `pi.session.read` / `pi.session.write` 三条能力，经 **ADR-0003 MCP 桥**自动变成：

```
pi_chat_write · pi_session_read · pi_session_write
```

### 2.3 会话池

**一个 session = 一个 agent 子进程。** 外部 agent 传 `session="alpha"` / `"beta"` 即得
**互相隔离**的进程 —— 可锁定（独占）、可切换，互不干扰。

### 2.4 降级口径

| 等级 | 含义 |
|:--|:--|
| **L0** | 待命（懒启动，尚未起进程 —— 正常态，非故障） |
| **L1** | 正常（走 agent，有工具能力） |
| **L2** | 重启中 —— **快速失败**，不排队 |
| **L3** | 熔断 —— 明确告知「AI 工具能力暂不可用」，由前端保底轻量对话 |

启动策略是**懒启动**：`on_enable` 只标记启用、不起进程（内核启动路径上不该等一个外部进程），
真正的拉起发生在第一次对话；失败也只是那一次请求降级，**内核与其它插件零影响**。

---

## 3. 安装与配置

```bash
# 1. 装 agent CLI（本插件按 ~/.npm-global/bin/pi 找它）
npm i -g --prefix ~/.npm-global @earendil-works/pi-coding-agent

# 2. 环境变量告诉插件去哪儿找运行时
export PI_CODING_AGENT_DIR=<插件目录>/runtime
```

**模型提供商走插件设置**（`provider` / `model` / `thinking`），改设置即生效、**不用改代码**。
设置项定义在 `api/settings.schema.json`，可在 Alongside 的「设置」页填入。

> ⚠️ **provider / model 必须与 `runtime/models.json` 里的一致**，且 **model id 要填上游网关认的名字**
> —— 写错会得到网关的「无可用模型」错误（历史上这里踩过：设置声明了却完全不生效，因为代码没读它）。

---

## 4. 结构

```
plugins/pi-agent/
├─ manifest.json            # 契约：能力 / 权限 / 生命周期 / 设置入口
├─ api/
│  ├─ router.py             # HTTP 端点（就近定义出入参，第三方插件不能用相对导入）
│  ├─ rpc.py                # ★ 桥接层：起子进程 + JSONL 协议 + 收流
│  ├─ sessions.py           # 会话池（一 session 一进程）
│  ├─ process.py            # 单进程管理器（懒启动）
│  ├─ lifecycle.py          # 启停钩子（懒启动；禁用必须收干净）
│  ├─ history.py            # 会话历史
│  └─ settings.schema.json  # 设置项声明
├─ runtime/                 # agent 运行时数据 + 本插件自持 PAT（★ 全忽略，不入库）
├─ tests/
└─ Dockerfile.pi            # 可选：把 agent 关进容器（Plain Docker 隔离；凭证走环境变量，不落镜像）
```

---

## 5. 改这个插件时不许做的事（照 ADR-0002 硬规则）

1. 不许 import 别的插件 —— 走事件总线 / 对方公开 API / 扩展点
2. 不许 join 别人的表 —— 要数据就调对方 API，并在 `manifest.requires` 里声明
3. 不许自己写登录 —— 内核统一注入 `Authorization: Bearer <jwt>`
4. 不许写死颜色 —— 只用设计令牌（`var(--accent)` 之类）
5. 不许改 `kernel/` 或 `core/` —— 那是框架的 bug，去 `docs/issues/` 写卡
6. ★ **不许用相对导入** —— 第三方插件按文件路径加载（详见 `api/router.py` 顶部注释）
