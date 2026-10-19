"""★ Pi 插件「设置真的生效」守护测试（2026-10-10）。

背景（实打实的风险点，主人点名要修）：
    此前 `provider` / `model` **硬编码**在 process.py / sessions.py
    （`DEFAULT_PROVIDER = "alongside"` / `model = "alongside:high"`），
    而 settings.schema.json 里明明声明了 provider / model / pi_binary /
    max_parallel_sessions —— **配了完全不生效**。
    后果：换一个网关（模型 id 不同）就必须改代码；网关返回
    `503 无可用的模型`，插件只报 `reply: ""`，排查要一路挖到源码。

本测试钉住三条不变量（改坏了就会红）：
    1. **默认值 = schema default** —— 不配任何设置时，运行时参数
       与 settings.schema.json 的 default 一致（防两处悄悄漂移）。
    2. **设置可覆盖** —— 写入 plugin_setting 后，运行时参数随之为变
       （含 `--model` 的 `:<thinking>` 后缀拼接）。
    3. **反硬编码** —— `_pool()` / `_manager()` **不得**把 provider/model
       写死在调用里；必须来自 `_runtime_options()`。

不连库：直接 monkeypatch 设置缓存，纯逻辑验证。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_API_DIR = Path(__file__).resolve().parents[3] / "plugins" / "pi-agent" / "api"
_ROUTER = _API_DIR / "router.py"
_SCHEMA = _API_DIR / "settings.schema.json"


def _load_router():
    """按内核的方式（文件路径）加载插件路由模块。

    第三方插件不属于任何包，用 `pi_agent_router_under_test` 这个独立名字，
    避免与内核已加载的同名模块互相污染。
    """
    spec = importlib.util.spec_from_file_location("pi_agent_router_under_test", _ROUTER)
    assert spec and spec.loader, "无法加载 plugins/pi-agent/api/router.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def router():
    return _load_router()


@pytest.fixture()
def schema_defaults() -> dict:
    return json.loads(_SCHEMA.read_text(encoding="utf-8"))["properties"]


def _set(router, monkeypatch, settings: dict) -> None:
    """注入设置（绕过 DB）并刷新 TTL 缓存时间戳，避免读到上一轮残值。"""
    import time

    monkeypatch.setattr(router, "_SETTINGS_CACHE", settings, raising=False)
    monkeypatch.setattr(router, "_SETTINGS_CACHE_AT", time.monotonic(), raising=False)


# ── 不变量 1：默认值必须等于 schema default ───────────────────────────


def test_defaults_match_schema(router, monkeypatch, schema_defaults):
    """不配任何设置时，运行时参数 = settings.schema.json 的 default。"""
    _set(router, monkeypatch, {})
    o = router._runtime_options()

    assert o["provider"] == schema_defaults["provider"]["default"], (
        "provider 默认值与 settings.schema.json 漂移了 —— 两处必须一致"
    )
    # model：schema 存的是裸 id，运行时带 :<thinking> 后缀
    assert o["model"].split(":")[0] == schema_defaults["model"]["default"]
    assert o["model"].endswith(":" + schema_defaults["thinking_level"]["default"])
    assert o["max_sessions"] == schema_defaults["max_parallel_sessions"]["default"]


# ── 不变量 2：设置必须能覆盖默认值 ────────────────────────────────────


def test_settings_override_defaults(router, monkeypatch):
    """写入 plugin_setting 后，运行时参数随之改变。"""
    _set(router, monkeypatch, {
        "provider": "lifedemo",
        "model": "lifedemo",
        "thinking_level": "low",
        "max_parallel_sessions": 8,
        "pi_binary": "/opt/custom/pi",
    })
    o = router._runtime_options()

    assert o["provider"] == "lifedemo"
    assert o["model"] == "lifedemo:low", "model 应拼接 :<thinking_level>"
    assert o["max_sessions"] == 8
    assert o["binary"] == "/opt/custom/pi"


def test_model_with_explicit_thinking_not_double_suffixed(router, monkeypatch):
    """model 里已带 :<thinking> 时不得重复拼接。"""
    _set(router, monkeypatch, {"model": "lifedemo:medium", "thinking_level": "high"})
    assert router._runtime_options()["model"] == "lifedemo:medium"


def test_bad_max_sessions_falls_back(router, monkeypatch):
    """脏值不得让对话挂掉 —— 回退到默认 4。"""
    _set(router, monkeypatch, {"max_parallel_sessions": "不是数字"})
    assert router._runtime_options()["max_sessions"] == 4


# ── 不变量 3：反硬编码（本次修复的核心）──────────────────────────────


def test_pool_and_manager_take_provider_model_from_settings(router, monkeypatch):
    """★ 核心反回归：_pool()/_manager() 传给 get_pool/get_manager 的
    provider/model **必须来自设置**，不能硬编码。

    做法：注入一组"绝不会是默认值"的参数，捕获实际传给底层单例的参数。
    """
    caught: list[dict] = []

    class _FakeSessions:
        def get_pool(self, **kw):
            caught.append(kw)
            return object()

        def shutdown_pool(self):
            pass

    class _FakeProc:
        def get_manager(self, **kw):
            caught.append(kw)
            return object()

        def shutdown_manager(self):
            pass

    monkeypatch.setattr(router, "_load_sibling", lambda name: _FakeSessions() if name == "sessions" else _FakeProc())
    monkeypatch.setattr(router, "_POOL_SIG", None, raising=False)
    monkeypatch.setattr(router, "_MGR_SIG", None, raising=False)
    _set(router, monkeypatch, {"provider": "sentinel-provider", "model": "sentinel-model"})

    router._pool()
    router._manager()

    assert caught, "未捕获到底层调用"
    for kw in caught:
        assert kw.get("provider") == "sentinel-provider", (
            f"provider 没有从设置来（疑似被硬编码）：{kw.get('provider')!r}"
        )
        assert str(kw.get("model", "")).split(":")[0] == "sentinel-model", (
            f"model 没有从设置来（疑似被硬编码）：{kw.get('model')!r}"
        )


def test_router_source_has_no_hardcoded_provider_call():
    """★ 源码级反回归：不许出现 `get_pool(provider=...` / `get_manager(model=...)`
    这类把 provider/model 写死在调用里的写法。

    这条最直白地锁住本次修复的意图 —— 只要有人把硬编码写回来就红。
    """
    src = _ROUTER.read_text(encoding="utf-8")
    for bad in ('provider="alongside"', "provider='alongside'",
                'model="alongside:high"', "model='alongside:high'"):
        assert bad not in src, (
            f"router.py 又出现硬编码 {bad} —— provider/model 必须走 _runtime_options()"
        )


def test_schema_has_provider_field(schema_defaults):
    """settings.schema.json 必须暴露 provider（此前只有 model，无法换 provider）。"""
    assert "provider" in schema_defaults, "schema 缺 provider 字段"
    assert schema_defaults["provider"]["type"] == "string"
