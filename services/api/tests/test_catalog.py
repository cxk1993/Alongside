"""T20 能力目录后端测试（只跑这一个文件，不影响别的卡）。

★ 状态码约定：校验类 422、找不到 404、未鉴权 401、id 冲突 409（无）。
数据库隔离：用临时库 ./data/tmp_t20.db，绝不碰主库 alongside.db。
表由模型直接建（create_app 不自动跑迁移）。
★ 不对 T19 模块 import / join —— web_entry 源经 get_plugin_client 跨插件调用。
"""

from __future__ import annotations

import os

# ★ 必须在 import 任何内核/模块之前设置临时库，init_engine 只认一次。
os.environ["DB_PATH"] = "./data/tmp_t20.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.catalog.models import CatalogEntry  # noqa: E402

BASE = "/api/v1/catalog"


@pytest.fixture(scope="module")
def client():
    init_engine()  # 用 DB_PATH 指向的临时库
    engine = get_engine()
    CatalogEntry.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    """每条测试前清空本插件表，测试之间不得相互依赖。"""
    engine = get_engine()
    with Session(engine) as s:
        s.exec(text("DELETE FROM catalog_entry"))
        s.commit()
    yield


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def _manual_payload(**kw):
    base = {
        "name": "自建统计 API",
        "kind": "web+rest",
        "endpoint": "/api/v1/stats",
        "auth_ref": "pat:env:STATS_TOKEN",
        "capabilities": ["stats.read"],
        "enabled": True,
    }
    base.update(kw)
    return base


# ───────────────────────── GET /api/v1/catalog（四源合并） ─────────────────────────


def test_catalog_requires_auth(client):
    r = client.get(BASE)
    assert r.status_code == 401, r.text


def test_catalog_returns_four_sources(client, auth):
    r = client.get(BASE, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "entries" in body
    assert "generatedAt" in body
    assert "counts" in body

    sources = {e["source"] for e in body["entries"]}
    # plugin 源（注册表实时）至少有 calendar/todo 等；kernel 源静态声明 ≥ 3；manual 源为空起步
    assert "plugin" in sources
    assert "kernel" in sources
    assert "manual" not in sources or body["counts"].get("manual", 0) == 0
    assert body["counts"]["kernel"] >= 3
    assert body["counts"]["plugin"] >= 3

    # 形状对齐 T19 CapabilityEntry 的字段名集合
    expected = {
        "id",
        "name",
        "kind",
        "url",
        "endpoint",
        "auth_ref",
        "capabilities",
        "enabled",
        "note",
        "source",
    }
    for e in body["entries"]:
        assert set(e.keys()) == expected


def test_catalog_plugin_entries_have_provides_caps(client, auth):
    r = client.get(BASE, headers=auth)
    body = r.json()
    plugin_entries = [e for e in body["entries"] if e["source"] == "plugin"]
    assert any("calendar.event.read" in e["capabilities"] for e in plugin_entries)
    assert any(e["endpoint"] == "/api/v1/calendar" for e in plugin_entries)


# ───────────────────────── manual CRUD ─────────────────────────


def test_manual_create_appears_and_persists(client, auth):
    r = client.post(f"{BASE}/manual", json=_manual_payload(), headers=auth)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["source"] == "manual"
    assert created["name"] == "自建统计 API"
    assert created["enabled"] is True
    assert created["capabilities"] == ["stats.read"]
    assert created["auth_ref"] == "pat:env:STATS_TOKEN"

    # 出现在目录里并计入 counts.manual
    body = client.get(BASE, headers=auth).json()
    manual_entries = [e for e in body["entries"] if e["source"] == "manual"]
    assert any(e["id"] == created["id"] for e in manual_entries)
    assert body["counts"]["manual"] == 1


def test_manual_toggle_enabled(client, auth):
    created = client.post(f"{BASE}/manual", json=_manual_payload(), headers=auth).json()
    r = client.patch(f"{BASE}/manual/{created['id']}", json={"enabled": False}, headers=auth)
    assert r.status_code == 200, r.text
    body = client.get(BASE, headers=auth).json()
    manual_entries = [e for e in body["entries"] if e["source"] == "manual"]
    assert any(e["id"] == created["id"] and e["enabled"] is False for e in manual_entries)


def test_manual_toggle_off_still_listed_but_marked(client, auth):
    """enabled=false 的条目不消失，但 enabled 标记为 false（前端灰显 / agent 跳过）。"""
    created = client.post(f"{BASE}/manual", json=_manual_payload(), headers=auth).json()
    client.patch(f"{BASE}/manual/{created['id']}", json={"enabled": False}, headers=auth)
    body = client.get(BASE, headers=auth).json()
    manual_entries = [e for e in body["entries"] if e["source"] == "manual"]
    assert len(manual_entries) == 1
    assert manual_entries[0]["enabled"] is False


def test_manual_delete(client, auth):
    created = client.post(f"{BASE}/manual", json=_manual_payload(), headers=auth).json()
    r = client.delete(f"{BASE}/manual/{created['id']}", headers=auth)
    assert r.status_code == 204, r.text
    body = client.get(BASE, headers=auth).json()
    assert body["counts"].get("manual", 0) == 0


def test_manual_update_fields(client, auth):
    created = client.post(f"{BASE}/manual", json=_manual_payload(), headers=auth).json()
    r = client.patch(
        f"{BASE}/manual/{created['id']}",
        json={"name": "改名后的能力", "capabilities": ["a.read", "b.write"]},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["name"] == "改名后的能力"
    assert updated["capabilities"] == ["a.read", "b.write"]


# ───────────────────────── manual 校验（对齐 T19） ─────────────────────────


def test_manual_rejects_plain_secret(client, auth):
    r = client.post(
        f"{BASE}/manual",
        json=_manual_payload(auth_ref="sk-live-abcdefghijklmnopqrstuvwxyz123456"),
        headers=auth,
    )
    assert r.status_code == 422, r.text


def test_manual_accepts_none_auth(client, auth):
    r = client.post(f"{BASE}/manual", json=_manual_payload(auth_ref="none"), headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["auth_ref"] == "none"


def test_manual_rejects_bad_url_scheme(client, auth):
    r = client.post(
        f"{BASE}/manual",
        json=_manual_payload(kind="web", url="javascript:alert(1)"),
        headers=auth,
    )
    assert r.status_code == 422, r.text


def test_manual_rejects_non_web_without_endpoint(client, auth):
    r = client.post(
        f"{BASE}/manual",
        json=_manual_payload(kind="web+mcp", endpoint=None),
        headers=auth,
    )
    assert r.status_code == 422, r.text


def test_manual_not_found_404(client, auth):
    r = client.delete(f"{BASE}/manual/nonexistent", headers=auth)
    assert r.status_code == 404, r.text