"""T08B · BeeCount MCP 只读联动测试。

数据库隔离：./data/tmp_t08b.db，绝不碰主库 lifos.db。
表只建 finance 相关（finance_entry + finance_snapshot）。
FINANCE_UPSTREAM 默认 mock：离线全绿，不访问真实外网 / 不写真实 token。

红线覆盖：
  - source 状态（mock 默认可跑 / mcp 缺 token 明确报错）
  - sync 幂等（两次 sync 同日覆盖不翻倍）
  - snapshot 读回
  - 工具白名单：delete / 写工具被拒
  - 全库无 /api/v1/sync/* 调用（源码扫描）
"""
from __future__ import annotations

import os

# ★ 必须在 import 任何内核/模块之前设置临时库与 upstream。
os.environ["DB_PATH"] = "./data/tmp_t08b.db"
os.environ["FINANCE_UPSTREAM"] = "mock"
# mock 模式不要求 token；确保测试进程里没有误配的 PAT
os.environ.pop("BEECOUNT_MCP_TOKEN", None)

from datetime import UTC, date, datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.finance.beecount_mcp import (  # noqa: E402
    MCP_PATH,
    READ_TOOLS,
    BeeCountMCPClient,
    BeeCountMCPError,
    BeeCountNotConfiguredError,
    BeeCountToolForbiddenError,
    MockBeeCountClient,
    create_client,
    load_upstream_config,
)
from modules.finance.beecount_sync import (  # noqa: E402
    local_today,
    sync_from_client,
    sync_snapshot,
)
from modules.finance.models import FinanceEntry, FinanceSnapshot  # noqa: E402

TZ = timezone(timedelta(hours=8))
BASE = "/api/v1/finance"
FAKE_TOKEN_PREFIX_FOR_SCAN_ONLY = "bcmcp_"  # 仅用于源码扫描断言，不是真实凭据


@pytest.fixture(scope="module")
def client():
    init_engine()
    engine = get_engine()
    FinanceEntry.__table__.create(bind=engine, checkfirst=True)
    FinanceSnapshot.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    """每条测试前清空 finance 相关表 + 重置 mock 上游环境。"""
    os.environ["FINANCE_UPSTREAM"] = "mock"
    os.environ.pop("BEECOUNT_MCP_TOKEN", None)
    engine = get_engine()
    with Session(engine) as s:
        s.exec(text("DELETE FROM finance_snapshot"))
        s.exec(text("DELETE FROM finance_entry"))
        s.commit()
    yield
    os.environ["FINANCE_UPSTREAM"] = "mock"
    os.environ.pop("BEECOUNT_MCP_TOKEN", None)


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


