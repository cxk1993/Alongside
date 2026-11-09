"""T21 健康插件后端测试。

隔离库 ./data/tmp_t21.db；事件用总线历史捕获；不 import todo。
"""
from __future__ import annotations
from core.db_scope import declare_scope  # 2026-11-09 作用域翻转：显式声明

import os

os.environ["DB_PATH"] = "./data/tmp_t21.db"

from datetime import UTC, date, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.events import event_bus  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.health.models import HealthRecord  # noqa: E402
from modules.health.service import care_idempotency_key  # noqa: E402


@pytest.fixture(scope="module")
def client():
    init_engine()
    engine = get_engine()
    HealthRecord.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    engine = get_engine()
    with declare_scope(__import__("sqlmodel").Session(engine)) as s:
        s.exec(text("DELETE FROM health_record"))
        s.commit()
    yield


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def _clear_bus():
    event_bus._history.clear()


def _topics() -> list[str]:
    return [e["topic"] for e in event_bus._history]


def _payloads(topic: str) -> list[dict]:
    return [e["payload"] for e in event_bus._history if e["topic"] == topic]


def test_health_and_manifest(client, auth):
    r = client.get("/api/v1/health/health", headers=auth)
    assert r.status_code == 200 and r.json()["ok"] is True
    m = client.get("/api/v1/health/manifest", headers=auth)
    assert m.status_code == 200
    assert "health.care.requested" in m.json()["emits"]


def test_crud_roundtrip(client, auth):
    now = datetime.now(UTC).isoformat()
    body = {
        "kind": "symptom",
        "title": "头痛",
        "occurred_at": now,
        "severity": 3,
        "note": JSON_NOTE,
        "followup_needed": False,
    }
    r = client.post("/api/v1/health/records", json=body, headers=auth)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["kind"] == "symptom"

    r2 = client.get(f"/api/v1/health/records/{rid}", headers=auth)
    assert r2.status_code == 200
    assert r2.json()["title"] == "头痛"

    r3 = client.patch(
        f"/api/v1/health/records/{rid}",
        json={"title": "偏头痛", "severity": 4},
        headers=auth,
    )
    assert r3.status_code == 200
    assert r3.json()["title"] == "偏头痛"

    r4 = client.get("/api/v1/health/records", headers=auth)
    assert r4.status_code == 200
    assert len(r4.json()) == 1

    r5 = client.delete(f"/api/v1/health/records/{rid}", headers=auth)
    assert r5.status_code == 204
    r6 = client.get(f"/api/v1/health/records/{rid}", headers=auth)
    assert r6.status_code == 404


JSON_NOTE = '{"部位":"头"}'


def test_filter_kind(client, auth):
    now = datetime.now(UTC).isoformat()
    client.post(
        "/api/v1/health/records",
        json={"kind": "medication", "title": "布洛芬", "occurred_at": now},
        headers=auth,
    )
    client.post(
        "/api/v1/health/records",
        json={"kind": "lab", "title": "血常规", "occurred_at": now},
        headers=auth,
    )
    r = client.get("/api/v1/health/records?kind=medication", headers=auth)
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["kind"] == "medication"


def test_followup_publishes_event(client, auth):
    _clear_bus()
    now = datetime.now(UTC).isoformat()
    due = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
    body = {
        "kind": "appointment",
        "title": "复诊心内科",
        "occurred_at": now,
        "followup_needed": True,
        "followup_due": due,
    }
    r = client.post("/api/v1/health/records", json=body, headers=auth)
    assert r.status_code == 201
    rid = r.json()["id"]
    assert "health.care.requested" in _topics()
    pays = _payloads("health.care.requested")
    assert len(pays) == 1
    assert pays[0]["record_id"] == rid
    assert pays[0]["source"] == "health"
    assert pays[0]["suggest_due"] == due
    assert pays[0]["idempotency_key"] == care_idempotency_key(rid, date.fromisoformat(due))


def test_request_followup_manual(client, auth):
    _clear_bus()
    now = datetime.now(UTC).isoformat()
    r = client.post(
        "/api/v1/health/records",
        json={"kind": "symptom", "title": "低烧", "occurred_at": now},
        headers=auth,
    )
    rid = r.json()["id"]
    assert "health.care.requested" not in _topics()
    r2 = client.post(f"/api/v1/health/records/{rid}/request-followup", headers=auth)
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    assert r2.json()["idempotency_key"].startswith(f"health-followup:{rid}:")


def test_naive_datetime_rejected(client, auth):
    r = client.post(
        "/api/v1/health/records",
        json={"kind": "symptom", "title": "x", "occurred_at": "2026-09-20T08:00:00"},
        headers=auth,
    )
    assert r.status_code in (400, 422)


def test_boundary_requires_auth(client):
    r = client.get("/api/v1/health/records")
    assert r.status_code in (401, 403)
