# ISSUE-001 · `services/api/main.py` 的归属与接管方式

- **提出者**：T01（工程骨架）
- **影响谁**：**T03**（后端内核 · API 服务器）
- **状态**：**fixed**（T03 已按本卡建议方案接管，2026-09-15）
- **优先级**：normal（不阻塞开工，但交付前必须拍板）
- **日期**：2026-09-15

---

## 现象 / 背景

T01 的验收要求是"`services/api/` 是个能跑起来的 FastAPI 空壳，只有 `/healthz`"，
并且 `make dev` 要能同时起前后端。为了满足这一条，T01 建了入口文件：

```
services/api/main.py     ← 目前只有 /healthz 的 12 行空壳
```

`tools/task.py dev` 与 `docker-compose*.yml` 现在都以 `main:app` 为入口：

```
uvicorn main:app --reload --port 8000
```

但 T03 的任务卡里，**`create_app()` 工厂在 `services/api/core/app.py`**（`core/` 是 T03 的领地）。
于是出现了一个 T01 无权单方面决定的问题：**最终入口到底叫什么、由谁维护。**

## 为什么这会挡住后续

如果不拍板，会出现三种坏结果：

1. T03 另外建一个入口（比如 `core/app.py:app`），但 `tools/task.py` 与 compose 仍指向 `main:app`
   → **主人敲 `make dev` 起的是 T01 的空壳，不是真正的内核**，而后端"看起来是好的"。
2. T03 为了让它工作，去改 `tools/task.py` 或 compose —— 那是 T01 建的文件，越界了。
3. 有人加"导入失败就回退到空壳"的兜底逻辑 —— 这会**掩盖 T03 的真实故障**，
   属于总纲明令禁止的静默失败。T01 刻意没有这么写。

## 我的建议（请 T03 拍板）

**保持 `services/api/main.py` 作为全项目唯一的后端入口**，T01 把它**移交给 T03 维护**：

```python
# -*- coding: utf-8 -*-
"""Alongside 后端入口。"""
from __future__ import annotations

from core.app import create_app

app = create_app()
```

理由：

1. `tools/task.py` / `docker-compose.yml` / `docker-compose.dev.yml` 三处入口声明**全都不用改**，
   T02、T07、T13 也不会因为入口改名而返工。
2. 分层干净：`core/app.py` 提供工厂（可测试、可传参），`main.py` 只负责"把它装起来"。
3. 符合 T03 卡片里"`create_app()` 工厂"的设计 —— 入口与工厂本来就是两件事。

## 如果你不同意

请在这个文件末尾追加你的方案（**不要直接改 `tools/task.py`**，那是 T01 的目录），
然后由 T01/编排者统一改三处入口声明。改完把状态置为 `accepted`。

## 需要你做的

- [x] 交付 `core/app.py` 的 `create_app()`
- [x] 接管 `services/api/main.py`，改成上面那两行调用
      —— 实装内容：`from core.app import create_app` + `app = create_app()`，并注明"刻意不写回退兜底"
- [x] 验证：`python tools/task.py dev` 起的是真内核
      —— 证据：真实启动探测 3/3（`/healthz` 200、`/docs` 200）；
      且 `/readyz` 的 `modules` 字段已列出 `auth`，`/api/v1/modules` 返回完整 manifest，
      证明 `create_app()` 与模块发现链路生效（不再是 T01 的空壳）
- [x] 把本卡状态改为 `fixed`

> 补充：T03 一并把入口与 `tools/task.py`、两份 docker-compose 的 `main:app` 约定保持一致，
> 未修改那三处配置（符合本卡建议）。

## 附：T01 阶段的其他交接事项

| 事项 | 说明 |
|:--|:--|
| `services/api/pyproject.toml` | T01 已把总纲 §1.1/§1.6 要求的依赖全部锁好（含 argon2/pyjwt/pyotp/apscheduler），**T03 不需要改它**。若要加包，请在此文件加，并知会 T01 重跑 `verify`。 |
| `pyproject.toml` 的 `[tool.setuptools] packages = []` | 目前后端还没有包。T03 加了 `core/` 之后请改为 `packages = ["core"]`（或改用 find）。这一处**属于 T03**。 |
| `make lint` 里的内核洁癖检查 | T01 的 lint 会在 `services/api/scripts/check_kernel_purity.py` 存在时自动启用，不存在则**打印一行跳过提示**。该脚本归 T14。 |
| `make lint` 的 mypy 目标 | 自动探测 `core/ db/ modules/ main.py`，缺哪个就跳过哪个。所以 T04 未就绪时也不会失败。 |
