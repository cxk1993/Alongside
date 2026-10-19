"""补刀判据 · pi 会话历史读取（workbuddy 2026-09-27）。

背景：主人报障「找不到历史」→ 实测后端 8 端点无读消息接口 → 补 `history.py` + `GET /sessions/history`。
本测试用**自造 JSONL**（不依赖真实 runtime）锁住归一化语义：
  ① 只吃 `type=="message"`，坏行/半行**跳过不崩**；
  ② user/assistant 的内容从 `content:[{type:text}]` 正确抽取；thinking/toolCall **丢弃**；
  ③ `toolResult` 默认**不出**（对话流纯净），`include_tools=True` 才出；
  ④ `limit` 取**末尾 N 条**；⑤ 目录为空/不存在时返回**空结构而非异常**。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import history


def _write_session(dir_: Path, name: str, events: list[dict]) -> Path:
    p = dir_ / name
    with p.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    return p


@pytest.fixture()
def sessions_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "sessions"
    d.mkdir()
    monkeypatch.setattr(history, "SESSIONS_DIR", d)
    return d


def test_normalizes_and_skips_noise(sessions_dir: Path) -> None:
    _write_session(
        sessions_dir,
        "2026-09-26T00-00-00-000Z_aaaa-bbbb.jsonl",
        [
            {"type": "session", "version": 3, "id": "aaaa-bbbb"},  # 头：跳过
            {"type": "model_change", "provider": "alongside"},  # 非 message：跳过
            {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "你好"}]}},
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "text": "（内心戏，应丢弃）"},
                        {"type": "toolCall", "name": "x"},  # 应丢弃
                        {"type": "text", "text": "答案"},
                    ],
                },
            },
            {"type": "message", "message": {"role": "toolResult", "toolName": "mcp", "content": [{"type": "text", "text": "工具输出"}]}},
        ],
    )
    # 坏行（半行）也不能崩
    with (sessions_dir / "2026-09-26T00-00-00-000Z_aaaa-bbbb.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"type":"message","message":{"role":"user","content":[{"type":"tex\n')

    out = history.read_messages()
    assert out["session_id"] == "aaaa-bbbb"
    assert [m["role"] for m in out["messages"]] == ["user", "assistant"], out
    assert out["messages"][1]["text"] == "答案"  # thinking/toolCall 已丢弃
    assert out["count"] == 2


def test_tools_opt_in(sessions_dir: Path) -> None:
    _write_session(
        sessions_dir,
        "2026-09-26T00-00-01-000Z_cccc.jsonl",
        [
            {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "q"}]}},
            {"type": "message", "message": {"role": "toolResult", "toolName": "mcp", "content": [{"type": "text", "text": "t"}], "isError": False}},
        ],
    )
    assert history.read_messages()["count"] == 1  # 默认不含 tool
    with_tools = history.read_messages(include_tools=True)
    assert with_tools["count"] == 2
    tool = [m for m in with_tools["messages"] if m["role"] == "tool"][0]
    assert tool["tool"] == "mcp" and tool["error"] is False


def test_limit_takes_tail(sessions_dir: Path) -> None:
    _write_session(
        sessions_dir,
        "2026-09-26T00-00-02-000Z_dddd.jsonl",
        [
            {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": f"m{i}"}]}}
            for i in range(5)
        ],
    )
    out = history.read_messages(limit=2)
    assert out["total"] == 5 and out["count"] == 2
    assert [m["text"] for m in out["messages"]] == ["m3", "m4"]  # 取末尾


def test_empty_dir_is_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(history, "SESSIONS_DIR", tmp_path / "not-exist")
    out = history.read_messages()
    assert out["count"] == 0 and out["messages"] == [] and out["detail"]
