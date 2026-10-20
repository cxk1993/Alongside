# deploy/ —— 部署目录（T13 的领地）

**这个目录归 T13（部署 · 安全 · 验收）所有。** 其它卡只读。

## 预期会长出什么

这些由 T13 交付，**T01 刻意没有预置**：

```
deploy/
├─ Dockerfile.api          # 后端镜像
├─ Dockerfile.web          # 前端镜像（多阶段：build → nginx 静态）
├─ nginx/alongside.conf        # 反向代理 + SSE 三行 + 安全头
├─ scripts/backup.sh       # SQLite 热备（sqlite3 .backup + 完整性校验）
└─ certs/                  # acme.sh / Cloudflare DNS 签发的证书（不进 git）
```

> 根目录的 `docker-compose.yml` 引用了 `deploy/Dockerfile.api` 与 `deploy/Dockerfile.web`。
> **在 T13 交付这两个 Dockerfile 之前，`docker compose build` 会失败** —— 这是预期行为，不是 bug。
> 本机开发不需要 docker，直接用 `python tools/task.py dev`。

## 本机开发 vs 服务器部署

| | 本机（Win11） | 服务器（Linux） |
|:--|:--|:--|
| 启动 | `python tools/task.py dev` | `docker compose up -d` |
| 前端 | Vite dev server 5173 | 构建成静态产物，由 nginx 托管 |
| 后端 | uvicorn --reload 8000 | uvicorn 容器内 8000，只绑 127.0.0.1 |
| 数据 | 项目内 `data/` | 卷挂载 `./data:/app/data` |
| 对外 | 无 | nginx 8443（备案期）/ 443（备案后） |

**迁移 = 复制 `data/` 目录。** 这是选 SQLite 的核心好处，别把它弄丢。
