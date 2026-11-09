"""T10 习惯打卡后端测试。

数据库隔离：./data/tmp_t10.db，绝不碰主库。
"""
from __future__ import annotations
from core.db_scope import declare_scope  # 2026-11-09 作用域翻转：显式声明

import os

os.environ["DB_PATH"] = "./data/tmp_t10.db"

from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.habits.models import Habit, HabitLog  # noqa: E402
from modules.habits.service import local_today  # noqa: E402


@pytest.fixture(scope="module")
def client():
    init_engine()
    engine = get_engine()
    Habit.__table__.create(bind=engine, checkfirst=True)
    HabitLog.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    engine = get_engine()
    with declare_scope(__import__("sqlmodel").Session(engine)) as s:
        s.exec(text("DELETE FROM habits_log"))
        s.exec(text("DELETE FROM habits_habit"))
        s.commit()
    yield


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def _create(client, auth, **kw):
    r = client.post("/api/v1/habits", json=kw, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


# ───────────────────────── 基础 ─────────────────────────
def test_health(client):
    r = client.get("/api/v1/habits/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_manifest(client):
    r = client.get("/api/v1/habits/manifest")
    assert r.status_code == 200
    assert r.json()["id"] == "habits"


def test_create_and_get(client, auth):
    h = _create(client, auth, name="早睡", target="23:30 前", rest_weekdays=[5, 6])
    assert h["name"] == "早睡"
    assert h["rest_weekdays"] == [5, 6]
    assert h["rule"]["type"] == "daily"
    assert h["today_status"] == "pending"
    r = client.get(f"/api/v1/habits/{h['id']}", headers=auth)
    assert r.status_code == 200
    assert r.json()["id"] == h["id"]


def test_update(client, auth):
    h = _create(client, auth, name="喝水")
    r = client.patch(
        f"/api/v1/habits/{h['id']}",
        json={"name": "多喝水", "archived": True},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "多喝水"
    assert r.json()["archived"] is True


def test_delete(client, auth):
    h = _create(client, auth, name="要删的")
    client.post(
        f"/api/v1/habits/{h['id']}/checkin", json={}, headers=auth
    )
    r = client.delete(f"/api/v1/habits/{h['id']}", headers=auth)
    assert r.status_code == 204, r.text
    assert client.get(f"/api/v1/habits/{h['id']}", headers=auth).status_code == 404


# ───────────────────────── 打卡 / 连击 ─────────────────────────
def test_checkin_and_streak(client, auth):
    h = _create(client, auth, name="晨跑")
    today = local_today()
    # 连续打 3 天
    for i in range(3):
        d = today - timedelta(days=2 - i)
        r = client.post(
            f"/api/v1/habits/{h['id']}/checkin",
            json={"date": d.isoformat()},
            headers=auth,
        )
        assert r.status_code == 200, r.text
    body = r.json()
    assert body["today_status"] == "done"
    assert body["streak"] == 3


def test_missed_day_breaks_streak(client, auth):
    h = _create(client, auth, name="阅读")
    today = local_today()
    # 前天打卡，昨天漏了
    client.post(
        f"/api/v1/habits/{h['id']}/checkin",
        json={"date": (today - timedelta(days=2)).isoformat()},
        headers=auth,
    )
    body = client.get(f"/api/v1/habits/{h['id']}", headers=auth).json()
    assert body["streak"] == 0


def test_rest_weekday_skips_and_keeps_streak(client, auth):
    # 把「昨天」设为休息日，前天+今天都打卡 → streak 应为 2（跳过昨天）
    h = _create(client, auth, name="拉伸", rest_weekdays=[])
    today = local_today()
    yesterday = today - timedelta(days=1)
    client.patch(
        f"/api/v1/habits/{h['id']}",
        json={"rest_weekdays": [yesterday.weekday()]},
        headers=auth,
    )
    for d in (today - timedelta(days=2), today):
        client.post(
            f"/api/v1/habits/{h['id']}/checkin",
            json={"date": d.isoformat()},
            headers=auth,
        )
    body = client.get(f"/api/v1/habits/{h['id']}", headers=auth).json()
    assert body["today_status"] == "done"
    assert body["streak"] == 2


def test_rest_log_status(client, auth):
    h = _create(client, auth, name="冥想")
    r = client.post(
        f"/api/v1/habits/{h['id']}/checkin",
        json={"is_rest": True, "note": "生病"},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["today_status"] == "rest"


def test_uncheck(client, auth):
    h = _create(client, auth, name="写日记")
    client.post(f"/api/v1/habits/{h['id']}/checkin", json={}, headers=auth)
    r = client.delete(
        f"/api/v1/habits/{h['id']}/checkin/{local_today().isoformat()}",
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["today_status"] == "pending"
    assert r.json()["streak"] == 0


def test_checkin_idempotent_same_day(client, auth):
    h = _create(client, auth, name="俯卧撑")
    client.post(
        f"/api/v1/habits/{h['id']}/checkin", json={"value": "20"}, headers=auth
    )
    r = client.post(
        f"/api/v1/habits/{h['id']}/checkin", json={"value": "30"}, headers=auth
    )
    assert r.status_code == 200
    logs = client.get(f"/api/v1/habits/{h['id']}/logs", headers=auth).json()
    assert len(logs) == 1
    assert logs[0]["value"] == "30"


# ───────────────────────── 列表 / 概览 ─────────────────────────
def test_list_excludes_archived(client, auth):
    a = _create(client, auth, name="活跃习惯")
    b = _create(client, auth, name="归档习惯")
    client.patch(
        f"/api/v1/habits/{b['id']}", json={"archived": True}, headers=auth
    )
    names = [h["name"] for h in client.get("/api/v1/habits", headers=auth).json()]
    assert "活跃习惯" in names
    assert "归档习惯" not in names
    names2 = [
        h["name"]
        for h in client.get(
            "/api/v1/habits?include_archived=true", headers=auth
        ).json()
    ]
    assert "归档习惯" in names2
    # 清理引用
    assert a["id"]


def test_summary(client, auth):
    h1 = _create(client, auth, name="A")
    _create(client, auth, name="B")
    client.post(f"/api/v1/habits/{h1['id']}/checkin", json={}, headers=auth)
    s = client.get("/api/v1/habits/summary", headers=auth).json()
    assert s["total"] >= 2
    assert s["done"] >= 1
    assert s["pending"] >= 1


# ───────────────────────── 边界 ─────────────────────────
def test_boundary_empty_name(client, auth):
    r = client.post("/api/v1/habits", json={"name": "  "}, headers=auth)
    assert r.status_code in (400, 422), r.text


def test_boundary_empty_name_on_update(client, auth):
    h = _create(client, auth, name="原名")
    r = client.patch(
        f"/api/v1/habits/{h['id']}",
        json={"name": "  "},
        headers=auth,
    )
    assert r.status_code in (400, 422), r.text
    body = client.get(f"/api/v1/habits/{h['id']}", headers=auth).json()
    assert body["name"] == "原名"


def test_boundary_custom_rule_requires_days(client, auth):
    r = client.post(
        "/api/v1/habits",
        json={"name": "自定义", "rule": {"type": "custom"}},
        headers=auth,
    )
    assert r.status_code in (400, 422), r.text


def test_boundary_bad_rest_weekday(client, auth):
    r = client.post(
        "/api/v1/habits",
        json={"name": "越界", "rest_weekdays": [9]},
        headers=auth,
    )
    assert r.status_code in (400, 422), r.text


def test_boundary_no_auth(client):
    r = client.get("/api/v1/habits")
    assert r.status_code in (401, 403)


def test_logs_range(client, auth):
    h = _create(client, auth, name="区间")
    today = local_today()
    for i in range(3):
        client.post(
            f"/api/v1/habits/{h['id']}/checkin",
            json={"date": (today - timedelta(days=i)).isoformat()},
            headers=auth,
        )
    logs = client.get(
        f"/api/v1/habits/{h['id']}/logs"
        f"?from={today.isoformat()}&to={today.isoformat()}",
        headers=auth,
    ).json()
    assert len(logs) == 1
    assert logs[0]["date"] == today.isoformat()


def test_boundary_invalid_date_checkin(client, auth):
    h = _create(client, auth, name="坏日期")
    r = client.post(
        f"/api/v1/habits/{h['id']}/checkin",
        json={"date": "not-a-date"},
        headers=auth,
    )
    assert r.status_code == 422, r.text


def test_boundary_invalid_date_uncheck_path(client, auth):
    h = _create(client, auth, name="坏路径日期")
    r = client.delete(
        f"/api/v1/habits/{h['id']}/checkin/2026-13-45",
        headers=auth,
    )
    assert r.status_code == 422, r.text


def test_boundary_invalid_date_logs_query(client, auth):
    h = _create(client, auth, name="坏查询日期")
    r = client.get(
        f"/api/v1/habits/{h['id']}/logs?from=not-a-date",
        headers=auth,
    )
    assert r.status_code == 422, r.text


def test_boundary_not_found(client, auth):
    missing = "no-such-habit-id"
    assert client.get(
        f"/api/v1/habits/{missing}", headers=auth
    ).status_code == 404
    assert client.patch(
        f"/api/v1/habits/{missing}", json={"name": "x"}, headers=auth
    ).status_code == 404
    assert client.delete(
        f"/api/v1/habits/{missing}", headers=auth
    ).status_code == 404
    assert client.post(
        f"/api/v1/habits/{missing}/checkin", json={}, headers=auth
    ).status_code == 404
    assert client.delete(
        f"/api/v1/habits/{missing}/checkin/{local_today().isoformat()}",
        headers=auth,
    ).status_code == 404
    assert client.get(
        f"/api/v1/habits/{missing}/logs", headers=auth
    ).status_code == 404


def test_boundary_chinese_and_special_name(client, auth):
    h = _create(client, auth, name="写日记📓&冥想（晨）—打卡!")
    assert h["name"] == "写日记📓&冥想（晨）—打卡!"
    r = client.get(f"/api/v1/habits/{h['id']}", headers=auth)
    assert r.status_code == 200


def test_boundary_overlong_name(client, auth):
    r = client.post(
        "/api/v1/habits",
        json={"name": "长" * 121},
        headers=auth,
    )
    assert r.status_code == 422, r.text


def test_create_weekly_and_custom_rules(client, auth):
    w = _create(client, auth, name="每周三次", rule={"type": "weekly", "times": 3})
    assert w["rule"]["type"] == "weekly"
    assert w["rule"]["times"] == 3
    c = _create(client, auth, name="一三五", rule={"type": "custom", "days": [0, 2, 4]})
    assert c["rule"]["type"] == "custom"
    assert c["rule"]["days"] == [0, 2, 4]


def test_summary_counts_rest_and_best_streak(client, auth):
    today = local_today()
    h_rest = _create(client, auth, name="休息位")
    h_done = _create(client, auth, name="连击位")
    client.post(
        f"/api/v1/habits/{h_rest['id']}/checkin",
        json={"is_rest": True},
        headers=auth,
    )
    for i in range(2):
        client.post(
            f"/api/v1/habits/{h_done['id']}/checkin",
            json={"date": (today - timedelta(days=1 - i)).isoformat()},
            headers=auth,
        )
    s = client.get("/api/v1/habits/summary", headers=auth).json()
    assert s["rest"] >= 1
    assert s["done"] >= 1
    assert s["best_streak"] >= 2
