"""· 坞模块健康四态测试。

纯函数四态穷举 + dock 组装容错 + API 层（与内核注册表对表自洽 + 鉴权）。
隔离库走 conftest 模块级隔离；不造探针模块、不碰内核路由。
"""
from __future__ import annotations
from core.db_scope import declare_scope  # 2026-11-09 作用域翻转：显式声明

import os

os.environ["DB_PATH"] = "./data/tmp_t_o1_status.db"
os.environ["HEALTH_RECONCILE_ENABLED"] = "false"

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, create_engine  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.base import SQLModel  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.health.models import HealthRecord  # noqa: E402
from modules.health.module_status import (  # noqa: E402
    classify_module_status,
    dock_module_status,
)

# ── 纯函数四态穷举 ──


def test_classify_healthy() -> None:
    assert (
        classify_module_status(registered=True, activated=True, activation_error=None)
        == "healthy"
    )


def test_classify_degraded() -> None:
    assert (
        classify_module_status(
            registered=True, activated=False, activation_error="RuntimeError: boom"
        )
        == "degraded"
    )


def test_classify_disabled() -> None:
    assert (
        classify_module_status(registered=True, activated=False, activation_error=None)
        == "disabled"
    )


def test_classify_unknown() -> None:
    assert (
        classify_module_status(registered=False, activated=True, activation_error=None)
        == "unknown"
    )


def test_classify_degraded_beats_disabled() -> None:
    """激活报错优先于未激活：错误目击不能被 disabled 掩盖。"""
    assert (
        classify_module_status(
            registered=True, activated=False, activation_error="RuntimeError: boom"
        )
        == "degraded"
    )


# ── dock 组装 ──


def test_dock_tolerates_missing_registry_and_activator() -> None:
    """state 缺 registry/activator 时不炸：注册表照出（disabled），reconcile 照并表。"""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with declare_scope(Session(engine)) as session:
        app = SimpleNamespace(
            state=SimpleNamespace(
                modules={
                    "solo": {"id": "solo", "name": "Solo", "kind": "builtin", "api": {}}
                },
                registry=None,
                activator=None,
            )
        )
        out = dock_module_status(app, session)
    engine.dispose()
    assert out["ok"] is True
    assert out["count"] == 1
    assert out["modules"][0]["status"] == "disabled"
    assert out["summary"]["disabled"] == 1
    assert out["reconcile"]["desired_count"] == 0
    assert out["reconcile"]["scheduler_enabled"] is False
    # O1 契约 v2：无 optionalDependencies → dep 全 ok
    assert out["modules"][0]["dep_state"] == "ok"
    assert out["modules"][0]["reason_code"] is None
    assert out["modules"][0]["missing"] == []
    assert out["dep_degraded_count"] == 0


def test_dock_dep_state_soft_missing_degraded() -> None:
    """软依赖缺失 → dep_state=degraded + reason_code（消费半边）。"""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with declare_scope(Session(engine)) as session:
        app = SimpleNamespace(
            state=SimpleNamespace(
                modules={
                    "alpha": {
                        "id": "alpha",
                        "name": "Alpha",
                        "kind": "builtin",
                        "api": {},
                        "provides": ["alpha.read"],
                        "optionalDependencies": ["zeta.read", "alpha.read"],
                    }
                },
                registry=None,
                activator=None,
            )
        )
        out = dock_module_status(app, session)
    engine.dispose()
    row = out["modules"][0]
    assert row["dep_state"] == "degraded"
    assert row["reason_code"] == "optional_dependency_missing:zeta.read"
    assert row["missing"] == ["zeta.read"]
    assert out["dep_degraded_count"] == 1


# ── API 层 ──


@pytest.fixture(scope="module")
def client():
    init_engine()
    engine = get_engine()
    HealthRecord.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def test_dock_modules_self_consistent(client: TestClient, auth: dict) -> None:
    r = client.get("/api/v1/health/modules", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == len(body["modules"])
    assert sum(body["summary"].values()) == body["count"]
    # 与内核注册表对表：纯附加端点，id 集合与 census 语义零漂移
    kernel = client.get("/api/v1/modules").json()
    assert kernel["count"] == body["count"]
    assert {m["id"] for m in body["modules"]} == {m["id"] for m in kernel["modules"]}
    # 内置模块缺省启动即激活 → 全 healthy；health 模块行抽验
    assert body["summary"]["healthy"] == body["count"]
    assert body["summary"]["degraded"] == 0
    health_row = next(m for m in body["modules"] if m["id"] == "health")
    assert health_row["status"] == "healthy"
    assert health_row["activated"] is True
    assert health_row["api_health_declared"] is True
    assert health_row["detail"] is None


def test_dock_modules_reconcile_digest(client: TestClient, auth: dict) -> None:
    body = client.get("/api/v1/health/modules", headers=auth).json()
    assert body["reconcile"]["event_topic"] == "health.care.requested"
    assert body["reconcile"]["scheduler_enabled"] is False
    assert isinstance(body["reconcile"]["desired_count"], int)
    assert body["reconcile"]["desired_count"] >= 0


def test_dock_modules_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/health/modules").status_code == 401