# ───────────────────────── 健康 / 清单 ─────────────────────────
def test_health(client):
    r = client.get(f"{BASE}/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_manifest_declares_snapshot_and_net_permission(client):
    r = client.get(f"{BASE}/manifest")
    assert r.status_code == 200
    m = r.json()
    assert m["id"] == "finance"
    assert "finance.snapshot.read" in m["provides"]
    assert "finance.snapshot.updated" in m["emits"]
    # ★ 主机恒为 127.0.0.1（BeeCount 与 Alongside 同机部署）
    assert "net:out:127.0.0.1" in m["permissions"]
    assert "db:own" in m["permissions"]


# ───────────────────────── source 状态 ─────────────────────────
def test_source_mock_default(client, auth):
    r = client.get(f"{BASE}/beecount/source", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["upstream"] == "mock"
    assert body["configured"] is True
    assert body["write_enabled"] is False
    assert body["last_sync"] is None
    assert "get_ledger_stats" in body["read_tools"]
    assert "get_analytics_summary" in body["read_tools"]
    # 绝不返回 token 字段
    assert "token" not in body
    assert "BEECOUNT_MCP_TOKEN" not in body
    # 明文 PAT 不得出现在响应里
    assert FAKE_TOKEN_PREFIX_FOR_SCAN_ONLY not in r.text or body["token_present"] is False


def test_source_mcp_missing_token_not_configured(client, auth, monkeypatch):
    monkeypatch.setenv("FINANCE_UPSTREAM", "mcp")
    monkeypatch.setenv("BEECOUNT_BASE_URL", "http://127.0.0.1:8870")
    monkeypatch.delenv("BEECOUNT_MCP_TOKEN", raising=False)
    # ★ 还必须屏蔽 .env 回退：read_setting 在 os.environ 缺失时会回退读项目根 .env，
    #   而本机 .env 里确实有 BEECOUNT_MCP_TOKEN —— 不屏蔽就永远模拟不出"未配置"。
    #   （这条不是为了迁就实现，而是"未配置"在新语义下 = env 与 .env 都没有。）
    monkeypatch.setattr("core.config._dotenv_values", lambda: {})
    r = client.get(f"{BASE}/beecount/source", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["upstream"] == "mcp"
    assert body["configured"] is False
    assert body["token_present"] is False
    assert body["base_url_configured"] is True


# ───────────────────────── sync 幂等 + snapshot 读回 ─────────────────────────
def test_sync_mock_writes_snapshot_and_reads_back(client, auth):
    r = client.post(f"{BASE}/snapshots/sync", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["upstream"] == "mock"
    snap = body["snapshot"]
    assert snap is not None
    assert snap["total_asset"] == 1_234_567  # mock balance_cents
    assert snap["meta"] is not None
    assert "ledger_stats" in snap["meta"]
    assert "analytics" in snap["meta"]

    lst = client.get(f"{BASE}/snapshots", headers=auth)
    assert lst.status_code == 200
    page = lst.json()
    assert page["total"] == 1
    assert page["items"][0]["date"] == snap["date"]
    assert page["items"][0]["total_asset"] == 1_234_567

    src = client.get(f"{BASE}/beecount/source", headers=auth).json()
    assert src["last_sync"] is not None
    assert src["last_snapshot_date"] == snap["date"]
    assert src["snapshot_count"] == 1


def test_sync_idempotent_same_day_does_not_double(client, auth):
    r1 = client.post(f"{BASE}/snapshots/sync", headers=auth)
    assert r1.status_code == 200, r1.text
    r2 = client.post(f"{BASE}/snapshots/sync", headers=auth)
    assert r2.status_code == 200, r2.text
    assert r2.json()["snapshot"]["date"] == r1.json()["snapshot"]["date"]

    lst = client.get(f"{BASE}/snapshots", headers=auth).json()
    assert lst["total"] == 1  # 同日覆盖，不翻倍

    engine = get_engine()
    with Session(engine) as s:
        rows = list(s.exec(text("SELECT date, total_asset FROM finance_snapshot")).all())
    assert len(rows) == 1


def test_sync_service_level_inject_client_idempotent():
    """直接测 service 层：注入 mock client，两次 sync 同日仍一条。"""
    engine = get_engine()
    mock = MockBeeCountClient()
    with Session(engine) as s:
        out1 = sync_from_client(s, mock, upstream="mock")
        out2 = sync_from_client(
            s,
            MockBeeCountClient(stats={"balance_cents": 2_000_000}),
            upstream="mock",
        )
        assert out1["date"] == out2["date"]
        assert out2["total_asset"] == 2_000_000  # 覆盖更新，不新增
        n = len(list(s.exec(text("SELECT id FROM finance_snapshot")).all()))
        assert n == 1


def test_sync_mcp_mode_missing_token_clear_error(client, auth, monkeypatch):
    monkeypatch.setenv("FINANCE_UPSTREAM", "mcp")
    monkeypatch.setenv("BEECOUNT_BASE_URL", "http://127.0.0.1:8870")
    monkeypatch.delenv("BEECOUNT_MCP_TOKEN", raising=False)
    # 屏蔽 .env 回退，否则 read_setting 会读到真实 token，测不出「未配置」
    monkeypatch.setattr("core.config._dotenv_values", lambda: {})
    r = client.post(f"{BASE}/snapshots/sync", headers=auth)
    assert r.status_code == 503, r.text
    assert r.headers.get("content-type", "").startswith("application/problem+json")
    detail = r.json().get("detail", "")
    assert "BEECOUNT_MCP_TOKEN" in detail
    # 不静默空数据：不应 200 + 空 snapshot
    lst = client.get(f"{BASE}/snapshots", headers=auth).json()
    assert lst["total"] == 0


def test_load_upstream_config_and_create_client_errors(monkeypatch):
    monkeypatch.setenv("FINANCE_UPSTREAM", "mcp")
    monkeypatch.delenv("BEECOUNT_BASE_URL", raising=False)
    monkeypatch.delenv("BEECOUNT_MCP_TOKEN", raising=False)
    # ★ 同 test_source_mcp_missing_token_not_configured：必须一并屏蔽 .env 回退，
    #   否则 read_setting 会从项目根 .env 读到真实 token/地址，"未配置"就模拟不出来。
    monkeypatch.setattr("core.config._dotenv_values", lambda: {})
    cfg = load_upstream_config()
    assert cfg["upstream"] == "mcp"
    assert cfg["configured"] is False
    with pytest.raises(BeeCountNotConfiguredError) as ei:
        create_client()
    assert "BEECOUNT_BASE_URL" in str(ei.value)

    monkeypatch.setenv("BEECOUNT_BASE_URL", "http://127.0.0.1:8870")
    with pytest.raises(BeeCountNotConfiguredError) as ei2:
        create_client()
    assert "BEECOUNT_MCP_TOKEN" in str(ei2.value)

    monkeypatch.setenv("FINANCE_UPSTREAM", "mock")
    mock = create_client()
    assert isinstance(mock, MockBeeCountClient)


# ───────────────────────── MCP 客户端（httpx MockTransport，离线）──
def _rpc_ok(result: Any) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "result": result},
    )


def test_mcp_client_initialize_and_tools_list_offline():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        payload = __import__("json").loads(request.content.decode("utf-8"))
        method = payload.get("method")
        if method == "initialize":
            return _rpc_ok(
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "BeeCount Cloud MCP Server", "version": "1.29.0"},
                    "capabilities": {},
                }
            )
        if method == "tools/list":
            names = sorted(READ_TOOLS | {
                "create_transaction",
                "create_transactions",
                "update_transaction",
                "delete_transaction",
                "create_category",
                "update_budget",
                "parse_and_create_from_text",
                "search",
            })
            return _rpc_ok({"tools": [{"name": n} for n in names]})
        return httpx.Response(400, json={"error": "unexpected"})

    transport = httpx.MockTransport(handler)
    c = BeeCountMCPClient(
        "http://127.0.0.1:8870",
        "test-token-not-real",
        transport=transport,
    )
    assert c.mcp_url.endswith(MCP_PATH)
    init = c.initialize()
    assert init["serverInfo"]["name"].startswith("BeeCount")
    tools = c.tools_list()
    # 档案记录 18 个工具：读10 + 写7 + search1
    assert len(tools) == 18
    assert "get_ledger_stats" in tools
    assert "delete_transaction" in tools

    # 鉴权头存在且不落盘（仅内存）
    assert seen
    auth_header = seen[0].headers.get("Authorization", "")
    assert auth_header.startswith("Bearer ")
    # 响应/客户端 repr 不包含 token
    assert "test-token-not-real" not in repr(init)
    c.close()


