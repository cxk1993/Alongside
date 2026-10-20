"""T05 日程表后端测试（只跑这一个文件，不影响别的卡）。

数据库隔离：用临时库 ./data/tmp_t05.db，绝不碰主库 alongside.db。
表只在当前进程内由模型直接建（create_app 不自动跑迁移，避免跨插件迁移碰撞）。
"""
from __future__ import annotations

import os

# ★ 必须在 import 任何内核/模块之前设置临时库，init_engine 只认一次。
os.environ["DB_PATH"] = "./data/tmp_t05.db"

from datetime import datetime, timedelta, timezone  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.calendar.models import CalendarEvent  # noqa: E402

TZ = timezone(timedelta(hours=8))  # 主人本地时区 Asia/Shanghai


@pytest.fixture(scope="module")
def client():
    init_engine()  # 用 DB_PATH 指向的临时库
    engine = get_engine()
    # 只建本插件一张表（单一模块实例，避免与迁移的 file-path 加载重复注册）。
    # checkfirst=True 保证幂等 —— 表已存在就跳过，不会报错。
    CalendarEvent.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_events():
    """每条测试前清空本插件表。

    ★ 库是 module 级共享的（同一份临时库文件），事件会**跨测试累积**——
      实测踩过：list_range 测试因此查出了 8 个根（别的测试建的事件全在里面）。
      测试之间不得相互依赖，顺序依赖是脆弱测试。
    先删子块再删根，满足 parent_id 外键的删除顺序。
    """
    from sqlmodel import Session

    from db.engine import get_engine as _ge

    engine = _ge()
    with Session(engine) as s:
        s.exec(text("DELETE FROM calendar_event WHERE parent_id IS NOT NULL"))
        s.exec(text("DELETE FROM calendar_event WHERE parent_id IS NULL"))
        s.commit()
    yield


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat()


def _today_within(hours_ahead: int = 2):
    """★ 2026-09-25 修（顺带）：造一个**保证落在今天**的未来时刻。

    背景（实测）：原写法 `datetime.now(TZ) + timedelta(hours=2)` 在 **22:00 之后**跑
    会落到**次日** —— 于是 today-summary 自然查不到它，测试变成
    「每晚 22 点后必失败」的时间 flaky（22:47 跑全量时撞上）。

    语义保持：仍是"今天之内的未来时刻"（若 now+hours 跨天，则钳到当天 23:59）。
    """
    now = datetime.now(TZ)
    target = now + timedelta(hours=hours_ahead)
    if target.date() != now.date():
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
    return target


# ───────────────────────── 基础 ─────────────────────────
def test_health(client):
    r = client.get("/api/v1/calendar/health")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_manifest(client):
    r = client.get("/api/v1/calendar/manifest")
    assert r.status_code == 200
    assert r.json()["id"] == "calendar"


# ───────────────────────── CRUD ─────────────────────────
def test_create_and_get(client, auth):
    payload = {
        "title": "上课",
        "start_at": _iso(datetime(2026, 9, 15, 8, 0, tzinfo=TZ)),
        "end_at": _iso(datetime(2026, 9, 15, 10, 0, tzinfo=TZ)),
    }
    r = client.post("/api/v1/calendar/events", json=payload, headers=auth)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "上课"
    eid = body["id"]

    r2 = client.get(f"/api/v1/calendar/events/{eid}", headers=auth)
    assert r2.status_code == 200
    assert r2.json()["title"] == "上课"


