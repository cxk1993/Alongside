"""课程表（course）后端测试。

数据库隔离：./data/tmp_course.db，绝不碰主库。

覆盖：
  ① 基础（health / manifest / CRUD / 鉴权）
  ② 周网格（7 列、按 weekday 归位、按 start_time 升序）
  ③ 周次表达式（1-16 / 单双周 / 非法拒绝）
  ④ 上课提醒调度（窗口命中 + 幂等 + 状态端点）
"""
from __future__ import annotations
from core.db_scope import declare_scope  # 2026-11-09 作用域翻转：显式声明

import os

os.environ["DB_PATH"] = "./data/tmp_course.db"

from datetime import UTC, datetime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.course.models import CourseItem  # noqa: E402
from modules.course.service import _parse_weeks, local_today  # noqa: E402

SH = ZoneInfo("Asia/Shanghai")


@pytest.fixture(scope="module")
def client():
    init_engine()
    engine = get_engine()
    CourseItem.__table__.create(bind=engine, checkfirst=True)
    # ★ 学期设置存内核 plugin_setting 表 —— 隔离测试库也要建（生产由内核建表）
    from db.models.system import PluginSetting

    PluginSetting.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    engine = get_engine()
    with declare_scope(__import__("sqlmodel").Session(engine)) as s:
        s.exec(text("DELETE FROM course_item"))
        try:
            s.exec(text("DELETE FROM plugin_setting WHERE plugin_id='course'"))
        except Exception:  # noqa: BLE001 — 表可能尚未建（首次）
            pass
        s.commit()
    yield


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def _create(client, auth, **kw):
    body = {"name": "高等数学", "weekday": 0, **kw}
    r = client.post("/api/v1/course/items", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


# ───────────────────────── ① 基础 ─────────────────────────
def test_health(client):
    r = client.get("/api/v1/course/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_manifest(client):
    r = client.get("/api/v1/course/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "course"
    assert body["api"]["base"] == "/api/v1/course"


def test_create_and_get(client, auth):
    h = _create(client, auth, name="高等数学", teacher="张老师", location="知新楼 B203")
    assert h["name"] == "高等数学"
    assert h["teacher"] == "张老师"
    assert h["location"] == "知新楼 B203"
    assert h["enabled"] is True
    r = client.get(f"/api/v1/course/items/{h['id']}", headers=auth)
    assert r.status_code == 200
    assert r.json()["id"] == h["id"]


def test_update_and_delete(client, auth):
    h = _create(client, auth, name="大学物理")
    r = client.patch(
        f"/api/v1/course/items/{h['id']}",
        json={"location": "知新楼 C101", "enabled": False},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["location"] == "知新楼 C101"
    assert r.json()["enabled"] is False

    assert client.delete(f"/api/v1/course/items/{h['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/v1/course/items/{h['id']}", headers=auth).status_code == 404


def test_list_and_filter(client, auth):
    _create(client, auth, name="周一课", weekday=0)
    _create(client, auth, name="周三课", weekday=2)
    _create(client, auth, name="停了的课", weekday=2, enabled=False)

    all_items = client.get("/api/v1/course/items", headers=auth).json()
    assert len(all_items) == 3

    wed = client.get("/api/v1/course/items?weekday=2", headers=auth).json()
    assert len(wed) == 2

    on = client.get("/api/v1/course/items?enabled_only=true", headers=auth).json()
    assert len(on) == 2


def test_no_auth(client):
    assert client.get("/api/v1/course/items").status_code in (401, 403)
    assert client.get("/api/v1/course/week").status_code in (401, 403)


def test_not_found(client, auth):
    assert client.get("/api/v1/course/items/nope", headers=auth).status_code == 404
    assert client.patch("/api/v1/course/items/nope", json={"name": "x"}, headers=auth).status_code == 404
    assert client.delete("/api/v1/course/items/nope", headers=auth).status_code == 404


def test_boundary_empty_name(client, auth):
    assert client.post("/api/v1/course/items", json={"name": "  "}, headers=auth).status_code in (400, 422)


def test_boundary_bad_weekday(client, auth):
    r = client.post("/api/v1/course/items", json={"name": "越界", "weekday": 9}, headers=auth)
    assert r.status_code == 422, r.text


def test_boundary_overlong_name(client, auth):
    r = client.post("/api/v1/course/items", json={"name": "长" * 101}, headers=auth)
    assert r.status_code == 422, r.text


# ───────────────────────── ② 周网格 ─────────────────────────
def test_week_grid_has_7_days(client, auth):
    r = client.get("/api/v1/course/week", headers=auth)
    assert r.status_code == 200, r.text
    days = r.json()["days"]
    assert len(days) == 7
    assert [d["weekday"] for d in days] == [0, 1, 2, 3, 4, 5, 6]
    assert days[0]["label"] == "周一"
    assert days[6]["label"] == "周日"


def test_week_grid_groups_by_weekday_and_sorts(client, auth):
    _create(client, auth, name="周三早八", weekday=2, start_time="08:00")
    _create(client, auth, name="周三下午", weekday=2, start_time="14:00")
    _create(client, auth, name="周一课", weekday=0, start_time="10:00")

    days = client.get("/api/v1/course/week", headers=auth).json()["days"]
    wed = [d for d in days if d["weekday"] == 2][0]
    assert [i["name"] for i in wed["items"]] == ["周三早八", "周三下午"]  # 时间升序
    mon = [d for d in days if d["weekday"] == 0][0]
    assert [i["name"] for i in mon["items"]] == ["周一课"]


def test_week_grid_excludes_disabled(client, auth):
    _create(client, auth, name="停课", weekday=1, enabled=False)
    days = client.get("/api/v1/course/week", headers=auth).json()["days"]
    assert days[1]["items"] == []


def test_week_grid_bad_date(client, auth):
    r = client.get("/api/v1/course/week?date=not-a-date", headers=auth)
    assert r.status_code in (400, 422), r.text


# ───────────────────────── ③ 周次表达式 ─────────────────────────
def test_parse_weeks_forms():
    assert _parse_weeks(None) is None  # 每周
    assert _parse_weeks("") is None
    assert _parse_weeks("1-3") == {1, 2, 3}
    assert _parse_weeks("1,3,5") == {1, 3, 5}
    assert _parse_weeks("2-6双") == {2, 4, 6}
    assert _parse_weeks("1-6单") == {1, 3, 5}
    assert _parse_weeks("3-1") == {1, 2, 3}  # 反向容忍


def test_parse_weeks_invalid():
    from core.errors import ValidationError

    with pytest.raises(ValidationError):
        _parse_weeks("abc")
    with pytest.raises(ValidationError):
        _parse_weeks("1-x")


def test_invalid_weeks_rejected_by_api(client, auth):
    r = client.post(
        "/api/v1/course/items",
        json={"name": "坏周次", "weekday": 0, "weeks": "not-weeks"},
        headers=auth,
    )
    assert r.status_code in (400, 422), r.text


def test_week_grid_filters_by_term_week(client, auth):
    """带 weeks 的课：只有命中当前教学周才出现在网格里。"""
    today = local_today()
    term_start = (today - timedelta(days=today.weekday())).isoformat()  # 本周一 = 第 1 周
    _create(
        client, auth, name="第1周课", weekday=today.weekday(),
        weeks="1", term_start=term_start,
    )
    _create(
        client, auth, name="第9周课", weekday=today.weekday(),
        weeks="9", term_start=term_start,
    )
    grid = client.get(f"/api/v1/course/week?date={today.isoformat()}", headers=auth).json()
    assert grid["term_week"] == 1
    col = [d for d in grid["days"] if d["weekday"] == today.weekday()][0]
    names = [i["name"] for i in col["items"]]
    assert "第1周课" in names
    assert "第9周课" not in names


# ───────────────────────── ④ 上课提醒 ─────────────────────────
def test_scheduler_status_endpoint(client, auth):
    r = client.get("/api/v1/course/due/scheduler", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert "enabled" in body and "running" in body and "lead_minutes" in body


def test_collect_due_courses_window(client, auth):
    """提醒窗口：开课前 lead 分钟内命中；开课后 / 更早都不命中。"""
    from modules.course.remind_scheduler import _notified, collect_due_courses

    _notified.clear()
    now_sh = datetime.now(SH)
    today = now_sh.date()
    wd = today.weekday()

    def mk(name: str, minutes_from_now: int):
        t = (now_sh + timedelta(minutes=minutes_from_now)).strftime("%H:%M")
        return _create(client, auth, name=name, weekday=wd, start_time=t)

    soon = mk("马上要上", 5)      # 5 分钟后上课 → 应命中（lead 默认 15）
    later = mk("还早", 120)       # 2 小时后 → 不命中
    past = mk("刚上过", -30)      # 30 分钟前开始 → 不命中

    from sqlmodel import Session

    engine = get_engine()
    with declare_scope(Session(engine)) as s:
        due = collect_due_courses(s, now=datetime.now(UTC))
    due_ids = {r.id for r, _ in due}
    assert soon["id"] in due_ids
    assert later["id"] not in due_ids
    assert past["id"] not in due_ids
    _notified.clear()


def test_run_tick_publishes_and_is_idempotent(client, auth):
    """tick 命中 → 发布 1 次；再跑同一条不重复（进程内幂等）。"""
    from modules.course.remind_scheduler import _notified, run_course_tick

    _notified.clear()
    now_sh = datetime.now(SH)
    today = now_sh.date()
    t = (now_sh + timedelta(minutes=5)).strftime("%H:%M")
    _create(client, auth, name="幂等课", weekday=today.weekday(), start_time=t)

    from sqlmodel import Session

    engine = get_engine()
    with declare_scope(Session(engine)) as s:
        first = run_course_tick(s)
        second = run_course_tick(s)
    assert first == 1, f"首次应发布 1 条，实得 {first}"
    assert second == 0, f"二次应幂等 0 条，实得 {second}"
    _notified.clear()


def test_tick_no_start_time_skipped(client, auth):
    """没有 start_time 的课不参与提醒（无法定时）。"""
    from modules.course.remind_scheduler import _notified, run_course_tick

    _notified.clear()
    _create(client, auth, name="没时间", weekday=local_today().weekday())

    from sqlmodel import Session

    engine = get_engine()
    with declare_scope(Session(engine)) as s:
        assert run_course_tick(s) == 0
    _notified.clear()
