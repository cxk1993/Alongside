"""ISSUE-007 · todo 订阅 health.care.requested 测试。"""
from __future__ import annotations
from core.db_scope import declare_scope  # 2026-11-09 作用域翻转：显式声明

import os
from datetime import date, timedelta

os.environ["DB_PATH"] = "./data/tmp_t_issue007.db"

import pytest
from sqlmodel import Session, create_engine, select

from core.deps import set_engine
from core.events import event_bus
from db.base import SQLModel
from modules.todo.health_link import (
    EVENT_CARE,
    create_followup_todo,
    install_health_link,
    source_path_for,
    uninstall_health_link_for_tests,
)
from modules.todo.models import TodoItem, tags_from_json


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t007.db")
    SQLModel.metadata.create_all(engine)
    session = declare_scope(Session(engine))

    def _factory() -> Session:
        return declare_scope(Session(engine))

    set_engine(_factory)
    yield session
    session.close()


def _payload(key: str, title: str = "心内科复诊", due: str | None = None) -> dict:
    return {
        "record_id": "rec-1",
        "kind": "appointment",
        "title": title,
        "suggest_due": due,
        "source": "health",
        "idempotency_key": key,
    }


def test_create_followup_from_payload(db: Session) -> None:
    due = (date.today() + timedelta(days=2)).isoformat()
    key = f"health-followup:r1:{due}"
    out = create_followup_todo(db, _payload(key, title="心内科复诊", due=due))
    assert out is not None
    assert out["text"] == "跟进：心内科复诊"
    item = db.get(TodoItem, out["id"])
    assert item is not None
    assert item.source_path == source_path_for(key)
    assert "health" in tags_from_json(item.tags)
    assert item.done is False


def test_idempotent_same_key(db: Session) -> None:
    key = "health-followup:r2:2026-09-25"
    a = create_followup_todo(db, _payload(key, due="2026-09-25"))
    b = create_followup_todo(db, _payload(key, due="2026-09-25"))
    assert a is not None and b is None
    assert len(db.exec(select(TodoItem)).all()) == 1


def test_empty_due_sets_due_at(db: Session) -> None:
    out = create_followup_todo(db, _payload("health-followup:r3:none", due=None))
    assert out is not None
    item = db.get(TodoItem, out["id"])
    assert item is not None and item.due_at is not None


def test_event_hook_creates_todo(db: Session) -> None:
    uninstall_health_link_for_tests()
    install_health_link()
    key = "health-followup:r4:2026-09-26"
    event_bus.publish(EVENT_CARE, _payload(key, due="2026-09-26"), source="health")
    assert any(e["topic"] == EVENT_CARE for e in event_bus._history)
    found = db.exec(
        select(TodoItem).where(TodoItem.source_path == source_path_for(key))
    ).first()
    assert found is not None
    assert found.text.startswith("跟进：")


def test_event_hook_idempotent(db: Session) -> None:
    uninstall_health_link_for_tests()
    install_health_link()
    key = "health-followup:r5:2026-09-27"
    event_bus.publish(EVENT_CARE, _payload(key, due="2026-09-27"), source="health")
    event_bus.publish(EVENT_CARE, _payload(key, due="2026-09-27"), source="health")
    rows = db.exec(
        select(TodoItem).where(TodoItem.source_path == source_path_for(key))
    ).all()
    assert len(rows) == 1