def test_update_returns_clamped_children(client, auth):
    parent = {
        "title": "父块",
        "start_at": _iso(datetime(2026, 9, 15, 9, 0, tzinfo=TZ)),
        "end_at": _iso(datetime(2026, 9, 15, 12, 0, tzinfo=TZ)),
    }
    rp = client.post("/api/v1/calendar/events", json=parent, headers=auth)
    pid = rp.json()["id"]
    child = {
        "title": "子块",
        "start_at": _iso(datetime(2026, 9, 15, 9, 30, tzinfo=TZ)),
        "end_at": _iso(datetime(2026, 9, 15, 10, 30, tzinfo=TZ)),
        "parent_id": pid,
    }
    rc = client.post(f"/api/v1/calendar/events/{pid}/children", json=child, headers=auth)
    assert rc.status_code == 201, rc.text
    cid = rc.json()["id"]

    # 把父块整体右移 2 小时：子块应跟随并仍在父块内
    upd = {
        "start_at": _iso(datetime(2026, 9, 15, 11, 0, tzinfo=TZ)),
        "end_at": _iso(datetime(2026, 9, 15, 14, 0, tzinfo=TZ)),
    }
    ru = client.patch(f"/api/v1/calendar/events/{pid}", json=upd, headers=auth)
    assert ru.status_code == 200, ru.text
    tree = ru.json()
    kid = next(c for c in tree["children"] if c["id"] == cid)
    # 子块跟随 +2h：9:30->11:30, 10:30->12:30（本地），且仍在父块 [11:00,14:00] 内。
    # ★ 2026-09-28（主人）：出参改为**主人本地时区**（原为 UTC）——
    #   于是断言恢复"本地串"的原意（早期版本正是这么写的，当时因出参是 UTC 才改成 Z）。
    assert kid["start_at"].endswith("11:30:00+08:00"), kid["start_at"]
    assert kid["end_at"].endswith("12:30:00+08:00"), kid["end_at"]


# ───────────────────────── 树形（无 N+1） ─────────────────────────
def test_list_range_tree_single_query(client, auth):
    payload = {
        "title": "复习周",
        "start_at": _iso(datetime(2026, 9, 14, 8, 0, tzinfo=TZ)),
        "end_at": _iso(datetime(2026, 9, 14, 22, 0, tzinfo=TZ)),
        "children": [
            {
                "title": "子块A",
                "start_at": _iso(datetime(2026, 9, 14, 9, 0, tzinfo=TZ)),
                "end_at": _iso(datetime(2026, 9, 14, 10, 0, tzinfo=TZ)),
                "children": [
                    {
                        "title": "孙块",
                        "start_at": _iso(datetime(2026, 9, 14, 9, 15, tzinfo=TZ)),
                        "end_at": _iso(datetime(2026, 9, 14, 9, 45, tzinfo=TZ)),
                    }
                ],
            }
        ],
    }
    r = client.post("/api/v1/calendar/events", json=payload, headers=auth)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    frm = _iso(datetime(2026, 9, 14, 0, 0, tzinfo=TZ))
    to = _iso(datetime(2026, 9, 15, 0, 0, tzinfo=TZ))
    rl = client.get(
        f"/api/v1/calendar/events?from={frm}&to={to}", headers=auth
    )
    assert rl.status_code == 200, rl.text
    roots = rl.json()
    # 一次查询组装出三层树，根只有一个
    assert len(roots) == 1
    root = roots[0]
    assert root["id"] == pid
    assert len(root["children"]) == 1
    grandchild = root["children"][0]["children"]
    assert len(grandchild) == 1 and grandchild[0]["title"] == "孙块"

    # flat 模式不挂树
    rf = client.get(
        f"/api/v1/calendar/events?from={frm}&to={to}&include_children=false&flat=true",
        headers=auth,
    )
    assert rf.status_code == 200
    assert all(len(x["children"]) == 0 for x in rf.json())


# ───────────────────────── 跨天 ─────────────────────────
def test_cross_day_event(client, auth):
    # 周五 20:00 -> 周六 02:00（跨 2 天）
    payload = {
        "title": "跨天晚会",
        "start_at": _iso(datetime(2026, 9, 18, 20, 0, tzinfo=TZ)),
        "end_at": _iso(datetime(2026, 9, 19, 2, 0, tzinfo=TZ)),
        "span_days": 2,
    }
    r = client.post("/api/v1/calendar/events", json=payload, headers=auth)
    assert r.status_code == 201, r.text
    # 周五这一天能查到
    frm = _iso(datetime(2026, 9, 18, 0, 0, tzinfo=TZ))
    to = _iso(datetime(2026, 9, 19, 0, 0, tzinfo=TZ))
    rl = client.get(f"/api/v1/calendar/events?from={frm}&to={to}", headers=auth)
    assert rl.status_code == 200
    assert any(e["title"] == "跨天晚会" for e in rl.json())


