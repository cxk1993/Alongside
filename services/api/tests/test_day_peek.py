"""U2 day_peek / day_dots 测试（r7 环回聚合：能力发现 + fetch 注入 + 软失败）。"""
from __future__ import annotations

import os

os.environ["DB_PATH"] = "./data/tmp_day_peek.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from db.base import SQLModel  # noqa: E402
from modules.dashboard import aggregator  # noqa: E402


def _fetch_factory(mapping: dict[str, object]):
    """按路径前缀返回 canned 响应（模拟各源的环回端点；Exception 则模拟故障）。"""

    async def fetch(path: str) -> object:
        for prefix, resp in mapping.items():
            if path.startswith(prefix):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise RuntimeError(f"no canned response for {path}")

    return fetch


@pytest.fixture(scope="module")
def client_auth():
    init_engine()
    SQLModel.metadata.create_all(get_engine())
    client = TestClient(create_app())
    auth = {"Authorization": f"Bearer {create_access_token('admin')}"}
    yield client, auth
    aggregator.set_fetch_override(None)


def test_day_peek_shape(client_auth):
    client, auth = client_auth
    app = client.app
    # 四个内置模块的 manifest 已声明 x.day.peek（发现期注册进 app.state.modules）
    assert {"calendar", "todo", "diary", "review"} <= set(app.state.modules.keys())
    aggregator.set_fetch_override(
        _fetch_factory(
            {
                "/api/v1/calendar/day-peek": {
                    "items": [{"id": "c1", "title": "开周会", "start": "2026-09-23T10:00:00"}]
                },
                "/api/v1/todo/day-peek": {"items": [{"id": "t1", "title": "交房租", "done": False}]},
                "/api/v1/diary/day-peek": {"items": []},
                "/api/v1/review/day-peek": {"items": [{"id": "r1", "title": "2026-09-23"}]},
            }
        )
    )
    try:
        r = client.get("/api/v1/dashboard/day-peek?date=2026-09-23", headers=auth)
        assert r.status_code == 200
        body = r.json()
        assert body["date"] == "2026-09-23"
        assert set(body["list"]) == {"calendar", "todo", "diary", "review"}
        assert body["marks"]["calendar"] == 1
        assert body["marks"]["diary"] == 0
        assert body["dots"]["calendar"] is True
        assert body["dots"]["diary"] is False
        assert body["list"]["calendar"][0]["title"] == "开周会"
    finally:
        aggregator.set_fetch_override(None)


def test_day_dots_shape_and_merge(client_auth):
    client, auth = client_auth
    aggregator.set_fetch_override(
        _fetch_factory(
            {
                "/api/v1/calendar/day-dots": {"dates": ["2026-09-05"]},
                "/api/v1/todo/day-dots": {"dates": ["2026-09-05", "2026-09-06"]},
                "/api/v1/diary/day-dots": {"dates": []},
                "/api/v1/review/day-dots": {"dates": ["2026-09-06"]},
            }
        )
    )
    try:
        r = client.get("/api/v1/dashboard/day-dots?from=2026-09-01&to=2026-09-30", headers=auth)
        assert r.status_code == 200
        body = r.json()
        assert body["2026-09-05"] == ["calendar", "todo"]
        assert body["2026-09-06"] == ["review", "todo"]  # sorted
    finally:
        aggregator.set_fetch_override(None)


def test_day_peek_new_provider_auto_appears(client_auth):
    """r7 验收：运行期声明 x.day.peek 的新模块（如热装插件）自动参与聚合。"""
    client, auth = client_auth
    app = client.app
    app.state.modules["mood"] = {"id": "mood", "provides": ["x.day.peek"]}
    aggregator.set_fetch_override(
        _fetch_factory(
            {
                "/api/v1/mood/day-peek": {"items": [{"text": "心情不错"}]},
                "/api/v1/calendar/day-peek": {"items": []},
                "/api/v1/todo/day-peek": {"items": []},
                "/api/v1/diary/day-peek": {"items": []},
                "/api/v1/review/day-peek": {"items": []},
            }
        )
    )
    try:
        body = client.get("/api/v1/dashboard/day-peek?date=2026-09-23", headers=auth).json()
        assert "mood" in body["list"]
        assert body["list"]["mood"] == [{"text": "心情不错"}]
    finally:
        aggregator.set_fetch_override(None)
        app.state.modules.pop("mood", None)


def test_day_peek_soft_fail_skips_broken_source(client_auth):
    """单家失败 → 该源缺席，不拖累其余（缺一不塌）。"""
    client, auth = client_auth
    aggregator.set_fetch_override(
        _fetch_factory(
            {
                "/api/v1/calendar/day-peek": RuntimeError("boom"),
                "/api/v1/todo/day-peek": {"items": [{"id": "t1", "title": "交房租"}]},
                "/api/v1/diary/day-peek": {"items": []},
                "/api/v1/review/day-peek": {"items": []},
            }
        )
    )
    try:
        body = client.get("/api/v1/dashboard/day-peek?date=2026-09-23", headers=auth).json()
        assert "calendar" not in body["list"]
        assert body["list"]["todo"][0]["title"] == "交房租"
    finally:
        aggregator.set_fetch_override(None)


def test_day_peek_requires_auth(client_auth):
    client, _ = client_auth
    assert client.get("/api/v1/dashboard/day-peek").status_code == 401
