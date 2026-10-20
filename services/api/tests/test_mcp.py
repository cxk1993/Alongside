"""T18 MCP Server 测试。

覆盖：
  - derive_tool 机械映射（表驱动：名称/方法/路径/scope/未知动词跳过）
  - PAT 生命周期：创建（明文只回一次）→ 列表无明文 → patch scopes → 吊销 → 401
  - MCP 端点鉴权：无头 / 非 PAT 前缀 / 假 token → 401 problem+json
  - 协议三原语：initialize / tools/list / tools/call（越权 403 + 拒绝审计）
  - notifications → 202；未知 method → -32601
  - ★ 模块洁癖自检：modules/mcp/ 源码零业务词、零业务插件 import

数据库隔离：临时库 ./data/tmp_t18mcp.db。tools/list 只读磁盘 manifest + 库，
转发在测试里用 monkeypatch 替换（真实转发链路由 verify_t18_criterion.py 守门）。
"""
from __future__ import annotations

import os
import re

# ★ 必须在 import 任何内核/模块之前设置临时库，init_engine 只认一次。
os.environ["DB_PATH"] = "./data/tmp_t18mcp.db"

import ast  # noqa: E402（模块级：_is_docstring 也要用）
import hashlib  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.mcp import mcp_server  # noqa: E402
from modules.mcp.auth import PAT_PREFIX  # noqa: E402
from modules.mcp.registry_adapter import derive_tool  # noqa: E402

AUTH = {"Authorization": f"Bearer {create_access_token('admin')}"}


@pytest.fixture(scope="module")
def client():
    init_engine()
    engine = get_engine()
    # ★ 测试隔离加固（2026-09-25 · astrbot）：
    #   `SQLModel.metadata.create_all()` 只建**已 import** 的模型类；
    #   若本模块运行时 `modules.mcp.models` 尚未被 import，`mcp_pat` 表不会创建
    #   → `test_pat_*` 在 setup 阶段报 `no such table: mcp_pat`（首跑偶发；复跑因
    #   其他文件已 import 而幸免——即"首跑红、复跑绿"的隔离缺陷）。
    #   显式 import 保证表注册，**消除 import 顺序依赖**。
    from modules.mcp import models as _mcp_models  # noqa: F401

    # 建全部已注册表（mcp_pat + 内核表）；checkfirst 幂等。
    SQLModel.metadata.create_all(engine)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture()