# ───────────────────────── free-slots ─────────────────────────
def test_free_slots_two_busy_blocks(client, auth):
    b1 = {
        "title": "早会",
        "start_at": _iso(datetime(2026, 10, 1, 1, 0, tzinfo=TZ)),
        "end_at": _iso(datetime(2026, 10, 1, 3, 0, tzinfo=TZ)),
    }
    b2 = {
        "title": "午休",
        "start_at": _iso(datetime(2026, 10, 1, 6, 0, tzinfo=TZ)),
        "end_at": _iso(datetime(2026, 10, 1, 7, 0, tzinfo=TZ)),
    }
    client.post("/api/v1/calendar/events", json=b1, headers=auth)
    client.post("/api/v1/calendar/events", json=b2, headers=auth)

    r = client.get("/api/v1/calendar/free-slots?date=2026-10-01&min_hours=1", headers=auth)
    assert r.status_code == 200, r.text
    slots = r.json()
    # 空闲段：00:00-01:00(1h)、03:00-06:00(3h)、07:00-24:00(17h)
    # ★ 早期版本的期望值算错了：b2 是 06:00→07:00 只有 **1h**（不是 2h），
    #   忙碌合计 2h+1h=3h，空闲 = 24-3 = **21h**（漏算了 00:00-01:00 那一段）。
    total = round(sum(s["hours"] for s in slots), 2)
    assert total == 21.0, slots
    assert any(abs(s["hours"] - 17.0) < 0.01 for s in slots)
    assert any(abs(s["hours"] - 3.0) < 0.01 for s in slots)


def test_free_slots_min_hours_filters(client, auth):
    # ★ 忙碌事件自己建，不依赖上一条测试的残留（测试顺序依赖是脆弱测试）
    b1 = {
        "title": "实验",
        "start_at": _iso(datetime(2026, 10, 1, 1, 0, tzinfo=TZ)),
        "end_at": _iso(datetime(2026, 10, 1, 3, 0, tzinfo=TZ)),
    }
    b2 = {
        "title": "午休",
        "start_at": _iso(datetime(2026, 10, 1, 6, 0, tzinfo=TZ)),
        "end_at": _iso(datetime(2026, 10, 1, 7, 0, tzinfo=TZ)),
    }
    client.post("/api/v1/calendar/events", json=b1, headers=auth)
    client.post("/api/v1/calendar/events", json=b2, headers=auth)

    r = client.get("/api/v1/calendar/free-slots?date=2026-10-01&min_hours=4", headers=auth)
    assert r.status_code == 200, r.text
    slots = r.json()
    # 1h 与 3h 的空闲段被过滤，只剩 07:00-24:00(17h)
    assert all(s["hours"] >= 4 for s in slots), slots
    assert any(abs(s["hours"] - 17.0) < 0.01 for s in slots)


# ───────────────────────── 边界测试 ─────────────────────────
def test_boundary_empty_and_chinese_and_long(client, auth):
    # 空标题 -> 422
    r = client.post(
        "/api/v1/calendar/events",
        json={
            "title": "",
            "start_at": _iso(datetime(2026, 9, 15, 8, 0, tzinfo=TZ)),
            "end_at": _iso(datetime(2026, 9, 15, 9, 0, tzinfo=TZ)),
        },
        headers=auth,
    )
    assert r.status_code == 422

    # 中文标题 -> 正常
    r = client.post(
        "/api/v1/calendar/events",
        json={
            "title": "上课 · 无机化学（中文测试）",
            "start_at": _iso(datetime(2026, 9, 15, 8, 0, tzinfo=TZ)),
            "end_at": _iso(datetime(2026, 9, 15, 9, 0, tzinfo=TZ)),
        },
        headers=auth,
    )
    assert r.status_code == 201

    # 超长标题 -> 422（不崩）
    r = client.post(
        "/api/v1/calendar/events",
        json={
            "title": "长" * 500,
            "start_at": _iso(datetime(2026, 9, 15, 8, 0, tzinfo=TZ)),
            "end_at": _iso(datetime(2026, 9, 15, 9, 0, tzinfo=TZ)),
        },
        headers=auth,
    )
    assert r.status_code == 422


