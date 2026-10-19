"""补刀 · pi 会话历史读取（workbuddy 2026-09-27 主人授权代办）。

## 为什么有这个文件
主人报障「**找不到历史**」。实测（逐端点核过）：`api/router.py` 全部 8 个端点里
**没有任何"读取会话消息"的接口** —— 所以不是"读路径不统一"，是**下游端点不存在**。
本模块补齐这半边（**存储格式侧** —— 另半边 web 层由 AstrBot 的 `plugins/pi-agent/web/` 承载）。

## 存储格式（实测样本分析所得，非猜测）
pi 把会话记录写在 `runtime/sessions/{ISO时间戳}_{uuid}.jsonl`，**每行一个 JSON 事件**：

```json
{"type":"session","version":3,"id":"<uuid>","timestamp":"...","cwd":"..."}       ← 会话头
{"type":"model_change","provider":"alongside","modelId":"alongside"}                ← 模型切换
{"type":"thinking_level_change"}                                                ← 思考级别
{"type":"message","message":{"role":"system","content":"..."}}
{"type":"message","message":{"role":"user","content":[{"type":"text","text":"..."}]}}
{"type":"message","message":{"role":"assistant","content":[{"type":"thinking"},{"type":"toolCall"},{"type":"text","text":"..."}]}}
{"type":"message","message":{"role":"toolResult","toolName":"...","isError":false,"content":[{"type":"text","text":"..."}]}}
```

**归一化**：只取 `type=="message"` 且 role ∈ {user, assistant}（toolResult 可选返回）；
content 是 list 时拼 `type=="text"` 的 parts（**丢 thinking/toolCall** —— 前端展示不需要）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("pi-agent.history")

# runtime/ 与 api/ 同级（api/history.py → ../runtime/sessions）
RUNTIME_DIR = Path(__file__).resolve().parent.parent / "runtime"
SESSIONS_DIR = RUNTIME_DIR / "sessions"
DEFAULT_LIMIT = 200


def _session_files() -> list[Path]:
    """全部会话文件，**按 mtime 倒序**（最新在前）。目录不存在返回空。"""
    if not SESSIONS_DIR.is_dir():
        return []
    try:
        return sorted(
            SESSIONS_DIR.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError as exc:  # 权限/竞态
        log.warning("读取 sessions 目录失败: %s", exc)
        return []


def _file_session_id(path: Path) -> str:
    """从文件名解析 pi 的会话 id（`{ISO}_{uuid}.jsonl` → uuid；解析不出则退回文件名）。"""
    stem = path.stem
    return stem.rsplit("_", 1)[-1] if "_" in stem else stem


def resolve_session_file(session: str | None) -> Path | None:
    """`session` 名/编号/id → 对应文件。

    解析顺序（容错，永不抛）：
      1. 精确匹配 pi session id（文件名 uuid 段）；
      2. 匹配文件名前缀（时间戳段）；
      3. 空/`default`/`latest` → **最新一个**（前端默认会话名就是 `default`）。
    """
    files = _session_files()
    if not files:
        return None
    key = (session or "").strip()
    if key and key not in {"default", "latest"}:
        for p in files:
            if _file_session_id(p) == key or p.stem.startswith(key):
                return p
        # 数字索引（0 = 最新）
        if key.isdigit():
            idx = int(key)
            if 0 <= idx < len(files):
                return files[idx]
    return files[0]


def _parts_to_text(content: Any) -> str:
    """content（str | list[part]）→ 纯文本。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
            chunks.append(str(part["text"]))
    return "\n".join(chunks)


def read_messages(
    session: str | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
    include_tools: bool = False,
) -> dict[str, Any]:
    """读取某会话的历史消息（归一化）。

    返回：
        {
          "session": "<文件名 stem>",
          "session_id": "<pi uuid>",
          "count": <返回条数>,
          "total": <文件内消息总数>,
          "messages": [{"role": "user|assistant|tool", "text": "...", "ts": "...", "tool": "..."}],
        }
    """
    path = resolve_session_file(session)
    if path is None:
        return {
            "session": None,
            "session_id": None,
            "count": 0,
            "total": 0,
            "messages": [],
            "detail": "无会话记录（runtime/sessions 为空）",
        }

    msgs: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 半行/损坏行跳过（pi 正在写时可能截断）
                if not isinstance(evt, dict) or evt.get("type") != "message":
                    continue
                m = evt.get("message")
                if not isinstance(m, dict):
                    continue
                role = m.get("role")
                if role in {"user", "assistant"}:
                    text = _parts_to_text(m.get("content"))
                    if text.strip():
                        msgs.append(
                            {
                                "role": role,
                                "text": text,
                                "ts": m.get("timestamp") or evt.get("timestamp") or "",
                            }
                        )
                elif role == "toolResult":
                    if not include_tools:
                        continue
                    msgs.append(
                        {
                            "role": "tool",
                            "text": _parts_to_text(m.get("content")),
                            "ts": m.get("timestamp") or evt.get("timestamp") or "",
                            "tool": str(m.get("toolName") or ""),
                            "error": bool(m.get("isError")),
                        }
                    )
    except OSError as exc:
        log.warning("读取会话文件失败: %s", exc)
        return {
            "session": path.stem,
            "session_id": _file_session_id(path),
            "count": 0,
            "total": 0,
            "messages": [],
            "detail": f"读取失败：{exc}",
        }

    total = len(msgs)
    tail = msgs[-limit:] if limit > 0 else msgs
    return {
        "session": path.stem,
        "session_id": _file_session_id(path),
        "count": len(tail),
        "total": total,
        "messages": tail,
    }
