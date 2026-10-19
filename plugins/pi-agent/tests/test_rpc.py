"""pi-agent RPC 适配层测试。

分层：
  - **离线测试**（默认跑）：协议解析 / 事件语义 / 降级层级 / 熔断退避 —— 不需要真 pi。
  - **真机测试**（`-m live`，需 pi + alongside 路由）：真起子进程跑一轮对话。

跑法：
    cd services/api && pytest ../../plugins/pi-agent/tests -q            # 只跑离线
    cd services/api && pytest ../../plugins/pi-agent/tests -q -m live   # 含真机
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
API_DIR = PLUGIN_DIR / "api"


def _load(name: str):
    """按路径加载插件模块（与内核加载第三方插件同款，不能用相对导入）。"""
    mod_name = f"pi_agent_test_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, API_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── rpc.py：协议解析与语义 ──────────────────────────────────────

def test_pi_event_text_delta():
    rpc = _load("rpc")
    ev = rpc.PiEvent(type="message_update", raw={
        "assistantMessageEvent": {"type": "text_delta", "delta": "你"}
    })
    assert ev.text_delta == "你"
    assert not ev.is_settled


def test_pi_event_non_delta_returns_none():
    rpc = _load("rpc")
    ev = rpc.PiEvent(type="message_update", raw={
        "assistantMessageEvent": {"type": "thinking_delta", "delta": "想"}
    })
    assert ev.text_delta is None


def test_pi_event_settled_is_terminal():
    """★ agent_settled 是可靠终结信号（不是 agent_end）。"""
    rpc = _load("rpc")
    assert rpc.PiEvent(type="agent_settled").is_settled
    assert not rpc.PiEvent(type="agent_end").is_settled


def test_find_pi_binary_prefers_explicit(tmp_path):
    rpc = _load("rpc")
    fake = tmp_path / "pi"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    assert rpc.find_pi_binary(str(fake)) == str(fake)


def test_find_pi_binary_missing_returns_none_or_path():
    """不存在的显式路径 → None（调用方据此报"请先安装 pi"）。"""
    rpc = _load("rpc")
    assert rpc.find_pi_binary("/nonexistent/pi-xyz") is None


# ── process.py：降级 / 熔断 / 退避（全离线）────────────────────

def test_manager_starts_at_l0_when_never_started(tmp_path):
    """★ 2026-09-28（方案 A）：未启动过的管理器层级是 **L0「待命」**（懒启动正常态），

    不是 L1（没进程）也不是 L2（那是"尝试过但失败"）。
    原断言 `== L2` 正是「状态点恒显启动中」的后端根因，随本次修复一并更正。
    """
    proc = _load("process")
    m = proc.PiProcessManager(cwd=str(tmp_path))
    assert m.level == proc.LEVEL_L0
    assert m.status()["alive"] is False
    assert m.status()["ever_started"] is False


def test_manager_l2_after_failed_start_attempt(tmp_path):
    """★ 2026-09-28（方案 A）：**尝试过**启动但没进程 → L2（真·异常），与 L0 区分开。"""
    proc = _load("process")
    m = proc.PiProcessManager(cwd=str(tmp_path), binary="/nonexistent/pi-xyz")
    assert m.level == proc.LEVEL_L0          # 先待命
    m.ensure_started()                        # 试一次（必失败）
    assert m.status()["ever_started"] is True
    assert m.level == proc.LEVEL_L2           # 试过失败 → 才是"启动失败"


def test_backoff_grows_exponentially_and_caps(tmp_path):
    proc = _load("process")
    m = proc.PiProcessManager(cwd=str(tmp_path))
    m._consecutive_fails = 1
    assert m._backoff_seconds() == proc.BACKOFF_BASE_S
    m._consecutive_fails = 2
    assert m._backoff_seconds() == proc.BACKOFF_BASE_S * 2
    m._consecutive_fails = 3
    assert m._backoff_seconds() == proc.BACKOFF_BASE_S * 4
    m._consecutive_fails = 20
    assert m._backoff_seconds() == proc.BACKOFF_MAX_S  # 封顶


def test_circuit_opens_after_threshold(tmp_path):
    """★ 10 分钟内 ≥5 次失败 → 熔断（停止自动重启）。"""
    proc = _load("process")
    m = proc.PiProcessManager(cwd=str(tmp_path))
    assert not m._circuit_open
    for _ in range(proc.CIRCUIT_FAILS):
        m._note_failure()
    assert m._circuit_open is True
    assert m.level == proc.LEVEL_L3


def test_circuit_blocks_start_attempt(tmp_path):
    """★ 熔断后 ensure_started 必须**快速失败**（不排队、不反复起）。"""
    proc = _load("process")
    m = proc.PiProcessManager(cwd=str(tmp_path))
    for _ in range(proc.CIRCUIT_FAILS):
        m._note_failure()
    assert m.ensure_started() is False


def test_circuit_reset_recovers(tmp_path):
    proc = _load("process")
    m = proc.PiProcessManager(cwd=str(tmp_path))
    for _ in range(proc.CIRCUIT_FAILS):
        m._note_failure()
    assert m._circuit_open
    m.reset_circuit()
    assert not m._circuit_open
    assert m._consecutive_fails == 0


def test_success_clears_failure_streak(tmp_path):
    proc = _load("process")
    m = proc.PiProcessManager(cwd=str(tmp_path))
    m._note_failure()
    m._note_failure()
    m._note_success()
    assert m._consecutive_fails == 0


def test_prompt_raises_when_circuit_open(tmp_path):
    """L3 时 prompt 直接抛 PiRpcError（调用方据此降级到轻量对话）。"""
    proc = _load("process")
    m = proc.PiProcessManager(cwd=str(tmp_path))
    for _ in range(proc.CIRCUIT_FAILS):
        m._note_failure()
    with pytest.raises(proc.PiRpcError):
        list(m.prompt("hi"))


def test_singleton_and_shutdown(tmp_path):
    proc = _load("process")
    m1 = proc.get_manager(cwd=str(tmp_path))
    m2 = proc.get_manager()
    assert m1 is m2
    proc.shutdown_manager()
    m3 = proc.get_manager(cwd=str(tmp_path))
    assert m3 is not m1  # 关掉后重建
    proc.shutdown_manager()


def test_default_cwd_is_plugin_runtime():
    """★ 默认工作目录在插件 runtime/ 内 —— 避免 pi 读到 Alongside 之外的 AGENTS.md。"""
    proc = _load("process")
    proc.shutdown_manager()
    m = proc.get_manager()
    assert str(PLUGIN_DIR / "runtime") in m._cwd
    proc.shutdown_manager()


# ── 真机（-m live）──────────────────────────────────────────────

@pytest.mark.live
def test_live_prompt_roundtrip(tmp_path):
    """真起 pi 子进程跑一轮对话（需已装 pi + alongside 路由可达）。"""
    rpc = _load("rpc")
    binary = rpc.find_pi_binary()
    if not binary:
        pytest.skip("未安装 pi")
    proc = _load("process")
    m = proc.PiProcessManager(cwd=str(tmp_path), binary=binary)
    try:
        if not m.ensure_started():
            pytest.skip(f"pi 起不来：{m.status().get('last_error')}")
        text = []
        for ev in m.prompt("1+1等于几？只回数字"):
            if ev.text_delta:
                text.append(ev.text_delta)
            if ev.is_settled:
                break
        assert "2" in "".join(text)
    finally:
        m.stop()