def test_boundary_no_timezone_rejected(client, auth):
    r = client.post(
        "/api/v1/calendar/events",
        json={
            "title": "无时区",
            "start_at": "2026-09-15T08:00:00",
            "end_at": "2026-09-15T09:00:00",
        },
        headers=auth,
    )
    # 服务端 to_utc 对 naive 抛 ValidationError -> 422
    assert r.status_code in (400, 422), r.text


def test_child_cannot_exceed_parent(client, auth):
    parent = {
        "title": "父",
        "start_at": _iso(datetime(2026, 9, 16, 9, 0, tzinfo=TZ)),
        "end_at": _iso(datetime(2026, 9, 16, 12, 0, tzinfo=TZ)),
    }
    rp = client.post("/api/v1/calendar/events", json=parent, headers=auth)
    pid = rp.json()["id"]
    # 子块企图超出父块 -> 被钳制回父块内，不会 500
    child = {
        "title": "越界子块",
        "start_at": _iso(datetime(2026, 9, 16, 0, 0, tzinfo=TZ)),
        "end_at": _iso(datetime(2026, 9, 16, 23, 0, tzinfo=TZ)),
        "parent_id": pid,
    }
    rc = client.post(f"/api/v1/calendar/events/{pid}/children", json=child, headers=auth)
    assert rc.status_code == 201, rc.text
    # 钳制后子块仍在父块 [09:00,12:00] 内
    cc = client.get(f"/api/v1/calendar/events/{pid}", headers=auth).json()
    kid = cc["children"][0]
    # 越界子块 00:00-23:00 被钳进父块 [09:00,12:00]（本地）。
    # ★ 2026-09-28：出参改本地时区（同上）→ 断言写本地串。
    assert kid["start_at"].endswith("09:00:00+08:00"), kid["start_at"]
    assert kid["end_at"].endswith("12:00:00+08:00"), kid["end_at"]


# ── U2 聚合数据源：/today-summary ──────────────────────────


def test_today_summary_200_empty_when_no_events_today(client, auth):
    """今天没事件 → 200 + 空结构（不抛错，规范 §2「缺数据返回空结构」）。"""
    r = client.get("/api/v1/calendar/today-summary", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "今日日程"
    assert body["items"] == []
    assert body["count"] == 0


def test_today_summary_includes_todays_event(client, auth):
    """建一个今天的事件 → 摘要 count=1 且 items 含其标题。"""
    # ★ 2026-09-25 修：改用 _today_within —— 原 now+2h 在 22:00 后会跨天（时间 flaky）
    start_dt = _today_within(2)
    start = start_dt.isoformat()
    end = (start_dt + timedelta(hours=1)).isoformat()
    r = client.post(
        "/api/v1/calendar/events",
        json={"title": "U2测试事件", "start_at": start, "end_at": end},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    r = client.get("/api/v1/calendar/today-summary", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert any("U2测试事件" in it["text"] for it in body["items"])


def test_today_summary_401_without_token(client):
    """★ -3：绝不裸奔 —— 无 token 一律 401。"""
    r = client.get("/api/v1/calendar/today-summary")
    assert r.status_code == 401


def test_today_summary_shows_local_hhmm(client, auth):
    """★ 2026-09-28（主人「改回本地时区」）：today-summary 的 items[].text 前 5 字
    必须是**本地 HH:MM**。

    修前：路由按字符串切片取小时（`str(start)[11:16]`），而出参是 UTC ⇒
    **下午 3 点的课会显示成 "07:00"**（用户可见的真 bug）。
    改为本地时区出参后自动治好 —— 本用例守的就是它。
    """
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz)
    start = now + timedelta(hours=3)
    if start.date() != now.date():  # 跨天则钳到当天 23:59（与文件内 _today_at 同款防 flaky）
        start = now.replace(hour=23, minute=59, second=0, microsecond=0)
    r = client.post(
        "/api/v1/calendar/events",
        json={"title": "本地时间校验", "start_at": start.isoformat(),
              "end_at": (start + timedelta(minutes=30)).isoformat()},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    s = client.get("/api/v1/calendar/today-summary", headers=auth)
    assert s.status_code == 200, s.text
    items = s.json()["items"]
    assert items, s.json()
    assert items[0]["text"].startswith(f"{start.hour:02d}:{start.minute:02d}"), (
        items[0], f"期望本地 {start.hour:02d}:{start.minute:02d}")