def pat(client):
    """建一个带 calendar:read/write 的 PAT，返回 (明文, 头, id)。"""
    r = client.post(
        "/api/v1/mcp/pats",
        json={"name": "测试-PAT", "scopes": ["calendar:read", "calendar:write"]},
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    plain = body["token"]
    return plain, {"Authorization": f"Bearer {plain}"}, body["id"]


def _rpc(method: str, params: dict | None = None, msg_id: int | str = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


# ───────────────────────── 机械映射（纯函数，表驱动） ─────────────────────────
@pytest.mark.parametrize(
    ("provides", "base", "expect"),
    [
        ("calendar.event.write", "/api/v1/calendar",
         ("calendar_event_write", "POST", "/api/v1/calendar/events", "calendar:write")),
        ("calendar.slot.free", "/api/v1/calendar",
         ("calendar_slot_free", "GET", "/api/v1/calendar/slots", "calendar:free")),
        ("notes.note.read", "/api/v1/notes",
         ("notes_note_read", "GET", "/api/v1/notes/notes", "notes:read")),
        ("finance.snapshot.read", "/api/v1/finance",
         ("finance_snapshot_read", "GET", "/api/v1/finance/snapshots", "finance:read")),
        ("todo.item.update", "/api/v1/todo",
         ("todo_item_update", "PUT", "/api/v1/todo/items", "todo:update")),
        ("diary.entry.delete", "/api/v1/diary",
         ("diary_entry_delete", "DELETE", "/api/v1/diary/entrys", "diary:delete")),
    ],
)
def test_derive_tool_mechanical(provides, base, expect):
    t = derive_tool(provides, base, "someplugin")
    assert t is not None
    assert (t.name, t.method, t.path, t.scope) == expect


@pytest.mark.parametrize("bad", ["only.two", "a.b.unknownverb", ""])
def test_derive_tool_skips_invalid(bad):
    assert derive_tool(bad, "/api/v1/x", "p") is None


# ─────────────── 显式路由映射 api.tools（ISSUE-008 方案 A） ───────────────
@pytest.mark.parametrize(
    ("provides", "base", "tools", "expect_path"),
    [
        # dashboard 3 条：单数资源 / 语义命名（声明取自真实 manifest）
        ("dashboard.today.read", "/api/v1/dashboard",
         {"today": "/today", "system-health": "/health-of-system", "overview": "/overview"},
         "/api/v1/dashboard/today"),
        ("dashboard.system-health.read", "/api/v1/dashboard",
         {"today": "/today", "system-health": "/health-of-system", "overview": "/overview"},
         "/api/v1/dashboard/health-of-system"),
        ("dashboard.overview.read", "/api/v1/dashboard",
         {"today": "/today", "system-health": "/health-of-system", "overview": "/overview"},
         "/api/v1/dashboard/overview"),
        # resource 级共享：entry.read 与 entry.write 共用一条声明
        ("finance.entry.write", "/api/v1/finance", {"entry": "/entries"},
         "/api/v1/finance/entries"),
        # 未声明的 resource 走机械推导（显式映射不误伤别的 resource）
        ("finance.snapshot.read", "/api/v1/finance", {"entry": "/entries"},
         "/api/v1/finance/snapshots"),
        # route 值完整路径形态（脚本与实现兼容两种形态）
        ("finance.entry.read", "/api/v1/finance",
         {"entry": "/api/v1/finance/entries"}, "/api/v1/finance/entries"),
        # 未传 tools → 纯机械推导（常规 REST 插件零声明兼容，T18 哲学）
        ("todo.item.update", "/api/v1/todo", None, "/api/v1/todo/items"),
    ],
)
def test_derive_tool_explicit_routes(provides, base, tools, expect_path):
    t = derive_tool(provides, base, "someplugin", tools)
    assert t is not None
    assert t.path == expect_path


def test_tool_map_explicit_routes_end_to_end(client):
    """端到端：真实 manifest 上 11 条破缺全治 + habits_log_write 已删（26→25）。

    对表基准 = Qoder 判据帖（16:27）12 工具全表；habits.log.write 因唯一真实
    写端点为带路径参数的 checkin（MCP 无法直通转发）按「宁可少暴露」删除。
    依赖 client fixture：build_tool_map() 查 plugin_state，须先 init_engine +
    create_all（临时库），否则 no such table。
    """
    from modules.mcp.registry_adapter import build_tool_map

    tools = {t.name: t for t in build_tool_map()}
    assert tools["dashboard_today_read"].path == "/api/v1/dashboard/today"
    # ★ system-health 段含连字符：name 只替换 '.'，连字符保留（判据脚本同款口径）
    assert tools["dashboard_system-health_read"].path == (
        "/api/v1/dashboard/health-of-system")
    assert tools["dashboard_overview_read"].path == "/api/v1/dashboard/overview"
    assert tools["finance_entry_read"].path == "/api/v1/finance/entries"
    assert tools["finance_entry_write"].path == "/api/v1/finance/entries"
    assert tools["finance_beeccount_read"].path == "/api/v1/finance/beecount/source"
    assert tools["web_entry_read"].path == "/api/v1/web/entries"
    assert tools["web_entry_write"].path == "/api/v1/web/entries"
    assert tools["review_daily_read"].path == "/api/v1/review/days"
    assert tools["review_source_read"].path == "/api/v1/review/source"
    assert tools["calendar_slot_free"].path == "/api/v1/calendar/free-slots"
    # 机械推导命中组不受影响（照常）
    assert tools["calendar_event_write"].path == "/api/v1/calendar/events"
    # ★ 2026-09-27：habits_log_write **恢复暴露** —— 当初删它的唯一理由就是
    #   "写端点带 {habit_id}、MCP 无法直通"（T18 哲学：宁可少暴露）。
    #   桥接层支持路径参数代入后，那条理由消失了。
    assert tools["habits_log_write"].method == "POST"
    assert tools["habits_log_write"].path == "/api/v1/habits/{habit_id}/checkin"
    assert "habits_habit_read" in tools
    # ★ 2026-09-27：路径参数型端点成批打通（patch 动词 + 细粒度路由键 + 桥接层代入）
    assert tools["todo_item_patch"].method == "PATCH"
    assert tools["todo_item_patch"].path == "/api/v1/todo/items/{item_id}"
    assert tools["docs_node_patch"].path == "/api/v1/docs/nodes/{node_id}"
    assert tools["docs_node_delete"].method == "DELETE"
    assert tools["docs_node_delete"].path == "/api/v1/docs/nodes/{node_id}"
    # ★ 2026-09-28：标签汇总（AI 一览分类 / 前端动态标签条的数据源）
    assert tools["todo_tag_read"].method == "GET"
    assert tools["todo_tag_read"].path == "/api/v1/todo/tags"
    # 工具数量随模块扩展而增长（含 agents 模块新工具）
    assert len(tools) >= 25, f"当前 {len(tools)} 个工具，期望 ≥25"


# ───────────────── 改动型端点成批暴露（2026-10-04）─────────────────
# 背景：2026-09-27 那次「打通往路径参数型端点」只补了 docs/habits/todo 三个模块的
#   provides，其余 12 个模块的 PATCH/DELETE 端点**后端有、工具面没有** ——
#   AI 只能新建不能改/删（实证：改一条日程标题只能请主人手动点）。
# 纪律：manifest 的 api.tools 用**细粒度 `resource.verb` 键**（粗键 `resource` 会撞车：
#   同一 resource 上常挂多个端点且路径不同）。
_MUTATION_TOOLS = [
    # (工具名, 方法, 完整路径, scope)
    ("calendar_event_patch",   "PATCH",  "/api/v1/calendar/events/{event_id}",  "calendar:patch"),
    ("calendar_event_delete",  "DELETE", "/api/v1/calendar/events/{event_id}",  "calendar:delete"),
    ("todo_item_delete",       "DELETE", "/api/v1/todo/items/{item_id}",        "todo:delete"),
    ("habits_habit_patch",     "PATCH",  "/api/v1/habits/{habit_id}",           "habits:patch"),
    ("habits_habit_delete",    "DELETE", "/api/v1/habits/{habit_id}",           "habits:delete"),
    ("sidebar_item_patch",     "PATCH",  "/api/v1/sidebar/items/{item_id}",     "sidebar:patch"),
    ("sidebar_item_delete",    "DELETE", "/api/v1/sidebar/items/{item_id}",     "sidebar:delete"),
    ("web_entry_patch",        "PATCH",  "/api/v1/web/entries/{entry_id}",      "web:patch"),
    ("web_entry_delete",       "DELETE", "/api/v1/web/entries/{entry_id}",      "web:delete"),
    ("course_item_patch",      "PATCH",  "/api/v1/course/items/{item_id}",      "course:patch"),
    ("course_item_delete",     "DELETE", "/api/v1/course/items/{item_id}",      "course:delete"),
    ("finance_entry_patch",    "PATCH",  "/api/v1/finance/entries/{entry_id}",  "finance:patch"),
    ("finance_entry_delete",   "DELETE", "/api/v1/finance/entries/{entry_id}",  "finance:delete"),
    ("health_record_patch",    "PATCH",  "/api/v1/health/records/{record_id}",  "health:patch"),
    ("health_record_delete",   "DELETE", "/api/v1/health/records/{record_id}",  "health:delete"),
    ("diary_entry_patch",      "PATCH",  "/api/v1/diary/entry/{node_id}",       "diary:patch"),
    ("diary_entry_delete",     "DELETE", "/api/v1/diary/entry/{node_id}",       "diary:delete"),
    ("push_subscribe_delete",  "DELETE", "/api/v1/push/subscribe",              "push:delete"),
    ("review_note_delete",     "DELETE", "/api/v1/review/notes/{note_id}",      "review:delete"),
    ("agents_agent_patch",     "PATCH",  "/api/v1/agents/agents/{agent_id}",    "agents:patch"),
    ("agents_agent_delete",    "DELETE", "/api/v1/agents/agents/{agent_id}",    "agents:delete"),
    ("agents_task_patch",      "PATCH",  "/api/v1/agents/tasks/{task_id}",      "agents:patch"),
    ("agents_task_delete",     "DELETE", "/api/v1/agents/tasks/{task_id}",      "agents:delete"),
]


def test_mutation_tools_exposed(client):
    """改动型端点成批可达：PATCH/DELETE 不再缺席工具面。

    ★ 这条测试的意义：**照见「测试全绿 ≠ 改动生效」**。上一版测试只断言
      若干写工具存在，于是「12 个模块的删改端点整类不可达」在 42 个测试
      全绿的情况下潜伏了 7 天。这里逐条钉死方法 + 完整路径 + scope。
    """
    from modules.mcp.registry_adapter import build_tool_map

    tools = {t.name: t for t in build_tool_map()}
    missing = [n for n, *_ in _MUTATION_TOOLS if n not in tools]
    assert not missing, f"以下改动型工具未暴露：{missing}"

    for name, method, path, scope in _MUTATION_TOOLS:
        t = tools[name]
        assert t.method == method, f"{name} 方法应为 {method}，实际 {t.method}"
        assert t.path == path, f"{name} 路径应为 {path}，实际 {t.path}"
        assert t.scope == scope, f"{name} scope 应为 {scope}，实际 {t.scope}"

    # 反向对照：路径**模板**允许含 `{param}`（forward 层运行时代入），
    # 但要防真正的推导事故 —— 空路径段（`//`，说明拼错了）与未闭合花括号。
    # ★ 2026-10-04 自纠：上一版这里是**手工白名单 replace**（把已知参数名逐个
    #   抠掉再看有没有残留 `{`）—— 新增一个路径参数（如 `{child_id}`）就会误报。
    #   那是我自己写的测试在自伤（坑谱 #66）。改成结构性判据，一劳永逸。
    for t in tools.values():
        assert "//" not in t.path.replace("://", ""), \
            f"{t.name} 路径含空段（拼接事故）：{t.path}"
        assert t.path.count("{") == t.path.count("}"), \
            f"{t.name} 路径花括号不配对：{t.path}"
        for seg in t.path.split("/"):
            if "{" in seg:
                assert re.fullmatch(r"\{[a-z][a-z0-9_]*\}", seg), \
                    f"{t.name} 路径参数段写法异常：{seg}"


def test_mutation_endpoints_match_router_truth(client):
    """对表：每条改动型工具的 (方法, 路径) 必须在真实 router 里存在。

    ★ 这是**双向验证**（坑谱 #67(d)）：只查工具面存在 = 自己证明自己；
      这里回到模块 OpenAPI（内核已暴露 `/api/{module}/openapi.json`）
      核对端点真身，防止「工具面有、后端无」的幽灵工具。
    """
    import httpx
    from modules.mcp.registry_adapter import build_tool_map

    tools = {t.name: t for t in build_tool_map()}
    seen: set[tuple[str, str]] = set()
    for name, method, path, _scope in _MUTATION_TOOLS:
        module = name.split("_")[0]
        try:
            spec = client.get(f"/api/{module}/openapi.json").json()
        except Exception:  # noqa: BLE001 —— 拿不到就跳过（内核版本差异，不误报）
            continue
        paths = spec.get("paths") or {}
        seen.add((method, path))
        assert path in paths, f"{name}: 后端 OpenAPI 里没有 {path}"
        ops = {k.lower() for k in paths[path]}
        assert method.lower() in ops, f"{name}: {path} 不支持 {method}，只有 {sorted(ops)}"
    assert len(seen) >= 20, f"对表只覆盖了 {len(seen)} 条，期望 ≥20"


# ───────────────────────── PAT 生命周期 ─────────────────────────
def test_pat_create_returns_plaintext_once(client):
    r = client.post(
        "/api/v1/mcp/pats", json={"name": "一次性", "scopes": ["calendar:read"]},
        headers=AUTH,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["token"].startswith(PAT_PREFIX)
    # 列表里绝无明文
    r2 = client.get("/api/v1/mcp/pats", headers=AUTH)
    names_and_prefixes = json.dumps(r2.json(), ensure_ascii=False)
    assert body["token"] not in names_and_prefixes
    assert body["token_prefix"] in names_and_prefixes
    # 库里也绝无明文（SELECT 证明无明文列）
    from db.engine import get_engine as ge

    engine = ge()
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT name FROM pragma_table_info('mcp_pat')"
        ).fetchall()
    cols = {row[0] for row in rows}
    assert "token_hash" in cols and "token_prefix" in cols
    assert "token" not in cols and "plaintext" not in cols
    # 哈希对得上、明文对不上
    with engine.connect() as conn:
        hashes = conn.exec_driver_sql("SELECT token_hash FROM mcp_pat").fetchall()
    assert hashlib.sha256(body["token"].encode()).hexdigest() in {h[0] for h in hashes}


def test_pat_revoke_then_401(client, pat):
    plain, headers, pid = pat
    # 吊销前能用
    r = client.post(
        "/api/v1/mcp",
        json=_rpc("initialize", {"protocolVersion": "2025-06-18"}),
        headers=headers,
    )
    assert r.status_code == 200, r.text
    # 吊销（幂等：连吊两次）
    assert client.delete(f"/api/v1/mcp/pats/{pid}", headers=AUTH).status_code == 204
    assert client.delete(f"/api/v1/mcp/pats/{pid}", headers=AUTH).status_code == 204
    # 吊销后立即 401
    r2 = client.post("/api/v1/mcp", json=_rpc("initialize", {}), headers=headers)
    assert r2.status_code == 401
    assert r2.headers["content-type"].startswith("application/problem+json")


def test_pat_patch_scopes(client, pat):
    _, headers, pid = pat
    r = client.patch(f"/api/v1/mcp/pats/{pid}", json={"scopes": ["notes:read"]}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["scopes"] == ["notes:read"]


# ───────────────────────── MCP 端点鉴权 ─────────────────────────
def test_mcp_requires_authorization(client):
    r = client.post("/api/v1/mcp", json=_rpc("initialize", {}))
    assert r.status_code == 401


def test_mcp_rejects_user_jwt(client):
    """用户 JWT 不能打 MCP 端点 —— AI 与人用不同凭证（ADR-0003 §2.3）。"""
    r = client.post("/api/v1/mcp", json=_rpc("initialize", {}), headers=AUTH)
    assert r.status_code == 401
    assert "PAT" in r.json()["detail"]


def test_mcp_rejects_fake_pat(client):
    r = client.post(
        "/api/v1/mcp", json=_rpc("initialize", {}),
        headers={"Authorization": f"Bearer {PAT_PREFIX}notarealtoken"},
    )
    assert r.status_code == 401


# ───────────────────────── 协议三原语 ─────────────────────────
def test_initialize(client, pat):
    _, headers, _ = pat
    r = client.post(
        "/api/v1/mcp",
        json=_rpc("initialize", {"protocolVersion": "2025-03-26"}),
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["protocolVersion"] == "2025-03-26"
    assert "tools" in body["result"]["capabilities"]
    assert body["result"]["serverInfo"]["name"] == "alongside-mcp"


def test_notifications_return_202(client, pat):
    _, headers, _ = pat
    r = client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=headers,
    )
    assert r.status_code == 202
    assert r.content == b""


def test_tools_list_contains_calendar(client, pat):
    """磁盘上 calendar 的 manifest 有 provides → tools/list 必须出现（不重启）。"""
    _, headers, _ = pat
    r = client.post("/api/v1/mcp", json=_rpc("tools/list"), headers=headers)
    assert r.status_code == 200
    names = [t["name"] for t in r.json()["result"]["tools"]]
    assert "calendar_event_write" in names
    assert "calendar_event_read" in names
    assert "calendar_slot_free" in names


def test_unknown_method_rpc_error(client, pat):
    _, headers, _ = pat
    r = client.post("/api/v1/mcp", json=_rpc("no/such/method"), headers=headers)
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32601


def test_unknown_tool_rpc_error_and_audited(client, pat):
    _, headers, _ = pat
    r = client.post(
        "/api/v1/mcp",
        json=_rpc("tools/call", {"name": "ghost_tool_write", "arguments": {}}),
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32602
    # 拒绝也要留痕
    logs = client.get("/api/v1/mcp/audit-logs", headers=AUTH).json()
    assert any(entry["action"] == "denied" and "ghost" in entry["target"] for entry in logs)


def test_tools_call_scope_denied_403(client):
    """PAT 只有 calendar:read → 调写工具 calendar_event_write → 403 写明缺哪个 scope。"""
    r = client.post(
        "/api/v1/mcp/pats",
        json={"name": "只读-PAT", "scopes": ["calendar:read"]},
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    r2 = client.post(
        "/api/v1/mcp",
        json=_rpc("tools/call", {"name": "calendar_event_write", "arguments": {}}),
        headers=headers,
    )
    assert r2.status_code == 403
    body = r2.json()
    assert "calendar:write" in body["detail"]


def test_tools_call_forwards_via_http(client, pat, monkeypatch):
    """成功路径：scope 过 → 内部 HTTP 转发（mock）→ 写类工具落审计。"""
    _, headers, _ = pat
    seen: dict = {}

    def fake_forward(method, path, payload, transport=None):
        seen.update(method=method, path=path, payload=payload)
        return 201, {"id": "abc123", "title": "被 AI 写进来的"}

    monkeypatch.setattr(mcp_server, "forward", fake_forward)
    payload = {"title": "被 AI 写进来的", "start_at": "2026-09-20T08:00:00+08:00",
               "end_at": "2026-09-20T09:00:00+08:00"}
    r = client.post(
        "/api/v1/mcp",
        json=_rpc("tools/call", {"name": "calendar_event_write",
                                 "arguments": {"payload": payload}}),
        headers=headers,
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["isError"] is False
    assert seen["method"] == "POST" and seen["path"] == "/api/v1/calendar/events"
    assert seen["payload"] == payload
    body = json.loads(result["content"][0]["text"])
    assert body["status"] == 201 and body["body"]["id"] == "abc123"
    # 写类工具 → audit_log 有 mcp 记录
    logs = client.get("/api/v1/mcp/audit-logs", headers=AUTH).json()
    assert any(e["action"] == "calendar_event_write" for e in logs)


def test_forward_mock_transport(monkeypatch):
    """forward 直测：MockTransport 注入（GET→params / POST→json）。"""
    from modules.mcp.forward import forward

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"echo_path": request.url.path,
                                         "q": dict(request.url.params)})

    code, body = forward("GET", "/api/v1/x/things", {"a": "1"},
                         transport=httpx.MockTransport(handler))
    assert code == 200 and body["q"]["a"] == "1"
    code, body = forward("GET", "/api/v1/x/things", None,
                         transport=httpx.MockTransport(handler))
    assert code == 200


def test_forward_marks_mcp_origin(monkeypatch):
    """★ 2026-10-02（主人「mcp 创建待办强制加标签」）：转发必须打来路标记。

    这是那条硬约束的**另一半** —— 插件侧（todo router）靠 `X-Alongside-Client: mcp`
    区分「AI 来路」与「人的来路」。少打这个头，插件的强制校验永远不会触发，
    AI 又能静默建出无标签条目（护栏形同虚设，且**悄无声息**）。
    故在此把「头确实发出去了」钉死。
    """
    from core.mcp_writes import CLIENT_MCP, HEADER
    from modules.mcp.forward import forward

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["client"] = request.headers.get(HEADER)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={})

    code, _ = forward("POST", "/api/v1/x/things", {"a": "1"},
                      transport=httpx.MockTransport(handler))
    assert code == 200
    assert seen["client"] == CLIENT_MCP
    # 身份依旧是内核 service JWT（来路标记是**附加**，不改原有鉴权口径）
    assert seen["auth"].startswith("Bearer ")


# ───────────────────────── ★ 模块洁癖自检 ─────────────────────────
def test_mcp_module_purity():
    """modules/mcp/ 源码零业务词、零业务插件 import。

    与 scripts/check_kernel_purity.py 同款思路（AST 层，只看代码不看注释），
    但不改那个脚本（SCAN_ROOTS 扩围另走 issues 提卡）——先在本模块自守。
    docstring 里解释映射规则用的示例（如 calendar.event.write）不算违规——
    与内核洁癖检查器"剥掉注释与文档字符串"同一口径；但**代码与字符串字面量**
    不得出现业务词。
    """
    business_words = ("calendar", "todo", "notes", "finance", "habit",
                      "beecount", "obsidian", "diary")
    module_dir = Path(__file__).resolve().parents[1] / "modules" / "mcp"
    offenders: list[str] = []

    for py in sorted(module_dir.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        src = py.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(py))

        # 收集字符串常量占据的行号范围（docstring 也在其中，一并排除出行检查）；
        # 但除 docstring 外的字符串常量仍要做词检查 —— 先记范围，再按节点判断。
        string_line_ranges: list[tuple[int, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                string_line_ranges.append((node.lineno, node.end_lineno or node.lineno))

        def _in_string(lineno: int, _ranges=string_line_ranges) -> bool:
            return any(a <= lineno <= b for a, b in _ranges)

        def _is_modules_import(name: str) -> bool:
            return name.startswith("modules.") and not name.startswith("modules.mcp")

        for node in ast.walk(tree):
            # 1) 标识符不得含业务词
            if isinstance(node, ast.Name) and node.id.lower() in business_words:
                offenders.append(f"{py.name}:{node.lineno} 标识符 {node.id}")
            # 2) 字符串字面量（非 docstring 部分）不得含业务词。
            #    docstring 判定：位于模块/函数/类的 body 首位。
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _is_docstring(node, tree):
                    continue
                v = node.value.lower()
                for w in business_words:
                    if w in v:
                        offenders.append(f"{py.name}:{node.lineno} 字符串 {node.value!r}")
            # 3) 严禁 import 其他插件模块
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_modules_import(alias.name):
                        offenders.append(f"{py.name}:{node.lineno} import {alias.name}")
            if isinstance(node, ast.ImportFrom) and node.module and _is_modules_import(node.module):
                offenders.append(f"{py.name}:{node.lineno} from {node.module}")

        # 4) 代码行（去注释、去字符串行）不得含业务词 —— 兜住 f-string 片段等
        for i, line in enumerate(src.splitlines(), 1):
            if _in_string(i):
                continue
            code = line.split("#")[0].lower()
            for w in business_words:
                if w in code:
                    offenders.append(f"{py.name}:{i} 代码行含业务词 {w}")

    assert not offenders, "mcp 模块洁癖违规：\n" + "\n".join(offenders)


def _is_docstring(node: ast.Constant, tree: ast.AST) -> bool:
    """判定字符串常量是否是模块/类/函数的 docstring。"""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    owner = parents.get(id(node))
    if owner is None or not isinstance(owner, ast.Expr):
        return False
    body_owner = parents.get(id(owner))
    if body_owner is None:
        return False
    body = getattr(body_owner, "body", None)
    if not body:
        return False
    return body[0] is owner


# ───── 路径参数型端点（2026-09-27 主人「把挂路径参数的整类端点修通」）─────
def test_derive_tool_patch_verb_and_fine_grained_key():
    """patch → PATCH（此前无动词映射 PATCH）；路由键先查 resource.verb 再回落。"""
    routes = {"item": "/items", "item.patch": "/items/{item_id}"}
    t = derive_tool("todo.item.patch", "/api/v1/todo", "todo", routes)
    assert t is not None
    assert (t.method, t.path, t.scope) == (
        "PATCH", "/api/v1/todo/items/{item_id}", "todo:patch")
    # 同一 resource 的另一条端点回落 resource 键，不被细粒度键误伤
    w = derive_tool("todo.item.write", "/api/v1/todo", "todo", routes)
    assert (w.method, w.path) == ("POST", "/api/v1/todo/items")
    # 旧 manifest（只有 resource 键）行为完全不变
    old = derive_tool("todo.item.write", "/api/v1/todo", "todo", {"item": "/items"})
    assert old is not None and old.path == "/api/v1/todo/items"


def test_patch_counts_as_write_verb_for_audit():
    """patch 属写类 → 必须落审计（否则"改了数据不留痕"）。"""
    from modules.mcp.registry_adapter import is_write_tool

    t = derive_tool("docs.node.patch", "/api/v1/docs", "docs",
                    {"node.patch": "/nodes/{node_id}"})
    assert t is not None and is_write_tool(t) is True


def test_forward_substitutes_path_params():
    """{node_id} 从 payload 代入 URL，并从 body 摘掉（不再打一个字面量 {node_id}）。"""
    from modules.mcp.forward import forward

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content or b"{}")
        return httpx.Response(200, json={"ok": True})

    code, _ = forward("PATCH", "/api/v1/docs/nodes/{node_id}",
                      {"node_id": "abc123", "name": "新名字"},
                      transport=httpx.MockTransport(handler))
    assert code == 200
    assert seen["path"] == "/api/v1/docs/nodes/abc123"
    assert seen["body"] == {"name": "新名字"}  # 路径参数已摘除，不污染请求体


def test_forward_path_param_is_escaped():
    """参数值里的 / 必须转义，否则能拼出跨段路径（越权面）。"""
    from modules.mcp.forward import forward

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # ★ 必须看 raw_path：url.path 会被 httpx **解码**回 "a/b c"，
        #   那时断言就变成"看不出转义有没有生效"了。
        seen["raw"] = request.url.raw_path
        return httpx.Response(200, json={})

    forward("GET", "/api/v1/x/{k}", {"k": "a/b c"},
            transport=httpx.MockTransport(handler))
    assert seen["raw"] == b"/api/v1/x/a%2Fb%20c"


def test_forward_missing_path_param_is_422():
    """缺路径参数 → 明确 422（而不是打出去 404 让人猜）。"""
    from modules.mcp.forward import forward

    code, body = forward("PATCH", "/api/v1/docs/nodes/{node_id}", {"name": "x"},
                         transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert code == 422
    assert "node_id" in json.dumps(body, ensure_ascii=False)


def test_openapi_params_exposes_path_params():
    """路径参数要进 schema 且标必填（旧版直接跳过，AI 无从知道该填什么）。"""
    from modules.mcp.openapi_params import _props_from_operation

    spec = {"components": {"schemas": {
        "BodyIn": {"properties": {"name": {"type": "string"}}}}}}
    op = {
        "summary": "改一个节点的名字",
        "description": "改一个节点的名字。\n支持改名 / 移动 / 改 meta_json。",
        "parameters": [{"name": "node_id", "in": "path", "required": True,
                        "schema": {"type": "string"}, "description": "节点 id"}],
        "requestBody": {"content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/BodyIn"}}}},
    }
    entry = _props_from_operation(spec, op)
    assert "node_id" in entry["properties"]
    assert "路径参数" in entry["properties"]["node_id"]["description"]
    assert "node_id" in entry["required"]
    assert entry["properties"]["name"]["type"] == "string"  # $ref 解析照常
    # ★ 2026-09-28：端点的**作者注释**要带出来 —— MCP 工具描述就是拿它当解释，
    #   否则 44 个工具的描述长得一模一样，AI 只能猜（主人原话「抓瞎」）。
    assert entry["doc"].startswith("改一个节点的名字")
    assert "支持改名" in entry["doc"]


def test_tool_description_prefers_endpoint_doc():
    """工具描述优先用端点作者注释；拉不到就回落机械句（绝不空、绝不报错）。"""
    from modules.mcp.mcp_server import _tool_description
    from modules.mcp.registry_adapter import ToolMapping

    t = ToolMapping(name="x_y_read", verb="read", method="GET",
                    path="/api/v1/x/y", scope="x:read",
                    plugin_id="x", description="机械句")
    s = _tool_description(t)
    # 测试环境没有内网服务 → 必然回落，但**必须非空**
    assert s and isinstance(s, str)
    # 机械句兜底时不能是空串（旧行为不能被破坏）
    assert _tool_description(None) == ""


def test_openapi_params_ignores_autogenerated_summary():
    """★ 回归：FastAPI 无 docstring 时会用**函数名**自动生成英文标题当 summary
    （`def update_item` → "Update Item"）。那**不是解释** —— 首版内核把它当解释用了，
    结果 todo_item_patch 的工具描述变成 "Update Item"，比机械句更误导（实测踩过）。
    判据：只认 docstring 派生的 description，或**含中文**的 summary。
    """
    from modules.mcp.openapi_params import _props_from_operation

    spec: dict = {}
    prm = [{"name": "q", "in": "query", "schema": {"type": "string"}}]
    # ① 只有自动生成的英文标题 → 不许当解释
    assert "doc" not in _props_from_operation(spec, {"summary": "Update Item", "parameters": prm})
    # ② 作者手写的中文 summary → 采信
    assert _props_from_operation(spec, {"summary": "改一条待办", "parameters": prm}).get("doc") == "改一条待办"
    # ③ description（docstring 派生）优先，且优先于 summary
    both = {"summary": "Update Item", "description": "改一条待办。\n支持改标签。", "parameters": prm}
    assert _props_from_operation(spec, both)["doc"].startswith("改一条待办。")


def test_tools_call_accepts_top_level_named_arguments(client, pat, monkeypatch):
    """★ 2026-09-28 实测抓到的真 bug 的回归：schema 声明的是**具名参数**，
    但桥接层历史上只读 `payload` ⇒ 顶层具名参数被**静默丢弃**。
    实测现场：calendar_event_read 传 {"from":…, "to":…} → 422「query from Field required」。
    本用例守「具名直传」与「payload 包裹」两种写法都能到 forward。
    """
    _, headers, _ = pat
    seen: dict = {}

    def fake_forward(method, path, payload, transport=None):
        seen.update(method=method, path=path, payload=payload)
        return 200, []

    monkeypatch.setattr(mcp_server, "forward", fake_forward)
    # 写法一：具名直传（AI 照 schema 的自然写法）
    r = client.post("/api/v1/mcp", json=_rpc("tools/call", {
        "name": "calendar_event_read",
        "arguments": {"from": "2026-09-01T00:00:00+08:00", "to": "2026-09-30T00:00:00+08:00", "flat": True},
    }), headers=headers)
    assert r.status_code == 200, r.text
    assert seen["payload"] == {"from": "2026-09-01T00:00:00+08:00",
                               "to": "2026-09-30T00:00:00+08:00", "flat": True}
    # 写法二：payload 包裹（旧写法，必须零影响）
    seen.clear()
    r = client.post("/api/v1/mcp", json=_rpc("tools/call", {
        "name": "calendar_event_read",
        "arguments": {"payload": {"from": "2026-09-01T00:00:00+08:00", "to": "2026-09-30T00:00:00+08:00"}},
    }), headers=headers)
    assert r.status_code == 200, r.text
    assert seen["payload"]["from"] == "2026-09-01T00:00:00+08:00"
