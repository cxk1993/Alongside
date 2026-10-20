"""T08 账本（finance）后端测试。

数据库隔离：用临时库 ./data/tmp_t08.db，绝不碰主库 alongside.db。
表只在当前进程内由模型直接建（create_app 不自动跑迁移，避免跨插件迁移碰撞）。
"""
from __future__ import annotations

import os

# ★ 必须在 import 任何内核/模块之前设置临时库，init_engine 只认一次。
os.environ["DB_PATH"] = "./data/tmp_t08.db"

from datetime import datetime, timedelta, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.finance.models import FinanceEntry  # noqa: E402
from modules.finance.service import amount_to_cents  # noqa: E402

TZ = timezone(timedelta(hours=8))  # Asia/Shanghai
BASE = "/api/v1/finance"


@pytest.fixture(scope="module")
def client():
    init_engine()
    engine = get_engine()
    # 只建本插件表
    FinanceEntry.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    """每条测试前清空本插件表，测试之间不得相互依赖。"""
    engine = get_engine()
    with Session(engine) as s:
        s.exec(text("DELETE FROM finance_entry"))
        s.commit()
    yield


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat()


def _create(client, auth, **kw):
    if "occurred_at" not in kw:
        kw["occurred_at"] = _iso(datetime(2026, 9, 15, 12, 0, tzinfo=TZ))
    if "amount_cents" not in kw and "amount" not in kw:
        kw["amount_cents"] = 1000
    if "direction" not in kw:
        kw["direction"] = "expense"
    r = client.post(f"{BASE}/entries", json=kw, headers=auth)
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
    m = r.json()
    assert m["id"] == "finance"
    assert "finance.entry.created" in m["emits"]
    assert m["api"]["base"] == "/api/v1/finance"


# ───────────────────────── CRUD ─────────────────────────
def test_create_and_get_cents(client, auth):
    occurred = _iso(datetime(2026, 9, 15, 8, 0, tzinfo=TZ))
    item = _create(
        client,
        auth,
        amount_cents=12345,
        direction="expense",
        category="餐饮",
        account="现金",
        occurred_at=occurred,
        note="午饭",
    )
    assert item["amount_cents"] == 12345
    assert item["direction"] == "expense"
    assert item["category"] == "餐饮"
    assert item["account"] == "现金"
    assert item["note"] == "午饭"
    # 时间往返保时区（至少是 aware，且时刻等价）
    got = datetime.fromisoformat(item["occurred_at"].replace("Z", "+00:00"))
    assert got.tzinfo is not None
    assert got == datetime.fromisoformat(occurred.replace("Z", "+00:00"))

    r = client.get(f"{BASE}/entries/{item['id']}", headers=auth)
    assert r.status_code == 200
    assert r.json()["amount_cents"] == 12345


def test_create_decimal_string_amount(client, auth):
    item = _create(
        client,
        auth,
        amount="12.34",
        direction="income",
        category="工资",
        account="招行",
    )
    assert item["amount_cents"] == 1234  # Decimal("12.34") * 100 精确
    assert item["direction"] == "income"


def test_create_income_direction(client, auth):
    item = _create(client, auth, amount_cents=50000, direction="income", category="红包")
    assert item["direction"] == "income"
    assert item["amount_cents"] == 50000


