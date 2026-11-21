"""B1 提案层测试（bidiu 2026-11-21）。

覆盖：
  · 状态机全转移：pending → approved → undone；pending → rejected
  · approve 真执行（todo 域造数：签了才删）
  · undo 真复原（delete 的 before 快照重建；新 id、同内容）
  · MCP_PROPOSAL_MODE=on：write 工具被拦成提案（数据不动）、read 工具不受影响
  · 拦截关（缺省）：write 工具直执（存量行为不变）
  · 提案层自己的工具不被自拦（approve/reject/undo 直达）

数据库隔离：临时库 ./data/tmp_b1proposals.db。
内部 HTTP 在测试里走**同一个 TestClient**（monkeypatch service._request 路由到
client.request）——真实转发链路由 release_check / 真栈端到端守门。
"""
from __future__ import annotations

import json
import os

# ★ 必须在 import 任何内核/模块之前设置临时库，init_engine 只认一次。
os.environ["DB_PATH"] = "./data/tmp_b1proposals.db"

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
    # 显式 import 保证表注册（消除 import 顺序依赖——test_mcp.py 同款教训）
    from modules.mcp import models as _mcp_models  # noqa: F401
    from modules.proposals import models as _proposals_models  # noqa: F401
    from modules.todo import models as _todo_models  # noqa: F401

    # 每次 pytest 进程拿到干净库：临时库里可能残留上次运行的提案，会污染
    # 「pending 列表为空」这类断言（实测踩过：单独跑绿、全量跑红）。
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture()
def pat(client):
    """建一枚带 todo 全套 scope 的 PAT，返回 (明文, 头)。"""
    r = client.post(
        "/api/v1/mcp/pats",
        json={
            "name": "提案探针",
            "scopes": ["todo:read", "todo:write", "todo:delete", "todo:patch"],
        },
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["token"], {"Authorization": f"Bearer {body['token']}"}


def _rpc(method: str, params: dict | None = None, msg_id: int | str = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


@pytest.fixture()
def fake_internal(monkeypatch, client):
    """把 proposals.service._request 路由到同一个 TestClient——测试里的内部 HTTP。"""
    from modules.proposals import service as svc

    def fake(method: str, path: str, payload: dict | None):
        real_path, body = svc._substitute_path(path, payload or {})
        headers = {**AUTH, "X-Alongside-Client": "proposal-test"}
        resp = client.request(method, real_path, json=body if body else None, headers=headers)
        if resp.status_code == 204 or not resp.content:
            return resp.status_code, None
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, {"raw": resp.text[:300]}

    monkeypatch.setattr(svc, "_request", fake)
    return fake


@pytest.fixture()
def fake_mcp_forward(monkeypatch, client):
    """把 mcp_server.forward 路由到同一个 TestClient——MCP 拦截内部 HTTP 的测试替身。"""
    import re as _re

    from modules.mcp import mcp_server

    def fake(method: str, path: str, payload: dict | None, transport=None):
        body = dict(payload or {})

        def sub(m):
            val = body.pop(m.group(1), None)
            return str(val) if val is not None else m.group(0)

        real = _re.sub(r"\{(\w+)\}", sub, path)
        verb = method.upper()
        headers = {**AUTH, "X-Alongside-Client": "mcp"}
        if verb == "GET":
            resp = client.request("GET", real, params=body or None, headers=headers)
        else:
            resp = client.request(verb, real, json=body or None, headers=headers)
        if not resp.content:
            return resp.status_code, None
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, {"raw": resp.text[:200]}

    monkeypatch.setattr(mcp_server, "forward", fake)
    return fake


def _make_todo(client, text: str) -> dict:
    r = client.post("/api/v1/todo/items", json={"text": text, "tags": ["测试"]}, headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def _todo_exists(client, text: str) -> bool:
    r = client.get("/api/v1/todo/items", headers=AUTH)
    items = r.json().get("items", [])
    return any(i.get("text") == text for i in items)


# ───────────────────────── 状态机与真实执行 ─────────────────────────


def test_delete_flow_pending_approve_undo(client, fake_internal):
    """delete 提案：签发不动数据 → 签字真删 → 撤销真回来（新 id 同内容）。"""
    row = _make_todo(client, "B1 提案闭环验证（删了要能回来）")
    item_id = row["id"]

    # ① 人类直接签发一条 delete 提案（before 快照 = 当前整行）
    r = client.post(
        "/api/v1/proposals",
        json={
            "title": "删除一条测试待办",
            "tool_name": "todo_item_delete",
            "action": "delete",
            "method": "DELETE",
            "path": "/api/v1/todo/items/{item_id}",
            "payload": {"item_id": item_id},
            "before": row,
            "actor": "human",
        },
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["status"] == "pending"
    # 数据还在（提案不执行）
    assert _todo_exists(client, row["text"])

    # ② 签字 → 真删
    r2 = client.post(f"/api/v1/proposals/{pid}/approve", headers=AUTH)
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "approved"
    assert not _todo_exists(client, row["text"]), "签字后待办应真被删掉"

    # ③ 撤销 → before 整行重建（新 id、同内容）
    r3 = client.post(f"/api/v1/proposals/{pid}/undo", headers=AUTH)
    assert r3.status_code == 200, r3.text
    assert r3.json()["status"] == "undone"
    assert _todo_exists(client, row["text"]), "撤销后待办应被 before 快照重建"
    # 新 id ≠ 原 id（诚实语义：重建，不是复活原行）
    r4 = client.get("/api/v1/todo/items", headers=AUTH)
    got = [i for i in r4.json().get("items", []) if i.get("text") == row["text"]]
    assert got and got[0]["id"] != item_id


def test_reject_terminal(client):
    """pending → rejected；rejected 不能再签字/撤销。"""
    r = client.post(
        "/api/v1/proposals",
        json={
            "title": "会被作废的提案",
            "tool_name": "todo_item_delete",
            "action": "delete",
            "method": "DELETE",
            "path": "/api/v1/todo/items/{item_id}",
            "payload": {"item_id": "nonexistent"},
            "actor": "human",
        },
        headers=AUTH,
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    assert client.post(f"/api/v1/proposals/{pid}/reject", headers=AUTH).status_code == 200
    r2 = client.post(f"/api/v1/proposals/{pid}/approve", headers=AUTH)
    assert r2.status_code == 422, "rejected 是终态，不能签字"
    r3 = client.post(f"/api/v1/proposals/{pid}/undo", headers=AUTH)
    assert r3.status_code == 422, "rejected 不能撤销"


def test_approve_failure_keeps_pending(client, monkeypatch):
    """执行失败（内部 500）→ 状态留在 pending（可重试），不做假批准。"""
    from modules.proposals import service as svc

    r = client.post(
        "/api/v1/proposals",
        json={
            "title": "会执行失败的提案",
            "tool_name": "todo_item_delete",
            "action": "delete",
            "method": "DELETE",
            "path": "/api/v1/todo/items/{item_id}",
            "payload": {"item_id": "no-such-id"},
            "actor": "human",
        },
        headers=AUTH,
    )
    pid = r.json()["id"]

    def boom(method, path, payload):
        return 500, {"detail": "boom"}

    monkeypatch.setattr(svc, "_request", boom)
    r2 = client.post(f"/api/v1/proposals/{pid}/approve", headers=AUTH)
    assert r2.status_code == 422, r2.text
    assert "500" in r2.json()["detail"]
    row = client.get(f"/api/v1/proposals/{pid}", headers=AUTH).json()
    assert row["status"] == "pending", "执行失败必须留在 pending"


def test_create_action_validated(client):
    """action 必须是三分之一；乱填 422。"""
    r = client.post(
        "/api/v1/proposals",
        json={"title": "x", "tool_name": "x", "action": "炸一炸", "method": "POST", "path": "/x"},
        headers=AUTH,
    )
    assert r.status_code == 422


# ───────────────────────── MCP 拦截 ─────────────────────────


def _mcp_call(client, headers, name, arguments, msg_id=1):
    r = client.post(
        "/api/v1/mcp",
        json=_rpc("tools/call", {"name": name, "arguments": arguments}, msg_id),
        headers=headers,
    )
    assert r.status_code == 200, r.text
    out = r.json()["result"]
    text = out["content"][0]["text"]
    try:
        return out.get("isError"), json.loads(text)
    except Exception:  # noqa: BLE001
        return out.get("isError"), {"raw": text[:200]}


def test_mcp_mode_on_write_intercepted_read_not(client, pat, fake_internal, fake_mcp_forward, monkeypatch):
    """开拦截：write 被拦成提案（数据不动）；read 直达；提案层工具不自拦。"""
    monkeypatch.setenv("MCP_PROPOSAL_MODE", "on")
    _, headers = pat
    row = _make_todo(client, "B1 MCP 拦截验证（先不动，签了才动）")
    item_id = row["id"]

    # ① write（delete）被拦 → 提案；数据不动
    err, body = _mcp_call(client, headers, "todo_item_delete", {"payload": {"item_id": item_id}})
    assert not err, body
    assert body["status"] == "pending", body
    assert body["proposal_id"], body
    assert _todo_exists(client, row["text"]), "拦截模式下 delete 不应直接执行"

    # ② 提案里应该有 before 快照（拦截点 GET 回来的）
    plist = client.get("/api/v1/proposals?status=pending", headers=AUTH).json()
    mine = [p for p in plist if p["tool_name"] == "todo_item_delete"]
    assert mine, plist
    assert mine[0]["before"], "delete 提案应带 before 快照"
    assert mine[0]["before"].get("id") == item_id
    assert mine[0]["actor"].startswith("mcp:"), mine[0]["actor"]

    # ③ read 工具不受影响（直达，不产提案）
    err, body = _mcp_call(client, headers, "todo_item_read", {"payload": {}}, msg_id=2)
    assert not err
    assert body.get("status") == 200, body
    pending_after = client.get("/api/v1/proposals?status=pending", headers=AUTH).json()
    assert len(pending_after) == len(plist), "read 不应产生提案"

    # ④ 签字 → 真删（AI 侧拿到的回执指明了 approve 工具）
    pid = mine[0]["id"]
    assert client.post(f"/api/v1/proposals/{pid}/approve", headers=AUTH).status_code == 200
    assert not _todo_exists(client, row["text"])


def test_mcp_mode_explicit_off_write_direct(client, pat, fake_mcp_forward, monkeypatch):
    """显式关（MCP_PROPOSAL_MODE=off）：write 直执——逃生阀可用。"""
    monkeypatch.setenv("MCP_PROPOSAL_MODE", "off")
    _, headers = pat
    # 别的测试可能留 pending 提案；断言「本测试不新增 pending」而非「全局为空」
    pending_before = len(client.get("/api/v1/proposals?status=pending", headers=AUTH).json())
    row = _make_todo(client, "B1 直执验证（显式关拦截）")
    item_id = row["id"]

    err, body = _mcp_call(client, headers, "todo_item_delete", {"payload": {"item_id": item_id}})
    assert not err, body
    assert body.get("status") == 204, body  # 直执：204 No Content
    assert not _todo_exists(client, row["text"]), "显式关拦截时 delete 应直接生效"
    pending_after = len(client.get("/api/v1/proposals?status=pending", headers=AUTH).json())
    assert pending_after == pending_before, "显式关时不应产生新提案"


def test_mcp_mode_default_fail_closed(client, pat, fake_mcp_forward, monkeypatch):
    """缺省（不设环境变量）= **开**（fail-closed）：write 被拦成提案，数据不动。

    共识 #9：忘设变量 = 静默裸奔，与「简介说有撤销但代码没有」同类病。
    """
    monkeypatch.delenv("MCP_PROPOSAL_MODE", raising=False)
    _, headers = pat
    row = _make_todo(client, "B1 缺省 fail-closed 验证（不该被直删）")
    item_id = row["id"]

    err, body = _mcp_call(client, headers, "todo_item_delete", {"payload": {"item_id": item_id}})
    assert not err, body
    assert body.get("status") == "pending", body
    assert body.get("proposal_id"), body
    assert _todo_exists(client, row["text"]), "缺省（开）时 delete 不得直接执行"


def test_mcp_mode_any_non_off_value_keeps_it_on(client, pat, monkeypatch):
    """除显式关词外一切取值（含 "on"/"1"/"ON"/垃圾值）= 开——只有明确关法才放行。"""
    for value in ("on", "1", "ON", "true", "garbage"):
        monkeypatch.setenv("MCP_PROPOSAL_MODE", value)
        from modules.mcp.mcp_server import _proposal_mode_on

        assert _proposal_mode_on() is True, f"{value!r} 应视为开"
    for value in ("off", "0", "FALSE", " no ", "disable", "disabled"):
        monkeypatch.setenv("MCP_PROPOSAL_MODE", value)
        from modules.mcp.mcp_server import _proposal_mode_on

        assert _proposal_mode_on() is False, f"{value!r} 应视为关"


def test_mcp_proposal_tools_not_self_intercepted(client, pat, fake_mcp_forward, monkeypatch):
    """开拦截时，提案层自己的工具（write=签发）不被自拦。"""
    monkeypatch.setenv("MCP_PROPOSAL_MODE", "on")
    # 给 PAT 补 proposals scope（另发一枚；拦截开关对提案层工具豁免）
    r = client.post(
        "/api/v1/mcp/pats",
        json={
            "name": "提案自用",
            "scopes": ["proposals:read", "proposals:write", "proposals:approve", "proposals:list"],
        },
        headers=AUTH,
    )
    assert r.status_code == 201
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    err, body = _mcp_call(
        client,
        headers,
        "proposals_proposal_write",
        {"payload": {"title": "工具面直发的提案", "tool_name": "manual", "action": "update", "method": "PATCH", "path": "/x"}},
        msg_id=9,
    )
    assert not err, body
    assert body.get("status") == 201, body
    assert body.get("body", {}).get("id"), body  # 转发结果：{status, body:{提案}}


def test_pat_never_expired_still_valid(client, pat):
    """S2 回归抽查：带未来 expires_at 的 PAT 正常鉴权。"""
    _, headers = pat
    r = client.post("/api/v1/mcp", json=_rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}), headers=headers)
    assert r.status_code == 200


# ─────────────── P1 修复回归（汐瑶实测 · bidiu 2026-11-22） ───────────────
# ① 提案 path 用**真实 id**（非占位符）时，undo 必须仍能降成集合路径（旧实现 405）
# ② method/path 已改必填（此前注释称「空=服务端推导」与实现不符）


def test_collection_path_strips_placeholder_and_real_id():
    """`_collection_path`：占位符与真实 id 都降成集合路径；集合路径原样。"""
    from modules.proposals.service import _collection_path

    assert _collection_path("/api/v1/todo/items/{item_id}") == "/api/v1/todo/items"
    assert (
        _collection_path("/api/v1/todo/items/04a94fb5b15d4e358f86cebb06619b52")
        == "/api/v1/todo/items"
    )
    assert _collection_path("/api/v1/calendar/events/12") == "/api/v1/calendar/events"
    assert _collection_path("/api/v1/todo/items") == "/api/v1/todo/items"


def test_delete_proposal_with_real_id_path_undo_restores(client, fake_internal):
    """真实 id 路径的 delete 提案：签发→approve 真删→undo 复原（P1 场景端到端）。"""
    row = _make_todo(client, "P1 真实 id 路径 undo 必须能复原")
    item_id = row["id"]
    r = client.post(
        "/api/v1/proposals",
        json={
            "title": "真实 id 路径删除",
            "tool_name": "todo_item_delete",
            "action": "delete",
            "method": "DELETE",
            "path": f"/api/v1/todo/items/{item_id}",  # ★ 真实 id，不是占位符
            "payload": {},
            "before": row,
            "actor": "human",
        },
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert client.post(f"/api/v1/proposals/{pid}/approve", headers=AUTH).status_code == 200
    assert not _todo_exists(client, row["text"]), "approve 后应真删"
    r2 = client.post(f"/api/v1/proposals/{pid}/undo", headers=AUTH)
    assert r2.status_code == 200, r2.text  # 旧实现此处 405
    assert _todo_exists(client, row["text"]), "undo 后应复原"


def test_create_proposal_requires_method_and_path(client):
    """method/path 必填（此前注释说可省，实现未推导——按判据 5 改诚实）。"""
    r = client.post(
        "/api/v1/proposals",
        json={"title": "缺路径", "tool_name": "x", "action": "create", "payload": {}},
        headers=AUTH,
    )
    assert r.status_code == 422, r.text
