"""T12 AI 智能体后端测试。

数据库隔离：./data/tmp_t12.db，绝不碰主库。
不调外网：dispatch / report 全离线。
"""
from __future__ import annotations
from core.db_scope import declare_scope  # 2026-11-09 作用域翻转：显式声明

import os

os.environ["DB_PATH"] = "./data/tmp_t12.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.agents.models import AgentAgent, AgentDispatch, AgentTask  # noqa: E402

BASE = "/api/v1/agents"


@pytest.fixture(scope="module")
def client():
    init_engine()
    engine = get_engine()
    AgentTask.__table__.create(bind=engine, checkfirst=True)
    AgentAgent.__table__.create(bind=engine, checkfirst=True)
    AgentDispatch.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    engine = get_engine()
    with declare_scope(__import__("sqlmodel").Session(engine)) as s:
        s.exec(text("DELETE FROM agents_dispatch"))
        s.exec(text("DELETE FROM agents_task"))
        s.exec(text("DELETE FROM agents_agent"))
        s.commit()
    yield


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def _mk_agent(client, auth, **kw):
    payload = {"name": "研究员", **kw}
    r = client.post(f"{BASE}/agents", json=payload, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _mk_task(client, auth, **kw):
    payload = {"title": "整理周报", **kw}
    r = client.post(f"{BASE}/tasks", json=payload, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


# ───────────────────────── 基础 ─────────────────────────
def test_health(client):
    r = client.get(f"{BASE}/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_manifest(client):
    r = client.get(f"{BASE}/manifest")
    assert r.status_code == 200
    assert r.json()["id"] == "agents"


def test_no_auth(client):
    assert client.get(f"{BASE}/agents").status_code in (401, 403)
    assert client.get(f"{BASE}/tasks").status_code in (401, 403)
    assert client.get(f"{BASE}/summary").status_code in (401, 403)


# ───────────────────────── Agent CRUD ─────────────────────────
def test_agent_create_and_get(client, auth):
    a = _mk_agent(
        client,
        auth,
        name="写作助手",
        description="负责文案",
        capabilities=["write", "summary"],
    )
    assert a["name"] == "写作助手"
    assert a["description"] == "负责文案"
    assert a["capabilities"] == ["write", "summary"]
    assert a["enabled"] is True
    r = client.get(f"{BASE}/agents/{a['id']}", headers=auth)
    assert r.status_code == 200
    assert r.json()["id"] == a["id"]


def test_agent_list_and_filter(client, auth):
    _mk_agent(client, auth, name="A-启用", enabled=True)
    _mk_agent(client, auth, name="B-停用", enabled=False)
    all_names = [x["name"] for x in client.get(f"{BASE}/agents", headers=auth).json()]
    assert "A-启用" in all_names and "B-停用" in all_names
    only = [
        x["name"]
        for x in client.get(f"{BASE}/agents?enabled_only=true", headers=auth).json()
    ]
    assert "A-启用" in only
    assert "B-停用" not in only


def test_agent_update_enable_disable(client, auth):
    a = _mk_agent(client, auth, name="可切换")
    r = client.patch(
        f"{BASE}/agents/{a['id']}", json={"enabled": False}, headers=auth
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    r = client.patch(
        f"{BASE}/agents/{a['id']}",
        json={"enabled": True, "name": "可切换-改名", "description": "新描述"},
        headers=auth,
    )
    body = r.json()
    assert body["enabled"] is True
    assert body["name"] == "可切换-改名"
    assert body["description"] == "新描述"


def test_agent_delete(client, auth):
    a = _mk_agent(client, auth, name="要删的")
    r = client.delete(f"{BASE}/agents/{a['id']}", headers=auth)
    assert r.status_code == 204, r.text
    assert client.get(f"{BASE}/agents/{a['id']}", headers=auth).status_code == 404


def test_agent_boundary(client, auth):
    r = client.post(f"{BASE}/agents", json={"name": "  "}, headers=auth)
    assert r.status_code in (400, 422), r.text
    r = client.post(f"{BASE}/agents", json={"name": "长" * 121}, headers=auth)
    assert r.status_code == 422, r.text
    a = _mk_agent(client, auth, name="原名")
    r = client.patch(f"{BASE}/agents/{a['id']}", json={"name": "   "}, headers=auth)
    assert r.status_code in (400, 422), r.text
    body = client.get(f"{BASE}/agents/{a['id']}", headers=auth).json()
    assert body["name"] == "原名"


def test_agent_chinese_and_special(client, auth):
    a = _mk_agent(client, auth, name="编排🤖&「测试」—v0.1!")
    assert a["name"] == "编排🤖&「测试」—v0.1!"
    assert client.get(f"{BASE}/agents/{a['id']}", headers=auth).status_code == 200


# ───────────────────────── Task CRUD ─────────────────────────
def test_task_create_and_get(client, auth):
    t = _mk_task(
        client,
        auth,
        title="拆解目标",
        description="一句话目标 → 任务块",
        priority=2,
        acceptance=["有产出", "可验收"],
    )
    assert t["title"] == "拆解目标"
    assert t["status"] == "draft"
    assert t["priority"] == 2
    assert t["acceptance"] == ["有产出", "可验收"]
    r = client.get(f"{BASE}/tasks/{t['id']}", headers=auth)
    assert r.status_code == 200
    assert r.json()["id"] == t["id"]


def test_task_list_filter_status(client, auth):
    _mk_task(client, auth, title="T-draft")
    t2 = _mk_task(client, auth, title="T-queued")
    client.post(f"{BASE}/tasks/{t2['id']}/dispatch", json={}, headers=auth)
    drafts = client.get(f"{BASE}/tasks?status=draft", headers=auth).json()
    assert all(x["status"] == "draft" for x in drafts)
    assert any(x["title"] == "T-draft" for x in drafts)
    queued = client.get(f"{BASE}/tasks?status=queued", headers=auth).json()
    assert any(x["title"] == "T-queued" for x in queued)


def test_task_update_fields(client, auth):
    t = _mk_task(client, auth, title="旧标题")
    r = client.patch(
        f"{BASE}/tasks/{t['id']}",
        json={"title": "新标题", "priority": 1, "description": "改过了"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "新标题"
    assert body["priority"] == 1
    assert body["description"] == "改过了"


def test_task_delete(client, auth):
    t = _mk_task(client, auth, title="删我")
    r = client.delete(f"{BASE}/tasks/{t['id']}", headers=auth)
    assert r.status_code == 204, r.text
    assert client.get(f"{BASE}/tasks/{t['id']}", headers=auth).status_code == 404


def test_task_boundary(client, auth):
    r = client.post(f"{BASE}/tasks", json={"title": "   "}, headers=auth)
    assert r.status_code in (400, 422), r.text
    r = client.post(f"{BASE}/tasks", json={"title": "长" * 201}, headers=auth)
    assert r.status_code == 422, r.text
    r = client.post(f"{BASE}/tasks", json={"title": "坏优先级", "priority": 0}, headers=auth)
    assert r.status_code == 422, r.text
    r = client.post(f"{BASE}/tasks", json={"title": "坏mode", "mode": "smtp"}, headers=auth)
    assert r.status_code in (400, 422), r.text


def test_task_404(client, auth):
    missing = "no-such-task"
    assert client.get(f"{BASE}/tasks/{missing}", headers=auth).status_code == 404
    assert client.patch(
        f"{BASE}/tasks/{missing}", json={"title": "x"}, headers=auth
    ).status_code == 404
    assert client.delete(f"{BASE}/tasks/{missing}", headers=auth).status_code == 404
    assert client.post(
        f"{BASE}/tasks/{missing}/dispatch", json={}, headers=auth
    ).status_code == 404
    assert client.post(
        f"{BASE}/tasks/{missing}/report", json={}, headers=auth
    ).status_code == 404


def test_agent_404(client, auth):
    missing = "no-such-agent"
    assert client.get(f"{BASE}/agents/{missing}", headers=auth).status_code == 404
    assert client.patch(
        f"{BASE}/agents/{missing}", json={"name": "x"}, headers=auth
    ).status_code == 404
    assert client.delete(f"{BASE}/agents/{missing}", headers=auth).status_code == 404


# ───────────────────────── 派发 / 回报（离线） ─────────────────────────
def test_dispatch_without_agent(client, auth):
    t = _mk_task(client, auth, title="无 agent 派发")
    r = client.post(f"{BASE}/tasks/{t['id']}/dispatch", json={}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task"]["status"] == "queued"
    assert body["task"]["dispatch_id"]
    assert body["mode"] == "poll"
    assert body["attempts"] == 1


def test_dispatch_with_agent(client, auth):
    a = _mk_agent(client, auth, name="派发目标", callback_url="http://127.0.0.1:9/cb")
    t = _mk_task(client, auth, title="指定 agent")
    r = client.post(
        f"{BASE}/tasks/{t['id']}/dispatch",
        json={"agent_id": a["id"], "mode": "webhook"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task"]["assignee"] == "派发目标"
    assert body["task"]["mode"] == "webhook"
    assert body["target"] == "http://127.0.0.1:9/cb"
    agent = client.get(f"{BASE}/agents/{a['id']}", headers=auth).json()
    assert agent["load"] == 1


def test_dispatch_disabled_agent_rejected(client, auth):
    a = _mk_agent(client, auth, name="停用agent", enabled=False)
    t = _mk_task(client, auth, title="派给停用")
    r = client.post(
        f"{BASE}/tasks/{t['id']}/dispatch", json={"agent_id": a["id"]}, headers=auth
    )
    assert r.status_code in (400, 422), r.text


def test_dispatch_done_task_rejected(client, auth):
    t = _mk_task(client, auth, title="已完成不可派")
    client.post(f"{BASE}/tasks/{t['id']}/dispatch", json={}, headers=auth)
    client.post(
        f"{BASE}/tasks/{t['id']}/report",
        json={"result": "ok", "result_status": "done"},
        headers=auth,
    )
    r = client.post(f"{BASE}/tasks/{t['id']}/dispatch", json={}, headers=auth)
    assert r.status_code in (400, 422), r.text


def test_report_done_and_immutability(client, auth):
    t = _mk_task(client, auth, title="回报完成")
    client.post(f"{BASE}/tasks/{t['id']}/dispatch", json={}, headers=auth)
    r = client.post(
        f"{BASE}/tasks/{t['id']}/report",
        json={"result": "已交付", "result_status": "done", "outputs": ["周报.md"]},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "done"
    assert body["result"] == "已交付"
    assert body["outputs"] == ["周报.md"]
    assert body["finished_at"] is not None
    # done 后不可再改 / 再回报
    r2 = client.patch(
        f"{BASE}/tasks/{t['id']}", json={"title": "想改"}, headers=auth
    )
    assert r2.status_code in (400, 422), r2.text
    r3 = client.post(
        f"{BASE}/tasks/{t['id']}/report", json={"result": "again"}, headers=auth
    )
    assert r3.status_code in (400, 422), r3.text
    r4 = client.delete(f"{BASE}/tasks/{t['id']}", headers=auth)
    assert r4.status_code in (400, 422), r4.text


def test_report_failed_then_retry_dispatch(client, auth):
    t = _mk_task(client, auth, title="失败可重试")
    client.post(f"{BASE}/tasks/{t['id']}/dispatch", json={}, headers=auth)
    r = client.post(
        f"{BASE}/tasks/{t['id']}/report",
        json={"result": "炸了", "result_status": "failed"},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    r2 = client.post(f"{BASE}/tasks/{t['id']}/dispatch", json={}, headers=auth)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["task"]["status"] == "queued"
    assert body["task"]["attempts"] == 2


def test_status_transition_illegal(client, auth):
    t = _mk_task(client, auth, title="非法跃迁")
    # draft 不能直接 done
    r = client.patch(
        f"{BASE}/tasks/{t['id']}", json={"status": "done"}, headers=auth
    )
    assert r.status_code in (400, 422), r.text
    r2 = client.post(
        f"{BASE}/tasks/{t['id']}/report",
        json={"result": "x", "result_status": "done"},
        headers=auth,
    )
    assert r2.status_code in (400, 422), r2.text


def test_list_tasks_bad_status(client, auth):
    r = client.get(f"{BASE}/tasks?status=nope", headers=auth)
    assert r.status_code in (400, 422), r.text


# ───────────────────────── Summary ─────────────────────────
def test_summary(client, auth):
    _mk_agent(client, auth, name="S1", enabled=True)
    _mk_agent(client, auth, name="S2", enabled=False)
    t1 = _mk_task(client, auth, title="S-draft")
    t2 = _mk_task(client, auth, title="S-done")
    client.post(f"{BASE}/tasks/{t2['id']}/dispatch", json={}, headers=auth)
    client.post(
        f"{BASE}/tasks/{t2['id']}/report",
        json={"result": "ok", "result_status": "done"},
        headers=auth,
    )
    s = client.get(f"{BASE}/summary", headers=auth).json()
    assert s["tasks_total"] >= 2
    assert s["draft"] >= 1
    assert s["done"] >= 1
    assert s["agents_total"] >= 2
    assert s["agents_enabled"] >= 1
    assert t1["status"] == "draft"