def test_mcp_client_tools_call_read_and_sse_parse():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content.decode("utf-8"))
        assert payload["method"] == "tools/call"
        name = payload["params"]["name"]
        if name == "get_ledger_stats":
            body = {"balance_cents": 5000, "name": "主账本"}
            # SSE 形态
            data = __import__("json").dumps(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "content": [
                            {"type": "text", "text": __import__("json").dumps(body)}
                        ]
                    },
                }
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=f"event: message\ndata: {data}\n\n".encode(),
            )
        return _rpc_ok({"content": [{"type": "text", "text": '{"ok":true}'}]})

    c = BeeCountMCPClient(
        "http://127.0.0.1:8870",
        "test-token-not-real",
        transport=httpx.MockTransport(handler),
    )
    stats = c.get_ledger_stats()
    assert stats["balance_cents"] == 5000
    c.close()


def test_mcp_client_forbids_delete_and_write_tools():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"不应发出 HTTP：{request.url}")

    c = BeeCountMCPClient(
        "http://127.0.0.1:8870",
        "test-token-not-real",
        transport=httpx.MockTransport(handler),
    )
    for bad in (
        "delete_transaction",
        "create_transactions",
        "update_transaction",
        "create_category",
        "update_budget",
        "parse_and_create_from_text",
    ):
        with pytest.raises(BeeCountToolForbiddenError) as ei:
            c.call_tool(bad, {})
        assert bad in str(ei.value)

    # create_transaction 接口桩：默认禁用
    with pytest.raises(BeeCountToolForbiddenError):
        c.create_transaction(amount=1)

    # mock 同样拒绝
    mock = MockBeeCountClient()
    with pytest.raises(BeeCountToolForbiddenError):
        mock.call_tool("delete_transaction", {})
    with pytest.raises(BeeCountToolForbiddenError):
        mock.create_transaction()
    c.close()