def test_update(client, auth):
    item = _create(client, auth, amount_cents=100, category="旧类", note="初稿")
    iid = item["id"]
    r = client.patch(
        f"{BASE}/entries/{iid}",
        json={"category": "新类", "amount_cents": 250, "note": "终稿"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["category"] == "新类"
    assert body["amount_cents"] == 250
    assert body["note"] == "终稿"

    # 用 Decimal 字符串更新金额
    r2 = client.patch(
        f"{BASE}/entries/{iid}",
        json={"amount": "3.50"},
        headers=auth,
    )
    assert r2.json()["amount_cents"] == 350


def test_delete_204_then_404(client, auth):
    item = _create(client, auth, note="临时")
    iid = item["id"]
    r = client.delete(f"{BASE}/entries/{iid}", headers=auth)
    assert r.status_code == 204
    assert r.text == "" or r.content == b""
    r2 = client.get(f"{BASE}/entries/{iid}", headers=auth)
    assert r2.status_code == 404


def test_get_missing_404(client, auth):
    r = client.get(f"{BASE}/entries/does-not-exist", headers=auth)
    assert r.status_code == 404


def test_update_missing_404(client, auth):
    r = client.patch(
        f"{BASE}/entries/does-not-exist",
        json={"category": "x"},
        headers=auth,
    )
    assert r.status_code == 404


def test_delete_missing_404(client, auth):
    r = client.delete(f"{BASE}/entries/does-not-exist", headers=auth)
    assert r.status_code == 404


# ───────────────────────── 过滤 / 分页 ─────────────────────────
def test_filter_by_category_account_direction(client, auth):
    _create(client, auth, amount_cents=100, category="餐饮", account="现金", direction="expense")
    _create(client, auth, amount_cents=200, category="交通", account="现金", direction="expense")
    _create(client, auth, amount_cents=300, category="餐饮", account="招行", direction="expense")
    _create(client, auth, amount_cents=400, category="工资", account="招行", direction="income")

    r = client.get(f"{BASE}/entries?category=餐饮", headers=auth)
    body = r.json()
    assert body["total"] == 2
    assert all(i["category"] == "餐饮" for i in body["items"])

    r = client.get(f"{BASE}/entries?account=招行", headers=auth)
    assert r.json()["total"] == 2

    r = client.get(f"{BASE}/entries?direction=income", headers=auth)
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["amount_cents"] == 400

    r = client.get(f"{BASE}/entries?category=餐饮&account=招行&direction=expense", headers=auth)
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["amount_cents"] == 300


def test_filter_by_date_range(client, auth):
    d1 = _iso(datetime(2026, 9, 1, 10, 0, tzinfo=TZ))
    d2 = _iso(datetime(2026, 9, 10, 10, 0, tzinfo=TZ))
    d3 = _iso(datetime(2026, 9, 20, 10, 0, tzinfo=TZ))
    _create(client, auth, amount_cents=111, occurred_at=d1, category="月初")
    _create(client, auth, amount_cents=222, occurred_at=d2, category="月中")
    _create(client, auth, amount_cents=333, occurred_at=d3, category="月末")

    # ★ 用 params= 传带时区的 ISO 串：URL 里的 `+08:00` 会被当成空格
    r = client.get(
        f"{BASE}/entries",
        params={"date_from": d1, "date_to": d2},
        headers=auth,
    )
    body = r.json()
    assert body["total"] == 2
    cats = {i["category"] for i in body["items"]}
    assert cats == {"月初", "月中"}


def test_pagination_limit_offset(client, auth):
    for i in range(5):
        _create(
            client,
            auth,
            amount_cents=100 + i,
            category=f"c{i}",
            occurred_at=_iso(datetime(2026, 9, 15, 8, 0, tzinfo=TZ) + timedelta(minutes=i)),
        )
    r1 = client.get(f"{BASE}/entries?limit=2&offset=0", headers=auth)
    assert r1.status_code == 200
    p1 = r1.json()
    assert p1["total"] == 5
    assert len(p1["items"]) == 2
    assert p1["limit"] == 2
    assert p1["offset"] == 0

    r2 = client.get(f"{BASE}/entries?limit=2&offset=2", headers=auth)
    p2 = r2.json()
    assert len(p2["items"]) == 2
    ids1 = {i["id"] for i in p1["items"]}
    ids2 = {i["id"] for i in p2["items"]}
    assert ids1 & ids2 == set()


# ───────────────────────── 汇总 ─────────────────────────
def test_summary_totals_and_by_category(client, auth):
    # 精确金额：餐饮 支出 12.34+1.00 = 13.34 元 → 1334 分
    # 工资 收入 100.00 元 → 10000 分
    # 交通 支出 0.50 元 → 50 分
    _create(client, auth, amount="12.34", direction="expense", category="餐饮", account="现金")
    _create(client, auth, amount_cents=100, direction="expense", category="餐饮", account="现金")
    _create(client, auth, amount_cents=10000, direction="income", category="工资", account="招行")
    _create(client, auth, amount="0.50", direction="expense", category="交通", account="现金")

    r = client.get(f"{BASE}/summary", headers=auth)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["expense_cents"] == 1234 + 100 + 50  # 1384
    assert s["income_cents"] == 10000
    assert s["net_cents"] == 10000 - 1384
    assert s["count"] == 4
    assert s["by_category"]  # 固定 schema 有该字段

    by = {c["category"]: c for c in s["by_category"]}
    assert by["餐饮"]["expense_cents"] == 1334
    assert by["餐饮"]["income_cents"] == 0
    assert by["餐饮"]["count"] == 2
    assert by["工资"]["income_cents"] == 10000
    assert by["交通"]["expense_cents"] == 50


def test_summary_empty_schema(client, auth):
    r = client.get(f"{BASE}/summary", headers=auth)
    assert r.status_code == 200
    s = r.json()
    assert s["expense_cents"] == 0
    assert s["income_cents"] == 0
    assert s["net_cents"] == 0
    assert s["count"] == 0
    assert s["by_category"] == []
    assert set(s.keys()) >= {
        "date_from",
        "date_to",
        "expense_cents",
        "income_cents",
        "net_cents",
        "count",
        "by_category",
    }


def test_summary_date_range_filter(client, auth):
    _create(
        client,
        auth,
        amount_cents=1000,
        direction="expense",
        category="区间内",
        occurred_at=_iso(datetime(2026, 9, 10, 12, 0, tzinfo=TZ)),
    )
    _create(
        client,
        auth,
        amount_cents=9999,
        direction="expense",
        category="区间外",
        occurred_at=_iso(datetime(2026, 8, 1, 12, 0, tzinfo=TZ)),
    )
    df = _iso(datetime(2026, 9, 1, 0, 0, tzinfo=TZ))
    dt = _iso(datetime(2026, 9, 30, 23, 59, tzinfo=TZ))
    r = client.get(
        f"{BASE}/summary",
        params={"date_from": df, "date_to": dt},
        headers=auth,
    )
    s = r.json()
    assert s["expense_cents"] == 1000
    assert s["count"] == 1
    assert s["by_category"][0]["category"] == "区间内"


# ───────────────────────── 金额解析（纯函数） ─────────────────────────
def test_amount_to_cents_exact():
    assert amount_to_cents(1, None) == 1
    assert amount_to_cents(12345, None) == 12345
    assert amount_to_cents(None, "12.34") == 1234
    assert amount_to_cents(None, "0.01") == 1
    assert amount_to_cents(None, "100") == 10000
    # amount_cents 优先
    assert amount_to_cents(50, "99.99") == 50
    # Decimal 语义
    assert int(Decimal("12.34") * 100) == 1234


def test_amount_to_cents_rejects_bad():
    from core.errors import ValidationError

    with pytest.raises(ValidationError):
        amount_to_cents(None, None)
    with pytest.raises(ValidationError):
        amount_to_cents(0, None)
    with pytest.raises(ValidationError):
        amount_to_cents(-5, None)
    with pytest.raises(ValidationError):
        amount_to_cents(None, "abc")
    with pytest.raises(ValidationError):
        amount_to_cents(None, "12.345")  # 三位小数
    with pytest.raises(ValidationError):
        amount_to_cents(None, "-1.00")


# ───────────────────────── 边界 ─────────────────────────
def test_boundary_zero_amount_rejected(client, auth):
    r = client.post(
        f"{BASE}/entries",
        json={
            "direction": "expense",
            "amount_cents": 0,
            "occurred_at": _iso(datetime(2026, 9, 15, tzinfo=TZ)),
        },
        headers=auth,
    )
    assert r.status_code == 422, r.text


def test_boundary_missing_amount_rejected(client, auth):
    r = client.post(
        f"{BASE}/entries",
        json={
            "direction": "expense",
            "occurred_at": _iso(datetime(2026, 9, 15, tzinfo=TZ)),
        },
        headers=auth,
    )
    # pydantic 不拦双空 → service ValidationError 422
    assert r.status_code == 422, r.text


def test_boundary_bad_direction_rejected(client, auth):
    r = client.post(
        f"{BASE}/entries",
        json={
            "direction": "transfer",
            "amount_cents": 100,
            "occurred_at": _iso(datetime(2026, 9, 15, tzinfo=TZ)),
        },
        headers=auth,
    )
    assert r.status_code == 422, r.text


def test_boundary_naive_datetime_rejected(client, auth):
    r = client.post(
        f"{BASE}/entries",
        json={
            "direction": "expense",
            "amount_cents": 100,
            "occurred_at": "2026-09-15T08:00:00",  # naive
        },
        headers=auth,
    )
    assert r.status_code in (400, 422), r.text


def test_boundary_chinese_and_emoji_note(client, auth):
    item = _create(
        client,
        auth,
        note="午饭 · 拉面🍜（中文测试）",
        category="餐饮备注",
    )
    assert item["note"] == "午饭 · 拉面🍜（中文测试）"


def test_boundary_no_auth_rejected(client):
    r = client.get(f"{BASE}/entries")
    assert r.status_code in (401, 403)
    r2 = client.get(f"{BASE}/summary")
    assert r2.status_code in (401, 403)
    r3 = client.post(
        f"{BASE}/entries",
        json={
            "direction": "expense",
            "amount_cents": 1,
            "occurred_at": _iso(datetime(2026, 9, 15, tzinfo=TZ)),
        },
    )
    assert r3.status_code in (401, 403)


def test_boundary_invalid_list_direction(client, auth):
    r = client.get(f"{BASE}/entries?direction=nope", headers=auth)
    assert r.status_code == 422, r.text


def test_idempotency_key(client, auth):
    headers = {**auth, "Idempotency-Key": "t08-finance-key-001"}
    payload = {
        "direction": "expense",
        "amount_cents": 888,
        "category": "幂等",
        "occurred_at": _iso(datetime(2026, 9, 15, 9, 0, tzinfo=TZ)),
    }
    r1 = client.post(f"{BASE}/entries", json=payload, headers=headers)
    assert r1.status_code == 201, r1.text
    r2 = client.post(f"{BASE}/entries", json=payload, headers=headers)
    assert r2.status_code == 201, r2.text
    assert r2.headers.get("X-Idempotent-Replay") == "true"
    assert r1.json()["id"] == r2.json()["id"]
