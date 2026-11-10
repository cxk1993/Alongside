"""事件订阅的生命周期回归（P0）：停用/卸载必须摘订阅，启用必须装回来。

★ 为什么这组测试存在：
  改造前 `unsubscribe_declared` 在全仓**零调用方**。实测（真实 EventBus + 真实
  manager）：`disable` 之后 handler 照收事件；`uninstall` 之后目录已被 rmtree，
  handler 仍被调用，且 `del p; gc.collect()` 后 weakref 仍存活（总线强引用 ⇒
  插件对象永远回收不掉）。用户能感知的形态：点「停用推送」后，日历提醒一来
  照样写库发推送 —— 因为 handler 走模块函数，不经过路由，摘路由拦不住。

★ 这组测试守三条不变量：
  ① 停用后：该插件在总线上**没有**任何声明式订阅，也不再收到事件；
  ② 启用后：订阅**回来**，且一条事件只投递一次（不是两次 —— 见 P0-2 累积）；
  ③ 卸载后：订阅被清掉，死插件的 handler 不会再被调用。

判据用「真实文件里的计数器」而不是 mock：handler 每被调用一次就往文件里写一行，
测试从**文件**读次数 —— 这是跨进程/跨模块对象都成立的证据。
"""
from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from core.app import create_app
from core.events import event_bus
from core.security import create_access_token
from db.engine import init_engine

API_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = API_ROOT.parent.parent / "plugins"  # 项目根/plugins

PID = f"demo3p-sub-{uuid.uuid4().hex[:6]}"
TOPIC = f"{PID}.ping"


def _write_plugin(root: Path, hit_file: Path) -> None:
    (root / "api" / "migrations").mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "id": root.name,
                "name": "订阅生命周期演示插件",
                "version": "0.1.0",
                "kind": "third-party",
                "minKernel": "0.1.0",
                "kernelApi": "^1",
                "icon": "clock",
                "description": "只用于验证停用/启用/卸载与事件订阅的绑定",
                "author": "test",
                "window": {"w": 480, "h": 320},
                "entry": "",
                "api": {
                    "base": f"/api/v1/{root.name}",
                    "openapi": f"/api/v1/{root.name}/openapi.json",
                    "health": f"/api/v1/{root.name}/health",
                },
                "provides": [],
                "requires": [],
                "slots": [],
                "emits": [],
                "consumes": [TOPIC],
                "permissions": ["db:own"],
                "migrations": "api/migrations",
                # 本插件提供 api/lifecycle.py（顶层声明式订阅）⇒ 必须声明 hooks
                "lifecycle": "hooks",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "api" / "router.py").write_text(
        "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n"
        "@router.get('/health')\ndef health():\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    # ★ 插件作者最自然的写法：在 lifecycle 模块顶层按声明订阅。
    (root / "api" / "lifecycle.py").write_text(
        f'''
import json
from pathlib import Path

from core.events import event_bus

_HIT = Path({str(hit_file)!r})
_MANIFEST = json.loads((Path(__file__).resolve().parent.parent / "manifest.json").read_text(encoding="utf-8"))


def on_ping(event):
    with _HIT.open("a", encoding="utf-8") as f:
        f.write("hit\\n")


event_bus.subscribe_declared(_MANIFEST, on_ping, topics=["{TOPIC}"])
''',
        encoding="utf-8",
    )


def _hits(hit_file: Path) -> int:
    return len(hit_file.read_text(encoding="utf-8").splitlines()) if hit_file.exists() else 0


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    import db.engine as _db_engine_mod

    plugin_dir = PLUGINS_DIR / PID
    hit_file = tmp_path / "hits.log"
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir, ignore_errors=True)
    _write_plugin(plugin_dir, hit_file)

    _db_engine_mod._engine = None
    engine = init_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    try:
        yield TestClient(create_app()), hit_file
    finally:
        shutil.rmtree(plugin_dir, ignore_errors=True)
        event_bus.unsubscribe_declared(PID)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def test_disable_removes_subscription_and_enable_restores_it_one_shot(
    client: tuple[TestClient, Path],
) -> None:
    http, hit_file = client

    r = http.post("/api/v1/plugins/install", json={"id": PID}, headers=_auth())
    assert r.status_code == 200, r.text

    event_bus.publish_kernel_event(TOPIC, {})
    base = _hits(hit_file)
    assert base >= 1, "安装后应当收到事件（前置判据：订阅确实装上了）"

    # ── ① 停用：订阅必须被摘掉 ────────────────────────────────
    r = http.post(f"/api/v1/plugins/{PID}/disable", headers=_auth())
    assert r.status_code == 200, r.text
    assert event_bus.declared_topics(PID) == frozenset(), "停用后总线上不该再有该插件的订阅"

    event_bus.publish_kernel_event(TOPIC, {})
    assert _hits(hit_file) == base, "停用后 handler 仍在收事件 —— 事件联动绕过了路由层"

    # ── ② 启用：订阅必须回来，且只投递一次 ────────────────────
    r = http.post(f"/api/v1/plugins/{PID}/enable", headers=_auth())
    assert r.status_code == 200, r.text
    assert event_bus.declared_topics(PID) == frozenset({TOPIC}), "启用后订阅应当恢复"

    before = _hits(hit_file)
    event_bus.publish_kernel_event(TOPIC, {})
    assert _hits(hit_file) - before == 1, (
        "一条事件只能投递一次；>1 说明 enable 又把同一份声明注册了一遍（P0-2 累积）"
    )

    # ── ③ 卸载：订阅与缓存都要清 ──────────────────────────────
    r = http.post(f"/api/v1/plugins/{PID}/uninstall", headers=_auth())
    assert r.status_code == 200, r.text
    assert PID not in event_bus._declared, "卸载后死插件的 handler 仍被总线强引用"

    before = _hits(hit_file)
    event_bus.publish_kernel_event(TOPIC, {})
    assert _hits(hit_file) == before, "插件目录已被删除，handler 却还在被调用"


def test_disable_push_stops_event_links(client: tuple[TestClient, Path]) -> None:
    """push 是 builtin，用户可以在设置页停用 —— 停用后三路联动必须一起停。"""
    from modules.push import push_link

    http, _hit = client
    push_link.install_push_link()  # 幂等：正常由 router import 装载
    assert event_bus.declared_topics("push") == frozenset(
        {push_link.EVENT_REMINDER, push_link.EVENT_TODO_DUE, push_link.EVENT_COURSE_DUE}
    )

    r = http.post("/api/v1/plugins/push/disable", headers=_auth())
    assert r.status_code == 200, r.text
    assert event_bus.declared_topics("push") == frozenset(), (
        "停用推送后三路 handler 仍在总线上 —— 日历提醒一来照样写库发推送"
    )

    r = http.post("/api/v1/plugins/push/enable", headers=_auth())
    assert r.status_code == 200, r.text
    assert event_bus.declared_topics("push") == frozenset(
        {push_link.EVENT_REMINDER, push_link.EVENT_TODO_DUE, push_link.EVENT_COURSE_DUE}
    ), "启用推送后三路联动应当装回来"