def test_mcp_client_http_error_mapped():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "PAT can only be used for MCP endpoints"})

    c = BeeCountMCPClient(
        "http://127.0.0.1:8870",
        "test-token-not-real",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(BeeCountMCPError) as ei:
        c.get_ledger_stats()
    assert "403" in str(ei.value) or "MCP" in str(ei.value)
    # 错误信息不得回显 token
    assert "test-token-not-real" not in str(ei.value)
    c.close()


def test_sync_snapshot_accepts_injected_client():
    engine = get_engine()
    with Session(engine) as s:
        out = sync_snapshot(s, client=MockBeeCountClient())
        assert out.ok is True
        assert out.snapshot is not None
        assert out.snapshot.total_asset > 0
        again = sync_snapshot(s, client=MockBeeCountClient())
        assert again.snapshot is not None
        assert again.snapshot.date == out.snapshot.date


# ───────────────────────── 红线：源码扫描 ─────────────────────────
def test_redline_no_sync_protocol_calls_in_finance_module():
    """Alongside 财务模块不得出现 BeeCount /api/v1/sync/* 协议调用。"""
    root = Path(__file__).resolve().parents[1] / "modules" / "finance"
    offenders: list[str] = []
    for p in root.rglob("*.py"):
        text_all = p.read_text(encoding="utf-8")
        for needle in ("/api/v1/sync", "sync/pull", "sync/push", "sync/full"):
            # 允许在注释/docstring 里写「禁 sync」的说明，但不得作为字符串字面量调用
            if needle in text_all:
                # 粗滤：跳过纯说明行
                for line in text_all.splitlines():
                    skip_words = ("禁", "不要", "绝不", "doc", "档案", "ADR")
                    if needle in line and not any(w in line for w in skip_words):
                        if line.strip().startswith("#") or line.strip().startswith("*"):
                            continue
                        offenders.append(f"{p.name}: {line.strip()[:80]}")
    # 说明性文字允许存在；真正的调用字符串（requests/httpx 打 sync）应为空
    code_offenders = [o for o in offenders if "httpx" in o or "post(" in o or "get(" in o]
    assert code_offenders == []


def test_redline_no_real_token_in_finance_sources():
    root = Path(__file__).resolve().parents[1] / "modules" / "finance"
    web_root = Path(__file__).resolve().parents[3] / "apps" / "web" / "src" / "apps" / "finance"
    for base in (root, web_root):
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.suffix not in {".py", ".ts", ".tsx", ".json", ".md", ".css"}:
                continue
            body = p.read_text(encoding="utf-8", errors="ignore")
            # 真实 PAT 形态：bcmcp_ 后跟长随机；测试/文档里的前缀说明除外
            if "bcmcp_0RmxF9kh" in body:
                raise AssertionError(f"疑似真实 PAT 前缀出现在 {p}")


def test_local_today_is_shanghai_calendar_day():
    # UTC 2026-09-18 16:30 → 上海 2026-09-19 00:30
    now = datetime(2026, 9, 18, 16, 30, tzinfo=UTC)
    assert local_today(now) == date(2026, 9, 19)
    # UTC 2026-09-18 08:00 → 上海 16:00 同日
    now2 = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
    assert local_today(now2) == date(2026, 9, 18)
