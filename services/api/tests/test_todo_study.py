"""★ 学业页相关测试（astrbot 2026-09-26 · 主人「学业页」）

覆盖：
  S1 · tag 层级匹配：`tag=学业` 命中 `学业` 与 `学业/高数`、`学业/大物`
  S2 · tag 精确：`tag=学业/高数` 只命中该课
  S3 · 不误伤：`学业证` 不算 `学业` 的子级
  S4 · 学业项用独立提前量（24h），日常项仍 30min（含窗口实测）
"""
import os

# ★ 必须在 import 任何内核/模块之前设置临时库（init_engine 只认一次）。
os.environ["DB_PATH"] = "./data/tmp_study_page.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.todo.models import TodoItem  # noqa: E402
from modules.todo.service import tag_hit  # noqa: E402
from core.db_scope import declare_scope  # 2026-11-09 作用域翻转：显式声明


@pytest.fixture(scope="module")
def client():
    init_engine()
    engine = get_engine()
    TodoItem.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    """清表（★ 容错：纯函数测试时表可能尚未建 —— 跳过即可）。"""
    try:
        engine = get_engine()
        with declare_scope(Session(engine)) as s:
            s.exec(text("DELETE FROM todo_item"))
            s.commit()
    except Exception:  # noqa: BLE001 —— 未建表/未初始化时静默跳过
        pass
    yield


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


# ───────────────────────── S1–S3 · 层级标签（纯函数）─────────────────────────
def test_tag_hit_hierarchy() -> None:
    """S1：`学业` 命中自身与子级。"""
    assert tag_hit(["学业"], "学业") is True
    assert tag_hit(["学业/高数"], "学业") is True
    assert tag_hit(["学业/大物"], "学业") is True
    assert tag_hit(["工作", "学业/高数"], "学业") is True


def test_tag_hit_exact_child() -> None:
    """S2：`学业/高数` 只命中该课。"""
    assert tag_hit(["学业/高数"], "学业/高数") is True
    assert tag_hit(["学业/大物"], "学业/高数") is False
    assert tag_hit(["学业"], "学业/高数") is False


def test_tag_hit_no_false_positive() -> None:
    """S3：不误伤 —— 前缀必须落在分隔符上。"""
    assert tag_hit(["学业证"], "学业") is False
    assert tag_hit(["日常"], "学业") is False
    assert tag_hit([], "学业") is False
    assert tag_hit(["学业"], "") is False


# ───────────────────────── S1 端到端 · API 层 ─────────────────────────
def test_list_items_tag_hierarchy(client: TestClient, auth: dict) -> None:
    """S1 端到端：GET /items?tag=学业 能筛出子标签项。"""
    for text_, tags in [
        ("高数作业", ["学业/高数"]),
        ("大物报告", ["学业/大物"]),
        ("买牛奶", ["日常"]),
    ]:
        r = client.post("/api/v1/todo/items", json={"text": text_, "tags": tags}, headers=auth)
        assert r.status_code == 201, r.text

    r = client.get("/api/v1/todo/items?tag=学业", headers=auth)
    assert r.status_code == 200, r.text
    texts = [it["text"] for it in r.json()["items"]]
    assert "高数作业" in texts and "大物报告" in texts
    assert "买牛奶" not in texts

    r2 = client.get("/api/v1/todo/items?tag=学业/高数", headers=auth)
    texts2 = [it["text"] for it in r2.json()["items"]]
    assert texts2 == ["高数作业"], texts2


# ───────────────────────── S4 · 学业提前量 ─────────────────────────
class _FakeItem:
    def __init__(self, tags: str | None):
        self.tags = tags


def test_study_lead_detection() -> None:
    """S4a：学业判定（含子标签）。"""
    from modules.todo.due_scheduler import _is_study

    assert _is_study(_FakeItem('["学业"]')) is True
    assert _is_study(_FakeItem('["学业/高数"]')) is True
    assert _is_study(_FakeItem('["日常"]')) is False
    assert _is_study(_FakeItem(None)) is False


def test_study_lead_value() -> None:
    """S4b：学业项 24h，日常项 30min。"""
    from modules.todo.due_scheduler import (
        DEFAULT_LEAD_MINUTES,
        DEFAULT_STUDY_LEAD_MINUTES,
        _lead_for,
    )

    assert _lead_for(_FakeItem('["学业/高数"]')) == DEFAULT_STUDY_LEAD_MINUTES
    assert _lead_for(_FakeItem('["日常"]')) == DEFAULT_LEAD_MINUTES
    assert DEFAULT_STUDY_LEAD_MINUTES == 1440
    assert DEFAULT_LEAD_MINUTES == 30


def test_study_item_enters_window_24h_early() -> None:
    """S4c：20 小时后到期的**学业**项现在应进窗口；日常项不应（内存库，独立于 app）。"""
    from datetime import UTC, datetime, timedelta

    from modules.todo import due_scheduler

    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    db = declare_scope(Session(eng))

    due_scheduler._notified.clear()
    now = datetime.now(UTC)

    db.add(TodoItem(text="高数作业", due_at=now + timedelta(hours=20), tags='["学业/高数"]'))
    db.add(TodoItem(text="买牛奶", due_at=now + timedelta(hours=20), tags='["日常"]'))
    db.commit()

    names = [h.text for h in due_scheduler.collect_due_todos(db, now=now)]
    assert "高数作业" in names, names
    assert "买牛奶" not in names, names
    db.close()
