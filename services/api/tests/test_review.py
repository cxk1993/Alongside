"""T09 复盘插件后端测试。

数据库隔离：./data/tmp_t09.db，只建本插件表，绝不碰主库。
上游：REVIEW_UPSTREAM=mock（离线全绿）；api 模式另测错误语义。
"""
from __future__ import annotations
from core.db_scope import declare_scope  # 2026-11-09 作用域翻转：显式声明

import os

os.environ["DB_PATH"] = "./data/tmp_t09.db"
os.environ["REVIEW_UPSTREAM"] = "mock"
os.environ["WORK_REVIEW_BRIDGE"] = "false"
os.environ.pop("WORK_REVIEW_TOKEN", None)

from datetime import date as DateType  # noqa: E402
from datetime import timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.review.client import ReviewClient, mock_markdown, mock_report  # noqa: E402
from modules.review.models import ReviewDaily, ReviewNote  # noqa: E402
from modules.review.parser import (  # noqa: E402
    extract_blocks,
    parse_duration,
    parse_export_markdown,
)
from modules.review.service import local_today  # noqa: E402

BASE = "/api/v1/review"
SH = ZoneInfo("Asia/Shanghai")


@pytest.fixture(scope="module")
def client():
    init_engine()
    engine = get_engine()
    ReviewDaily.__table__.create(bind=engine, checkfirst=True)
    ReviewNote.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    engine = get_engine()
    with declare_scope(__import__("sqlmodel").Session(engine)) as s:
        s.exec(text("DELETE FROM review_note"))
        s.exec(text("DELETE FROM review_daily"))
        s.commit()
    yield


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def _ingest(client, auth, day: DateType):
    r = client.post(f"{BASE}/ingest", params={"date": day.isoformat()}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


# ───────────────────────── 基础：health / manifest ─────────────────────────
def test_health(client):
    r = client.get(f"{BASE}/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["upstream_mode"] == "mock"
    assert body["version"] == "1.0.56"


def test_manifest(client):
    r = client.get(f"{BASE}/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "review"
    assert body["name"] == "复盘"
    assert body["api"]["base"] == BASE


# ───────────────────────── 无鉴权 401 ─────────────────────────
@pytest.mark.parametrize(
    "path",
    [
        f"{BASE}/source",
        f"{BASE}/days",
        f"{BASE}/day?date=2026-09-13",
        f"{BASE}/raw?date=2026-09-13",
        f"{BASE}/trend",
        f"{BASE}/notes",
    ],
)
def test_requires_auth_401(client, path):
    r = client.get(path)
    assert r.status_code == 401, r.text


def test_post_requires_auth_401(client):
    r = client.post(f"{BASE}/ingest", params={"date": "2026-09-13"})
    assert r.status_code == 401, r.text
    r2 = client.post(f"{BASE}/notes", json={"date": "2026-09-13", "content_md": "x"})
    assert r2.status_code == 401, r2.text


# ───────────────────────── 非法日期 422 ─────────────────────────
@pytest.mark.parametrize(
    "path",
    [
        f"{BASE}/day?date=not-a-date",
        f"{BASE}/day?date=2026-13-40",
        f"{BASE}/raw?date=abc",
        f"{BASE}/days?from=xx",
    ],
)
def test_invalid_date_422(client, auth, path):
    r = client.get(path, headers=auth)
    assert r.status_code == 422, r.text


# ───────────────────────── parse_duration 三种中文格式 ─────────────────────────
def test_parse_duration_three_formats():
    assert parse_duration("3小时13分52秒") == 3 * 3600 + 13 * 60 + 52
    assert parse_duration("41分13秒") == 41 * 60 + 13
    assert parse_duration("29秒") == 29
    assert parse_duration("") == 0
    assert parse_duration(None) == 0
    assert parse_duration("120") == 120


def test_parser_wr_blocks_and_empty():
    md = mock_markdown(DateType(2026, 9, 13))
    blocks = extract_blocks(md)
    assert "CATEGORY_TABLE" in blocks
    assert "APP_USAGE_TABLE" in blocks
    assert "AI_ANALYSIS" in blocks
    parsed = parse_export_markdown(md)
    assert parsed["categories"], "类别表应解析出数据"
    # 类别秒数与中文时长一致
    from modules.review.client import mock_report as mr

    report = mr(DateType(2026, 9, 13))
    if not report["empty"]:
        by_name = {c["name"]: c["seconds"] for c in parsed["categories"]}
        for c in report["categories"]:
            assert by_name.get(c["name"]) == c["seconds"]
    # 空 markdown 不崩
    empty = parse_export_markdown("")
    assert empty["categories"] == []
    assert empty["empty"] is True
    # 缺块独立：只有 AI 块
    only_ai = "<!-- WR_BLOCK_START:AI_ANALYSIS -->\n你好\n<!-- WR_BLOCK_END:AI_ANALYSIS -->"
    p2 = parse_export_markdown(only_ai)
    assert p2["ai_analysis_md"] == "你好"
    assert p2["categories"] == []


# ───────────────────────── ingest 幂等 ─────────────────────────
def test_ingest_idempotent(client, auth):
    day = DateType(2026, 9, 13)
    a = _ingest(client, auth, day)
    b = _ingest(client, auth, day)
    assert a["date"] == b["date"] == day.isoformat()
    assert a["raw_path"] == f"work-review:{day.isoformat()}"
    engine = get_engine()
    with declare_scope(Session(engine)) as s:
        rows = list(s.exec(select(ReviewDaily).where(ReviewDaily.date == day)).all())
        assert len(rows) == 1
        assert rows[0].raw_path == f"work-review:{day.isoformat()}"
    r = client.get(f"{BASE}/day", params={"date": day.isoformat()}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == day.isoformat()


def test_ingest_mock_consistency(client, auth):
    """mock 数据一致性：落库后的类别秒数 = mock_report 秒数；app 数一致。"""
    # 选一个非 empty 的确定日：遍历找到第一个
    day = None
    for i in range(20):
        d = DateType(2026, 9, 1) + timedelta(days=i)
        if not mock_report(d)["empty"]:
            day = d
            break
    assert day is not None
    report = mock_report(day)
    out = _ingest(client, auth, day)
    assert out["is_empty"] is False
    assert out["category_count"] == len(report["categories"])
    assert out["app_count"] == len(report["apps"])
    r = client.get(f"{BASE}/day", params={"date": day.isoformat()}, headers=auth)
    body = r.json()
    got = {c["name"]: c["seconds"] for c in body["categories"]}
    for c in report["categories"]:
        assert got[c["name"]] == c["seconds"]
        assert c["seconds"] == parse_duration(c["duration_text"])
    total = sum(got.values())
    assert body["total_seconds"] == total
    assert body["has_raw"] is True
    raw = client.get(f"{BASE}/raw", params={"date": day.isoformat()}, headers=auth).json()
    assert raw["found"] is True
    assert "WR_BLOCK_START:CATEGORY_TABLE" in raw["markdown"]


def test_ingest_empty_day(client, auth):
    """空响应日：优雅落库 is_empty，不崩。"""
    empty_day = None
    for i in range(40):
        d = DateType(2026, 8, 1) + timedelta(days=i)
        if mock_report(d)["empty"]:
            empty_day = d
            break
    assert empty_day is not None, "mock 应存在 empty 日"
    out = _ingest(client, auth, empty_day)
    assert out["is_empty"] is True
    day = client.get(f"{BASE}/day", params={"date": empty_day.isoformat()}, headers=auth).json()
    assert day["is_empty"] is True
    assert day["categories"] == []


# ───────────────────────── days 分页边界 ─────────────────────────
def test_days_pagination_boundaries(client, auth):
    # 空结果页
    r = client.get(f"{BASE}/days", params={"page": 1, "size": 10}, headers=auth)
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["total"] == 0
    assert r.json()["has_more"] is False

    # 落 3 天
    for i in range(3):
        _ingest(client, auth, DateType(2026, 9, 10) + timedelta(days=i))

    # size=1
    r1 = client.get(f"{BASE}/days", params={"page": 1, "size": 1}, headers=auth)
    b1 = r1.json()
    assert len(b1["items"]) == 1
    assert b1["total"] == 3
    assert b1["has_more"] is True
    # 倒序：最大日期在前
    assert b1["items"][0]["date"] >= b1["items"][-1]["date"]

    # 越界 page：不崩，空列表
    r2 = client.get(f"{BASE}/days", params={"page": 99, "size": 1}, headers=auth)
    assert r2.status_code == 200
    assert r2.json()["items"] == []
    assert r2.json()["total"] == 3
    assert r2.json()["has_more"] is False

    # size 很大
    r3 = client.get(f"{BASE}/days", params={"page": 1, "size": 500}, headers=auth)
    assert r3.status_code == 200
    assert len(r3.json()["items"]) == 3
    assert r3.json()["has_more"] is False


# ───────────────────────── source / notes / trend / compare / weekly ─────────────────────────
def test_source(client, auth):
    r = client.get(f"{BASE}/source", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "mock"
    assert body["upstream_online"] is True
    assert body["upstream_version"] == "1.0.56"
    assert body["message"] == "ok"
    _ingest(client, auth, DateType(2026, 9, 12))
    body2 = client.get(f"{BASE}/source", headers=auth).json()
    assert body2["last_sync_at"] is not None


def test_notes_crud_and_separation(client, auth):
    day = DateType(2026, 9, 13)
    _ingest(client, auth, day)
    r = client.post(
        f"{BASE}/notes",
        json={"date": day.isoformat(), "content_md": "今天状态不错"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    note = r.json()
    assert note["content_md"] == "今天状态不错"
    # 批注不进 review_daily.raw
    day_body = client.get(f"{BASE}/day", params={"date": day.isoformat()}, headers=auth).json()
    assert any(n["id"] == note["id"] for n in day_body["notes"])
    raw = client.get(f"{BASE}/raw", params={"date": day.isoformat()}, headers=auth).json()
    assert "今天状态不错" not in raw["markdown"]
    # 空批注 422
    bad = client.post(
        f"{BASE}/notes",
        json={"date": day.isoformat(), "content_md": "   "},
        headers=auth,
    )
    assert bad.status_code == 422
    # list notes
    lst = client.get(f"{BASE}/notes", params={"date": day.isoformat()}, headers=auth).json()
    assert len(lst["items"]) == 1


def test_trend_and_compare(client, auth):
    # ★ 日期必须「相对今天」：/trend 锚定 local_today 只查「最近 N 天」，
    #   写死具体日期会随日历滑出窗口，使该测试必然变红（2026-10-10 修）。
    # ★ 且不能简单取「今天-2/-1」：mock 有约 1/11 的「空日」（seed%11==0），
    #   那样约 16.5% 的日期会撞到空日、使断言又随机变红。
    #   故倒序取最近的两个非空日 —— 任何日期跑都稳。
    recent: list[DateType] = []
    d = local_today() - timedelta(days=1)
    while len(recent) < 2 and (local_today() - d).days <= 60:
        if not mock_report(d)["empty"]:
            recent.append(d)
        d -= timedelta(days=1)
    assert len(recent) == 2, "mock 应能提供两个非空日"
    d1, d2 = sorted(recent)  # d1 较早、d2 较晚，且都在 /trend 的 30 天窗口内
    _ingest(client, auth, d1)
    _ingest(client, auth, d2)
    r = client.get(f"{BASE}/trend", params={"metric": "total", "days": 30}, headers=auth)
    assert r.status_code == 200
    trend = r.json()
    assert trend["metric"] == "total"
    assert len(trend["points"]) >= 2
    assert trend["conclusion"]
    rc = client.get(
        f"{BASE}/compare",
        params={"date": d2.isoformat(), "against": d1.isoformat()},
        headers=auth,
    )
    assert rc.status_code == 200
    cmp_ = rc.json()
    assert cmp_["date"] == d2.isoformat()
    assert cmp_["conclusion"]
    # 结论是自然语言（含「比」或「合计」）
    assert ("比" in cmp_["conclusion"]) or ("合计" in cmp_["conclusion"])


def test_weekly(client, auth):
    r = client.get(f"{BASE}/weekly", params={"date": "2026-09-13"}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["source"] == "mock"
    assert body["total_seconds"] >= 0
    assert body["summary"]
    # 缓存命中
    r2 = client.get(f"{BASE}/weekly", params={"date": "2026-09-13"}, headers=auth)
    assert r2.json()["cached"] is True


def test_day_missing_is_empty_hint(client, auth):
    r = client.get(f"{BASE}/day", params={"date": "2020-01-01"}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["is_empty"] is True
    assert "无记录" in body["empty_hint"]


# ───────────────────────── client 错误语义（api / bridge） ─────────────────────────
def test_api_mode_missing_token_raises():
    c = ReviewClient(mode="api", token="", bridge=False)
    with pytest.raises(Exception) as ei:
        c.health()
    assert "WORK_REVIEW_TOKEN" in str(ei.value) or "token" in str(ei.value).lower()


def test_bridge_offline_fast_fail(monkeypatch):
    import httpx

    c = ReviewClient(
        mode="api",
        base_url="http://127.0.0.1:1",
        token="dummy-token-for-test",
        bridge=True,
        timeout=0.2,
    )

    def _boom(*_a: object, **_k: object) -> None:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "request", _boom)
    with pytest.raises(Exception) as ei:
        c.get_report(DateType(2026, 9, 13))
    msg = str(ei.value)
    assert "桥离线" in msg
    # 快速失败：构造时 timeout 已是 3s/0.2s，不无限重试
    assert c.timeout <= 3.0


def test_client_path_kind():
    assert ReviewClient(mode="mock").path_kind == "mock"
    assert ReviewClient(mode="api", token="t", bridge=False).path_kind == "direct"
    assert ReviewClient(mode="api", token="t", bridge=True).path_kind == "bridge"
    assert ReviewClient(mode="api", token="t", bridge=True).timeout <= 3.0


def test_local_today_timezone():
    assert local_today().isoformat()  # 可序列化
    assert local_today() == __import__("datetime").datetime.now(SH).date()
