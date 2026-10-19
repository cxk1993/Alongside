# Alongside · 在你身边

> **AI 人生协助系统** —— 让每个人都能拥有一套属于自己的 AI 人生协助系统。
> **在这套系统里，人与 AI 是同行者**，在同一段人生内前行，能帮助人更好地成长。
> **内核只提供框架，能力全部由插件长出来** —— 详见 [`docs/adr/0002-插件协议.md`](docs/adr/0002-插件协议.md)。

Alongside 是一个**单人自托管**的个人系统：窗口化的前端宿主 + 后端内核 + MCP 服务。
**AI 帮人从重复、杂、碎、忙的事务里解放出来，留出时间处理更重要的事情** ——
它主动记录、主动提醒、主动复盘，也让人始终保留控制权：可以拒绝它的提醒，可以调整它给的解释，
也可以随时知道「哪些信息是它从你那边知道的，哪些是它推测的」。

它连接**可替换的 Agent、工具与个人生活资料**，帮人整理信息、安排事务、持续学习和复盘成长，
让技术不再是少数人的门槛。AI 可以分担生活的重压，也可以放大一个人的思绪与行动；
**人生的方向、意义和体验，仍由人自己决定、自己走过。**

内核只管七件事——窗口 / 路由 / 鉴权 / 事件 / 数据 / 设置 / 扩展点；
日程、待办、习惯、理财、笔记、日记、健康、复盘……每一项能力都以**插件**形式长在内核之上。

---

## 在线 demo

> **想看它跑起来的样子？**
>
> 演示机是常驻在跑的，但**地址不公开挂** —— 需要的话，**联系我获取**（QQ `2868720251`）。
>
> 拿到地址和口令后，你会看到一个能用的「毛坯房」：
>
> - 点开桌面上的 **「理财」** 插件 —— 它内嵌着一个真实的 BeeCount 演示实例
>   （独立演示账本、示范数据，**不是任何人的真实账目**，随便点）。
> - 它是个 **PWA**：**Chrome / Edge 打开后，地址栏会出现「安装」图标**，
>   点一下就能装成桌面/手机 App —— 单独窗口、断网也能开，像原生应用一样常驻。
>
> （毛坯房、空数据，欢迎随便点。）

---

## 设计取向

- **一切皆插件**：插件在 `manifest.json` 里声明自己的窗口、路由、权限与能力，内核机械地发现它们。
- **MCP 是 AI 的正门**：插件声明的能力会映射成 MCP 工具，外部 agent 可以像调用本地能力一样调用它们。
- **数据主权**：所有数据在一个 `data/` 目录里（SQLite + WAL），迁移 = 复制目录，备份 = 复制文件。

### 命题要高，锚点要实，证据要散

这个项目想回答的不是「怎么管理时间」，而是「**AI 和人到底是什么关系**」。所以三件事得同时成立：

| | 要做到 | 具体是 |
|:--|:--|:--|
| **命题要高** | 别把问题问小了 | 问的是 AI 和人的关系，不是某个功能好不好用 |
| **锚点要实** | 落在一个真实的人身上 | 先让几个人真实地用一段时间，看它对人有什么影响 |
| **证据要散** | 别指望一句话总结 | 靠一件件看得见的变化，而不是一句高深的结论 |

**让高处的命题，从低处的人的体验里自然长出来。**

> 如果把宏大理念拆得太细、太简单，反而会失掉它作为「问题内核」的重量。
> 把「AI 和人的关系」改成「一个帮你整理日程的软件」，可这是**降维**，而不是落地。

> 人和 AI 的关系当然很好说，但**人和 AI 的关系，必须通过一个人的真实变化来证明。**

---

## 5 分钟上手

### 前置依赖

| 需要 | 版本 | 说明 |
|:--|:--|:--|
| **Node** | 20+（见 `.nvmrc`） | 前端构建与开发服务器 |
| **Python** | **3.11.x**（锁定，别用 3.12/3.13） | 后端运行时 |
| `make` | 可选 | **没有也能用**，见下方说明 |
| `docker` | 可选 | 只在服务器部署时需要，本机开发不需要 |

