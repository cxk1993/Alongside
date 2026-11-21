"""assets 资产层测试（bidiu 2026-11-22）。

覆盖（契约 contracts/asset.schema.json）：
  · 创建：id 前缀 as_、默认 tier=private/exportable=true、provenance.at 服务端补
  · provenance 缺 agent → 422；非法 type → 422
  · append-only + 显式 supersede：新条 supersedes=[old] / 旧条 superseded + superseded_by=new
  · supersedes 指向不存在 → 422；重复取代 → 422
  · 列表过滤（status/type/tier/q）
  · 硬删 + 反向指针清理
  · SSE 事件三件套真发（判据 5）

数据库隔离：临时库 ./data/tmp_assets.db。
"""
from __future__ import annotations

import os

os.environ["DB_PATH"] = "./data/tmp_assets.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402

AUTH = {"Authorization": f"Bearer {create_access_token('admin')}"}


@pytest.fixture(scope="module")
def client():
    init_engine()
    engine = get_engine()
    from modules.assets import models as _assets_models  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    c = TestClient(create_app())
    yield c
    engine.dispose()


def _asset(**over):
    body = {
        "type": "note",
        "title": "测试资产",
        "body": "正文内容",
        "provenance": {"agent": "human"},
        "tags": ["t1"],
    }
    body.update(over)
    return body


def test_create_asset_defaults(client):
    r = client.post("/api/v1/assets", json=_asset(title="首条资产"), headers=AUTH)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["id"].startswith("as_") and len(d["id"]) > 9
    assert d["status"] == "active"
    assert d["tier"] == "private"      # 契约默认 private
    assert d["scope"] == "private"
    assert d["exportable"] is True     # 契约默认 true
    assert d["provenance"]["agent"] == "human"
    assert d["provenance"]["at"]       # 服务端补时间
    assert d["schema_version"] == 1
    assert d["supersedes"] == [] and d["superseded_by"] is None


def test_create_requires_provenance_agent(client):
    r = client.post("/api/v1/assets", json=_asset(provenance={}), headers=AUTH)
    assert r.status_code == 422, r.text


def test_create_rejects_bad_type(client):
    r = client.post("/api/v1/assets", json=_asset(type="rant"), headers=AUTH)
    assert r.status_code == 422, r.text


def test_supersede_links_both_directions(client):
    old = client.post("/api/v1/assets", json=_asset(title="旧结论"), headers=AUTH).json()
    r = client.post(
        "/api/v1/assets",
        json=_asset(title="新结论", supersedes=[old["id"]]),
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    new = r.json()

    # 新条：supersedes 指向旧条
    assert new["supersedes"] == [old["id"]]
    # 旧条：status=superseded + superseded_by 指回新条（契约 allOf）
    old2 = client.get(f"/api/v1/assets/{old['id']}", headers=AUTH).json()
    assert old2["status"] == "superseded"
    assert old2["superseded_by"] == new["id"]
    # 旧条内容未被改写（append-only）
    assert old2["title"] == "旧结论"


def test_supersede_missing_target_rejected(client):
    r = client.post(
        "/api/v1/assets",
        json=_asset(title="x", supersedes=["as_notexist000"]),
        headers=AUTH,
    )
    assert r.status_code == 422, r.text


def test_double_supersede_rejected(client):
    old = client.post("/api/v1/assets", json=_asset(title="将被两次取代"), headers=AUTH).json()
    assert client.post(
        "/api/v1/assets", json=_asset(title="取代1", supersedes=[old["id"]]), headers=AUTH
    ).status_code == 201
    r = client.post(
        "/api/v1/assets", json=_asset(title="取代2", supersedes=[old["id"]]), headers=AUTH
    )
    assert r.status_code == 422, r.text


def test_list_filters(client):
    client.post("/api/v1/assets", json=_asset(title="过滤 A", type="skill"), headers=AUTH)
    r = client.get("/api/v1/assets?type=skill", headers=AUTH)
    assert r.status_code == 200
    assert all(x["type"] == "skill" for x in r.json())
    r2 = client.get("/api/v1/assets?status=draft", headers=AUTH)
    assert all(x["status"] == "draft" for x in r2.json())
    r3 = client.get("/api/v1/assets?q=过滤", headers=AUTH)
    assert any("过滤" in x["title"] for x in r3.json())


def test_hard_delete_unlinks_children(client):
    old = client.post("/api/v1/assets", json=_asset(title="将被删"), headers=AUTH).json()
    new = client.post(
        "/api/v1/assets", json=_asset(title="指向它的新条", supersedes=[old["id"]]), headers=AUTH
    ).json()
    r = client.delete(f"/api/v1/assets/{old['id']}", headers=AUTH)
    assert r.status_code == 204, r.text
    assert client.get(f"/api/v1/assets/{old['id']}", headers=AUTH).status_code == 404
    # 反向指针清理（不留悬挂引用）
    new2 = client.get(f"/api/v1/assets/{new['id']}", headers=AUTH).json()
    assert new2["superseded_by"] is None


def test_events_published(client, monkeypatch):
    from modules.assets import service as svc

    seen: list[tuple] = []
    monkeypatch.setattr(
        svc.event_bus, "publish", lambda topic, payload=None, **kw: seen.append((topic, payload, kw))
    )

    old = client.post("/api/v1/assets", json=_asset(title="事件旧条"), headers=AUTH).json()
    client.post(
        "/api/v1/assets", json=_asset(title="事件新条", supersedes=[old["id"]]), headers=AUTH
    )
    client.delete(f"/api/v1/assets/{old['id']}", headers=AUTH)

    topics = [t for t, _, _ in seen]
    assert "assets.asset.created" in topics
    assert "assets.asset.superseded" in topics
    assert "assets.asset.deleted" in topics
    prop = [(t, p, kw) for t, p, kw in seen if t.startswith("assets.")]
    assert all(kw.get("source") == "assets" for _, _, kw in prop)

def test_concurrent_supersede_same_old_asset(client):
    """两并发 create 同时 supersede 同一旧条：一个 201 一个 422（写锁防指针覆盖）。"""
    import threading

    old = client.post("/api/v1/assets", json=_asset(title="并发取代旧条"), headers=AUTH).json()
    results: list[int] = []
    barrier = threading.Barrier(2)

    def call(i: int):
        barrier.wait()
        r = client.post(
            "/api/v1/assets",
            json=_asset(title=f"并发取代-{i}", supersedes=[old["id"]]),
            headers=AUTH,
        )
        results.append(r.status_code)

    ts = [threading.Thread(target=call, args=(i,)) for i in (1, 2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert sorted(results) == [201, 422], results
    old2 = client.get(f"/api/v1/assets/{old['id']}", headers=AUTH).json()
    assert old2["status"] == "superseded"
    assert old2["superseded_by"], "旧条必须指向唯一胜者（不被覆盖成空）"
    winner = client.get(f"/api/v1/assets/{old2['superseded_by']}", headers=AUTH).json()
    assert winner["supersedes"] == [old["id"]]
