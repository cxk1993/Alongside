"""声明式激活器测试（卡档 v1.0 · 属主体的判据 2-5、8）。

覆盖：
1. 缺省 activates_on（存量 17 插件形态）→ 启动即激活，向后兼容零漂移；
2. 显式 startup:always → 启动即激活；
3. event:<topic> → 启动只注册不激活（404）；命中事件后激活（路由可用）；
   未命中 topic / 监听未挂时保持未激活（非 always 真例的反面）；
4. 非法 activates_on 条目 → manifest 校验启动即失败（fail-fast 不变）；
5. clear_pending：disable 联动后事件不再把禁用插件挂回（幂等）。

探针模块造在真实 modules/ 下（builtin router 走包导入，同 test_kernel；
名称 actprobe 已登记 conftest.LEFTOVER_PROBE_MODULES 防残留污染）。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.app import create_app
from core.errors import ManifestError
from core.events import event_bus

MODULES_DIR = Path(__file__).resolve().parents[1] / "modules"
PROBE_ID = "actprobe"
PROBE_BASE = f"/api/v1/{PROBE_ID}"

GOOD_ROUTER = '''
from fastapi import APIRouter

router = APIRouter()


@router.get("/ping")
def ping() -> dict[str, str]:
    return {"pong": "probe"}
'''


def _manifest(**over: object) -> str:
    base: dict[str, Any] = {
        "id": PROBE_ID,
        "name": "激活探针模块",
        "version": "0.1.0",
        "kind": "builtin",
        "minKernel": "0.1.0",
        "kernelApi": "^1",
        "icon": "probe",
        "description": "测试用临时模块",
        "author": "test",
        "window": {"w": 480, "h": 320},
        "entry": "@apps/probe",
        "api": {
            "base": PROBE_BASE,
            "openapi": f"{PROBE_BASE}/openapi.json",
            "health": f"{PROBE_BASE}/health",
        },
        "provides": [],
        "requires": [],
        "slots": [],
        "emits": [],
        "consumes": [],
        "permissions": [],
        "migrations": None,
        "settingsSchema": None,
    }
    base.update(over)
    return json.dumps(base, ensure_ascii=False)


@pytest.fixture
def make_probe() -> Iterator[Callable[..., None]]:
    """在真实 modules 目录下按给定 manifest 覆盖造探针，测试后必删。"""

    def _make(**over: object) -> None:
        d = MODULES_DIR / PROBE_ID
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        (d / "__init__.py").write_text("", encoding="utf-8")
        (d / "manifest.json").write_text(_manifest(**over), encoding="utf-8")
        (d / "router.py").write_text(GOOD_ROUTER, encoding="utf-8")

    try:
        yield _make
    finally:
        shutil.rmtree(MODULES_DIR / PROBE_ID, ignore_errors=True)


# ─────────────── 1. 缺省 → 启动即激活（向后兼容零漂移） ───────────────
def test_default_manifest_activates_on_startup(make_probe: Callable[..., None]) -> None:
    make_probe()  # 不带 activates_on 键 —— 存量插件的真实形态
    client = TestClient(create_app())

    body = client.get("/readyz").json()
    assert body["ok"] is True
    assert PROBE_ID in body["modules"]  # 已挂载
    assert PROBE_ID in body["activated"]  # census：已激活
    assert PROBE_ID in body["registered"]  # census：已注册
    assert body["activation_errors"] == {}  # 无激活失败目击
    # 兼容面：auth（无 activates_on 的存量 core 插件）也必须已激活
    assert "auth" in body["activated"]
    # 路由真实可用
    assert client.get(f"{PROBE_BASE}/ping").json() == {"pong": "probe"}


# ─────────────── 2. 显式 startup:always → 启动即激活 ───────────────
def test_explicit_startup_always_activates(make_probe: Callable[..., None]) -> None:
    make_probe(activates_on=["startup:always"])
    client = TestClient(create_app())

    body = client.get("/readyz").json()
    assert PROBE_ID in body["activated"]
    assert client.get(f"{PROBE_BASE}/ping").status_code == 200


# ─────────────── 3. event:<topic> → 懒加载全链路 ───────────────
def test_event_trigger_lazy_activation(make_probe: Callable[..., None]) -> None:
    make_probe(activates_on=["event:demo.ping"])
    app = create_app()
    client = TestClient(app)

    # 启动后：已注册、未激活、路由不存在
    assert PROBE_ID in app.state.modules
    body = client.get("/readyz").json()
    assert PROBE_ID in body["registered"]
    assert PROBE_ID not in body["activated"]
    assert PROBE_ID not in body["modules"]
    assert client.get(f"{PROBE_BASE}/ping").status_code == 404
    # 未激活的插件不该出现在 pending 之外的状态里
    assert app.state.activator.is_registered_pending(PROBE_ID)

    # 模拟生产 lifespan 接线（测试不进 lifespan，手动挂摘）
    event_bus.add_kernel_hook(app.state.activator.on_event)
    try:
        # 未命中 topic：保持未激活
        event_bus.publish("demo.other", {"n": 1})
        assert client.get(f"{PROBE_BASE}/ping").status_code == 404
        # 命中：延后激活，路由立即可用
        event_bus.publish("demo.ping", {"n": 2})
        assert client.get(f"{PROBE_BASE}/ping").json() == {"pong": "probe"}
        body = client.get("/readyz").json()
        assert PROBE_ID in body["activated"]
        assert PROBE_ID in body["modules"]
        # 激活后不再是 pending
        assert not app.state.activator.is_registered_pending(PROBE_ID)
    finally:
        event_bus.remove_kernel_hook(app.state.activator.on_event)


# ─────────────── 4. 监听未挂时事件发布零影响 ───────────────
def test_publish_without_listener_is_inert(make_probe: Callable[..., None]) -> None:
    make_probe(activates_on=["event:demo.ping"])
    client = TestClient(create_app())

    # 测试进程里没人挂 listener（未进 lifespan）：广播不应激活任何 pending
    event_bus.publish("demo.ping", {"n": 0})
    assert client.get(f"{PROBE_BASE}/ping").status_code == 404


# ─────────────── 5. disable 联动 clear_pending ───────────────
def test_disable_clears_pending_blocks_event_activation(
    make_probe: Callable[..., None],
) -> None:
    make_probe(activates_on=["event:demo.ping"])
    app = create_app()
    client = TestClient(app)
    activator = app.state.activator

    # 端点联动（disable_plugin）会调用的清理动作
    assert activator.clear_pending(PROBE_ID) is True
    # 幂等：再清无登记可移除
    assert activator.clear_pending(PROBE_ID) is False

    event_bus.add_kernel_hook(activator.on_event)
    try:
        event_bus.publish("demo.ping", {"n": 3})
        # pending 已清：事件命中也不再激活（disable 语义不被击穿）
        assert client.get(f"{PROBE_BASE}/ping").status_code == 404
    finally:
        event_bus.remove_kernel_hook(activator.on_event)


# ─────────────── 6. 非法条目 → fail-fast ───────────────
def test_invalid_activates_on_fails_fast(make_probe: Callable[..., None]) -> None:
    make_probe(activates_on=["http://nope"])  # 完全不合法的形态
    with pytest.raises(ManifestError):
        create_app()

    make_probe(activates_on=["event:demo"])  # topic 必须至少两段
    with pytest.raises(ManifestError):
        create_app()


# ─────────────── 7. container 型 → 激活成功且不尝试挂路由（路径 a） ───────────────
def test_container_module_activates_without_router(make_probe: Callable[..., None]) -> None:
    """容器型模块（纯前端编排）没有 router.py，激活应短路成功而非 ManifestError。"""
    make_probe(kind="container", api=None, tabs=[{"key": "a", "label": "甲页"}])
    (MODULES_DIR / PROBE_ID / "router.py").unlink()  # container 无后端路由
    client = TestClient(create_app())

    body = client.get("/readyz").json()
    assert PROBE_ID in body["activated"]  # 已激活（container 短路分支）
    assert PROBE_ID not in body["modules"]  # 无路由可挂，不在挂载清单
    assert body["activation_errors"] == {}  # 无激活失败目击