> **没有 `make` 也能用。** 项目入口的真实实现是跨平台脚本 `tools/task.py`，
> 根目录 `Makefile` 只是它的薄壳。两条路**行为完全一致**：
>
> ```bash
> make dev                     # 有 make 的机器
> python tools/task.py dev     # 任何机器都能跑（推荐，含 Windows 无 make 的情况）
> ```

### 三条命令跑起来

```bash
python tools/task.py setup    # 1. 首次：建 venv + 装前后端依赖（会慢一点）
python tools/task.py dev      # 2. 同时起前端与后端
python tools/task.py verify   # 3. 交付前自检：lint + test + 构建
```

起来之后：

| 地址 | 是什么 |
|:--|:--|
| http://localhost:5173 | 前端（开发服务器，改代码自动刷新） |
| http://localhost:8000/docs | 后端 API 文档（FastAPI 自动生成） |
| http://localhost:8000/healthz | 健康检查，返回 `{"ok":true}` |

`Ctrl+C` 会把前后端一起停掉。

### 全部命令

| 命令 | 作用 |
|:--|:--|
| `setup` | 建 venv + 装依赖（首次） |
| `dev` | 同时起前端 5173 与后端 8000 |
| `lint` | 前端 tsc + eslint + prettier ／ 后端 ruff + mypy |
| `test` | 前端 vitest ／ 后端 pytest |
| `verify` | **交付前必跑**：lint + test + 前端构建 |
| `build` | 前端产物（有 docker 时顺带建镜像） |
| `clean` | 清构建缓存，**`data/` 绝不动** |
| `new-plugin --id x --name "名字"` | 生成新插件骨架 |

---

## 目录结构

```
life/
├─ apps/web/                  # 前端宿主：只提供"窗"，不含业务
│  └─ src/kernel/             #   ★ 内核：窗口 / 坞 / 顶栏
│     ├─ plugins/             #   ★ 插件加载器
│     └─ slots/               #   ★ 扩展点定义
├─ services/api/              # 后端宿主：只提供"路由/鉴权/事件"，不含业务
│  ├─ main.py                 #   入口
│  ├─ core/                   #   配置/鉴权/日志/异常
│  ├─ db/                     #   内核级模型与迁移
│  └─ modules/<id>/           #   各插件的后端部分
├─ plugins/                   # 第三方插件投放目录
├─ services/bridge/           # 本机 Obsidian 桥
├─ contracts/                 # ★ 契约：插件 Schema / OpenAPI / 数据字典（改动需 RFC）
├─ deploy/                    # 镜像 / nginx / 证书 / 备份脚本
├─ docs/
│  ├─ adr/                    # 架构决策记录（含"被否决的方案 + 理由"）
│  ├─ issues/                 # 问题记录
│  ├─ specs/                  # 数据/接口规范
│  ├─ tutorials/              # 插件开发入门
│  └─ rfc/                    # 契约变更申请
├─ tools/task.py              # 项目唯一入口的真实实现
├─ docker-compose.yml         # 生产编排（两个服务，db 用卷不用容器）
└─ docker-compose.dev.yml     # 开发编排（可选，本机用不上）
```

---

## 我该先读哪份文档？

| 我想…… | 先读 | 再读 |
|:--|:--|:--|
| **知道为什么这么设计** | `docs/adr/0001-技术栈选型.md` | `docs/adr/0002-插件协议.md` |
| **写一个插件** | `docs/adr/0002-插件协议.md` | `docs/tutorials/plugin-dev-101.md` |
| **改接口 / 表结构** | `docs/rfc/TEMPLATE.md` | `contracts/README.md`（**不许自己改契约**） |
| **部署到服务器** | `deploy/README.md` | `docker-compose.yml` |
| **遇到了问题** | `docs/issues/TEMPLATE.md` | —— |

> ⚠️ **不要把整个仓库的文档从头读一遍。** 读你需要的部分就够了 ——
> 长文档会让关键约束被稀释掉。

---

## 外部集成：我喜欢它，所以为它做了插件

Alongside 不重复造轮子。有些事别人已经做得很好 —— 那就为它们写一个插件，把它们接进来。

