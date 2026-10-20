# 本机笔记桥（services/bridge）

跑在主人 **Windows 本机** 上的独立 FastAPI 服务，只监听 `127.0.0.1`，
经 **frpc 隧道**暴露到云服务器。云服务器侧 `services/api/modules/notes/` 通过签名请求拉取索引与全文。

**T24 起定位扩展**：从「笔记桥」升级为「通用本机代理」——笔记读写之外，
增加 **桌面通知**（场景 B / 晨昏简报 / 健康提醒的投递通道）。

## 端点（内网隧道，仅服务器可访问）

| 方法 | 路径 | 说明 |
|:--|:--|:--|
| GET  | `/bridge/healthz` | 存活 + 版本 + **capabilities** + 库列表 |
| GET  | `/bridge/libs` | 库配置（含 md 数量） |
| GET  | `/bridge/scan?lib=&limit=` | 全量索引（流式 ndjson，大库不 OOM） |
| GET  | `/bridge/read?lib=&path=` | 读全文（content + mtime + hash） |
| GET  | `/bridge/changes?since=` | 增量变化（供增量索引） |
| POST | `/bridge/write` | 写回（v0.1 默认 403，仅 mode=rw 放行） |
| POST | `/bridge/notify` | **T24** 桌面通知。JSON `{title, body, app_id?, channel?}` |
| GET  | `/bridge/notifications?since=` | **T24** 通知投递历史（环形，约 200 条） |

### notify 请求体

| 字段 | 说明 |
|:--|:--|
| `title` | 标题，默认 `Alongside`，最长 120 |
| `body` | 正文，**必填非空**，最长 2000 |
| `app_id` | Toast 应用名，默认 `Alongside` |
| `channel` | `auto`（默认）\| `winotify` \| `powershell` \| `log`（测试用，不弹窗） |

通知通道优先级：`winotify`（若安装）→ `PowerShell Toast` → 日志降级。
签名规则与笔记端点完全一致（云侧 `modules/notes/bridge_client.py` 同算法）。

## 自定义笔记夹（B2）

`config.yaml` 的 `libs[]` 即全部笔记库。增加自定义文件夹：

```yaml
  - id: my-notes
    name: 我的笔记夹
    path: "./vault/主仓库/某文件夹"
    mode: ro
    enabled: true
    include: ["**/*.md"]
```

改完 `config.yaml` 后重启 nssm 服务 `alongside-bridge` 生效。`enabled: false` 可先关着。
路径穿越被 `reader.py` 拒绝；响应只回 lib + POSIX 相对路径。

## 鉴权（所有 /bridge/* 必须带）

| Header | 说明 |
|:--|:--|
| `X-Bridge-PSK` | 预共享密钥（与服务器侧 `.env` 的 `BRIDGE_PSK` 一致） |
| `X-Bridge-Ts` | Unix 时间戳（秒），偏差 > 60s 拒绝 |
| `X-Bridge-Nonce` | 随机串，5 分钟内不可重放 |
| `X-Bridge-Sign` | `HMAC-SHA256(PSK, "{METHOD}\|{path?query}\|{ts}\|{nonce}\|{body}")` |

缺 / 错 PSK、错签名、过期时间戳、重放 nonce —— 一律 401。
路径穿越（`../../`）—— 400。

## 安装为 Windows 服务（随系统自启）

用 **nssm**（不要用 schtasks，已踩坑 3 小时）：

```powershell
nssm install alongside-bridge "<repo-root>\services\api\.venv\Scripts\python.exe"
nssm set alongside-bridge AppParameters "-c import sys; sys.path.insert(0, r'<repo-root>\services'); from bridge.main import create_bridge_app; import uvicorn; uvicorn.run(create_bridge_app(), host='127.0.0.1', port=8790)"
nssm set alongside-bridge AppDirectory "<repo-root>\services\bridge"
nssm set alongside-bridge AppEnvironmentExtra BRIDGE_PORT=8790 BRIDGE_PSK=**** BRIDGE_CONFIG=<repo-root>\services\bridge\config.yaml
nssm start alongside-bridge
```

或直接双击 `run.ps1`（同样只监听 127.0.0.1）。

## 安全要点

- **绝不监听 0.0.0.0**：frp 只隧道 `127.0.0.1:8790`。
- **PSK 只走环境变量** `BRIDGE_PSK`，不写进 `config.yaml` 明文、不进代码。
- 响应里只回 `lib + POSIX 相对路径`，绝不回本机绝对路径。
- 只存索引与摘要，不把全文传到服务器（按需经隧道拉取）。

## 默认库

见 `config.yaml`：`main`（主仓库）/ `review`（work review 日报）/ `undo`（复盘）默认启用；
`claude`（云昔专用文件夹）默认 `enabled: false`，主人自行打开。

> ⚠️ 本机没有真实 Obsidian vault 的自动化接入验证：真实 vault 接入需人工在主人机器上确认。
