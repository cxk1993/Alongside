"""E3 · health 期望态 reconcile 测试。"""
from __future__ import annotations
from core.db_scope import declare_scope  # 2026-11-09 作用域翻转：显式声明

import os
from datetime import UTC, date, datetime, timedelta

os.environ["DB_PATH"] = "./data/tmp_t_e3_reconcile.db"
os.environ["HEALTH_RECONCILE_ENABLED"] = "false"

import pytest
from sqlmodel import Session, create_engine, select

from core.deps import set_engine
from core.events import event_bus
from db.base import SQLModel
from modules.health.models import HealthRecord
from modules.health.reconcile import reconcile_followups, reconcile_status
from modules.todo.health_link import (
    create_followup_todo,
    find_existing,
    source_path_for,
    uninstall_health_link_for_tests,
)
from modules.todo.models import TodoItem


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/e3.db")
    SQLModel.metadata.create_all(engine)
    session = declare_scope(Session(engine))

    def _factory() -> Session:
        return declare_scope(Session(engine))

    set_engine(_factory)
    yield session
    uninstall_health_link_for_tests()
    session.close()


def _add_record(
    db: Session,
    *,
    title: str,
    followup: bool,
    due: date | None = None,
    record_id: str | None = None,
) -> HealthRecord:
    rec = HealthRecord(
        kind="appointment",
        title=title,
        occurred_at=datetime.now(UTC) - timedelta(days=1),
        followup_needed=followup,
        followup_due=due,
    )
    if record_id:
        rec.id = record_id
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def test_reconcile_publishes_desired_only(db: Session) -> None:
    due = date.today() + timedelta(days=2)
    _add_record(db, title="复诊", followup=True, due=due, record_id="rec-a")
    _add_record(db, title="已好转", followup=False, record_id="rec-b")

    seen: list[dict] = []
    original = event_bus.publish

    def spy(topic, payload, source="kernel"):
        out = original(topic, payload, source=source)
        if topic == "health.care.requested":
            seen.append(payload if isinstance(payload, dict) else {})
        return out

    event_bus.publish = spy
    try:
        summary = reconcile_followups(db)
    finally:
        event_bus.publish = original

    assert summary["ok"] is True
    assert summary["desired_count"] == 1
    assert summary["published_count"] == 1
    assert seen and seen[0]["record_id"] == "rec-a"
    assert seen[0]["idempotency_key"].startswith("health-followup:rec-a:")


def test_reconcile_broadcast_converges_todo_idempotent(db: Session) -> None:
    from modules.todo.health_link import install_health_link

    due = date.today() + timedelta(days=1)
    _add_record(db, title="用药提醒", followup=True, due=due, record_id="rec-med")
    install_health_link()

    s1 = reconcile_followups(db)
    assert s1["published_count"] == 1
    key = f"health-followup:rec-med:{due.isoformat()}"
    assert find_existing(db, key) is not None
    n1 = len(db.exec(select(TodoItem)).all())

    # 第二轮广播：幂等，不再新建
    s2 = reconcile_followups(db)
    assert s2["published_count"] == 1
    n2 = len(db.exec(select(TodoItem)).all())
    assert n1 == n2 == 1
    item = db.exec(select(TodoItem)).first()
    assert item is not None
    assert item.source_path == source_path_for(key)


def test_reconcile_status_readonly(db: Session) -> None:
    _add_record(db, title="化验", followup=True, due=date.today(), record_id="rec-lab")
    st = reconcile_status(db)
    assert st["desired_count"] == 1
    assert st["scheduler_enabled"] is False
    assert st["records"][0]["id"] == "rec-lab"


def test_create_followup_helper_still_works(db: Session) -> None:
    key = "health-followup:x:2026-09-21"
    out = create_followup_todo(
        db,
        {
            "record_id": "x",
            "title": "牙科",
            "suggest_due": "2026-09-21",
            "idempotency_key": key,
        },
    )
    assert out is not None
    assert find_existing(db, key) is not None