| 项目 | 它是什么 | 怎么接 | 它的许可 |
|:--|:--|:--|:--|
| [BeeCount](https://github.com/TNT-Likely/BeeCount) · [BeeCount-Cloud](https://github.com/TNT-Likely/BeeCount-Cloud) | 本地优先的跨端记账（自建云 / 多端同步 / MCP） | `services/api/modules/finance/` 走 MCP + REST 对接，Web 端由 `deploy/` 同源反代内嵌 | 自定义许可（个人免费，商用需授权） |
| [pi-web-ui](https://github.com/xing-shuyin/pi-web-ui) | 浏览器里的 AI 编码座舱（chat / code / files / terminal / git） | **独立部署**（`npm i -g pi-web-ui`，默认 8787）：由 nginx 反代到 `/pi/` 直接访问；也可用网页插件把它接进 Alongside —— 插件窗口内嵌其 URL，凭据经 `auth_ref` 注入、不明文入库 | MIT |
| [pi](https://github.com/earendil-works/pi)（`@earendil-works/pi-coding-agent`） | 带 read / bash / edit / write 工具的编码 agent CLI | `plugins/pi-agent/` —— 一个 **agent 适配器插件**（RPC 子进程 + 会话池）。★ 它只是**第一个**；opencode、deepseek harness 等可用同一结构各写一个 | MIT |

> **本仓库不包含以上任何项目的源码 —— 它们都是独立部署的。** 由你自己安装（Docker / npm），
> Alongside 只是通过公开接口接上话 —— 这既是干净的集成方式，也正是它们的许可所允许的。
> 版权与许可声明见 [`NOTICE`](NOTICE)。

> **这就是「一切皆插件」的样子。**
> 我欣赏一个项目，就为它写一个插件，把它接进我自己的系统。
> 你也可以这么做 —— 为你喜欢的任何项目、任何 App、任何设备，搓一个属于你自己的插件。
> 连 AI agent 也一样：`plugins/pi-agent/` 就是个例子，**换一个 agent 只需换掉最下面那层桥接**。
> 插件协议是公开的（`docs/tutorials/plugin-dev-101.md`），**不必先问谁，也不必先得到允许**。
> 
> **记得给喜欢的他们点star呐~**

---

## 服务器端部署：本体之外的附属服务

> **Alongside 本体很轻** —— 一个 FastAPI 后端 + 一个静态前端，Python + Node 就能跑起来。
> 但**它有几个插件是接别人的**：一旦你要在服务器上把「理财」「智能体」真正用起来，
> 就得额外跑下面这些服务。
>
> ⚠️ **本仓库不含它们的源码** —— 它们由你自己安装（它们的许可也是这么要求的）。
> 这就是「一切皆插件」的代价，也是它的自由：**你可以只装你要用的那几个。**

| 附属服务 | 谁需要它 | 怎么装 | 关键点 |
|:--|:--|:--|:--|
| **Node ≥ 22.19** | 「智能体」插件 | [官方 tarball](https://nodejs.org/) / nvm | 前端构建只要 20+，但 **pi 硬性要求 ≥22.19** —— 直接装 22.19+，两者通吃 |
| **pi CLI**<br>`@earendil-works/pi-coding-agent` | 「智能体」插件 | `npm i -g --prefix ~/.npm-global` | 插件按 `~/.npm-global/bin/pi` 找它 —— **装到别处它找不到** |
| **pi-web-ui** | 「智能体」专业模式（`/pi/`） | `npm i -g pi-web-ui` | 独立服务（默认 `127.0.0.1:8787`），由 nginx 反代 + token 墙 |
| **BeeCount-Cloud** | 「理财」插件 | `docker pull sunxiao0721/beecount-cloud` | 账本上游；插件经 MCP **只读**对接，不自带账本 |
| **nginx + 证书** | 对外访问 | acme.sh / certbot | HTTPS；**`/api/` 永不缓存**是硬要求 |

**一项都不装，系统照样能跑。** 插件可以单独启用 / 禁用 / 卸载，内核不会因为你没装 pi 就启动失败 ——
你只想用日程 / 待办 / 习惯 / 笔记，那 `python tools/task.py dev` 就够了。

> 现成的片段就在 [`deploy/`](deploy/) 下：`nginx/lifos.conf`（含 `location /api/` 三行与 BeeCount 同源反代）、
> `systemd/lifeos-api.service`、`Dockerfile.api` / `Dockerfile.web`；逐步说明见 [`deploy/README.md`](deploy/README.md)。

---

## 设计边界（给使用者）

Alongside 是**单人、自托管**的个人系统，有些事是刻意不做的：

- **不做多用户**：没有注册、没有权限组、没有邀请码 ——
  这是设计选择，不是还没做完。这里只有你和你自己的数据。
- **数据主权**：所有数据在一个 `data/` 目录里（SQLite + WAL），
  换机器 = 复制目录，离开 = 带走文件。
- **内核里不出现业务词汇**：`kernel/`、`core/` 下不写 calendar/todo/finance ——
  这是「一切皆插件」唯一能被机器验证的判据。

---

## 协作规则

> 本项目的开发由**人与 AI agent 共同完成**。仓库里既有写给人看的文档，也有写给 AI 的规则 ——
> 下面分开列，不混在一起。

### 通用约定（人 / AI 都遵守）

- **密钥只进 `.env`**：绝不进代码、绝不进前端、绝不进 git；`.env.example` 只放占位符。
- **时间统一带时区**：出入参 ISO8601（`2026-09-15T08:00:00+08:00`），库里存 UTC。
- **颜色只走设计令牌**：不写死色值，主题才能整体切换。
- **契约优先**：`contracts/**`、`manifest.json`、数据字典是接口，改动走 `docs/rfc/`。
- **插件之间不直接 import**：只走 ① 事件总线 ② 对方公开的 API ③ 扩展点 —— 插件才能各自独立启用 / 禁用 / 卸载。

### 给 AI 协作者的附加规则

> 本仓库的 AI 协作者（Claude / Cursor / 各类 agent）工作时必须遵守；
> 违反任何一条，工作视为未完成。
>
> 人类贡献者请读 `docs/adr/0002-插件协议.md` 与 `contracts/README.md`。

- **契约已冻结**：不许自己改 `contracts/**`、`manifest.json`、数据字典 —— 要改，先走 `docs/rfc/`。
- **不许从零发明**：项目里已有现成的骨架，照着用最省力、省时，质量也稳；当然，愿意自己弄一套更优的架构也可以，但请先把设计说清楚、跟大家对齐。
- **不许越界**：不碰别人的目录与领地；需要对方改，去 `docs/issues/` 写卡。
- **不许报假 done**：做不完就报 `partial` 并说清卡在哪；拿 mock 冒充真实数据直接判不通过。

完整规则见 [`docs/adr/0002-插件协议.md`](docs/adr/0002-插件协议.md)。

---

## 数据在哪

全部在 **`data/`** 一个目录里（SQLite + WAL）。

- **备份**：`sqlite3 data/lifos.db ".backup data/backups/lifos-<时间>.db"`（**不要直接 `cp`**）
- **迁移**：复制整个 `data/` 目录即可
- `data/` 已在 `.gitignore` 里，不会被提交

---

## 许可证

本项目采用 [Apache License 2.0](LICENSE) 许可。欢迎 fork、二次开发与商业使用，
保留版权与许可声明即可。

---

## 参与

**不管你会不会写代码，都有入口。**

| 你是 | 走哪 | 从哪开始 |
|:--|:--|:--|
| **有想法 / 有需求** | 提 Issue | 说清"想干什么、现在为什么干不了" |
| **想动手做** | 提 PR | 插件 → `docs/tutorials/plugin-dev-101.md`；界面 / 文档 / PPT → 先开 Issue 认领 |
| **发现 Bug** | 提 Issue | 照 `docs/issues/TEMPLATE.md` 填 |
| **想改契约** | 走 RFC | `docs/rfc/TEMPLATE.md`（**契约不许自己改**） |
| **第一次来** | 看 `good first issue` | 挑一个小的先上手 |

> 想先了解为什么这么设计 → `docs/adr/0001-技术栈选型.md`
> 想写插件 → `docs/adr/0002-插件协议.md`
> **详细规则见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。**

### 新手可以从哪开始

- 文档里看不懂的地方 → **改了它**（最好的入门任务）
- 补一个插件的示例（`docs/示例/`）
- 给现有插件补一条测试
- 报一个你用起来别扭的地方
