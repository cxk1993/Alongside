"""ai-chat 写工具的集成证据：**起草提案，不改数据**。

单测（`test_ai_chat_tools.py`）钉了工具面/能力/措辞三件事，但那些都是静态断言。
真正要命的一条在这里：**调 `todo_item_delete` 之后，待办必须还在，而提案池里多一张 pending。**

这条之所以必须存在（2026-11-22 比丢）：`make_tools` 里所有工具都走内部 HTTP。
如果写工具直接 `POST /api/v1/todo/items/{id}` 或 `DELETE`，AI 对话窗口就成了
绕过提案层的第二条写通道 —— 平台内「AI 提议、人执行」当场作废。
本测试把 ai-chat 的 `get_plugin_client` 换成立在同一 TestClient 上的假客户端，
真调 handler，再回头查数据。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MOD_DIR = Path(__file__).resolve().parents[1] / "modules" / "ai-chat"


def _load(mod_name: str, file_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, _MOD_DIR / file_name)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


tools = _load("it_ai_chat_tools", "tools.py")


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from sqlmodel import SQLModel

    from core.app import create_app
    from db.engine import get_engine, init_engine

    init_engine()
    engine = get_engine()
    # 显式 import 保证表注册（与 test_proposals.py 同款教训）
    from modules.proposals import models as _proposals_models  # noqa: F401
    from modules.todo import models as _todo_models  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    c = TestClient(create_app())
    yield c
    engine.dispose()


@pytest.fixture()
def auth():
    from core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token('admin')}"}


@pytest.fixture()
def wired(client, auth, monkeypatch):
    """把 ai-chat 的 get_plugin_client 换成立在同一 TestClient 上的假客户端。"""

    class SameStackClient:
        def _do(self, method: str, path: str, **kw):
            r = client.request(method, path, headers=auth, **kw)
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {path} → {r.status_code}: {r.text[:160]}")
            if r.status_code == 204 or not r.content:
                return None
            return r.json()

        def get(self, path: str, **kw):
            return self._do("GET", path, **kw)

        def post(self, path: str, **kw):
            return self._do("POST", path, **kw)

        def patch(self, path: str, **kw):
            return self._do("PATCH", path, **kw)

        def delete(self, path: str, **kw):
            return self._do("DELETE", path, **kw)

    monkeypatch.setattr(tools, "get_plugin_client", lambda _req, _caps: SameStackClient())
    return tools.make_tools(request=object())  # request 只用于取 client，已被替换


# ─────────────────────────── 真正要钉的一条 ───────────────────────────


def test_delete_tool_drafts_proposal_and_keeps_item(client, auth, wired):
    """调删除工具 → 提案 pending + 待办仍在（**没有被删**）。"""
    r = client.post("/api/v1/todo/items", json={"text": "写工具集成探针·可删"}, headers=auth)
    assert r.status_code in (200, 201), r.text
    item_id = r.json()["id"]

    out = wired["todo_item_delete"]({"item_id": item_id})
    assert out.get("ok") is True, out
    assert out.get("proposal_id"), out
    assert out.get("status") == "pending", out
    assert "尚未执行" in out.get("message", ""), out

    # ★ 核心：待办还在
    still = client.get(f"/api/v1/todo/items/{item_id}", headers=auth)
    assert still.status_code == 200, f"写工具不该直接删数据，实际 {still.status_code}"

    # 提案池里有一张 pending，且带 before 快照（撤销要靠它重建）
    plist = client.get("/api/v1/proposals?status=pending", headers=auth).json()
    mine = [p for p in plist if p["id"] == out["proposal_id"]]
    assert len(mine) == 1, "提案没落库"
    assert mine[0]["action"] == "delete"
    assert mine[0]["before"], "delete 提案必须带 before 快照（否则签发就该 422）"


def test_create_tool_drafts_without_creating(client, auth, wired):
    """调新建工具 → 提案 pending，但待办列表里没有这条新内容。"""
    marker = "写工具集成探针·新建"
    out = wired["todo_item_create"]({"text": marker, "priority": "low"})
    assert out.get("ok") is True and out.get("status") == "pending", out

    items = client.get("/api/v1/todo/items", headers=auth).json()
    rows = items if isinstance(items, list) else items.get("items", [])
    assert not any(i.get("text") == marker for i in rows), "写工具不该直接创建数据"


def test_update_tool_requires_and_sends_before(client, auth, wired):
    """update 也必须带 before；且原文没被改。"""
    r = client.post("/api/v1/todo/items", json={"text": "写工具集成探针·原文"}, headers=auth)
    item_id = r.json()["id"]

    out = wired["todo_item_update"]({"item_id": item_id, "text": "改过的标题"})
    assert out.get("status") == "pending", out

    cur = client.get(f"/api/v1/todo/items/{item_id}", headers=auth).json()
    assert cur.get("text") == "写工具集成探针·原文", "update 工具不该直接改数据"

    plist = client.get("/api/v1/proposals?status=pending", headers=auth).json()
    mine = [p for p in plist if p["id"] == out["proposal_id"]]
    assert mine and mine[0]["before"], "update 提案必须带 before 快照"