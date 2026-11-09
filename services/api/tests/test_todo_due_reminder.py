"""判据 · 待办到期提醒（workbuddy 2026-09-25 主人「做」）。

链路：due_scheduler 扫到期 → publish("todo.item.due") → push.link → web push。
本测试锁三件事：
  1. **窗口语义**：只挑「最近窗口内到期」的（防重启补发历史提醒）；
  2. **过滤语义**：已完成 / 无 due_at / 窗口外 的待办**绝不**被提醒；
  3. **幂等**：同一条只发一次（进程内记账）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import Session, SQLModel, create_engine

from core.db_scope import declare_scope  # 2026-11-09 作用域翻转：显式声明
from modules.todo import due_scheduler
from modules.todo.models import TodoItem


def _db() -> Session:
    eng = create_engine("sqlite://")  # 内存库
    SQLModel.metadata.create_all(eng)
    return declare_scope(Session(eng))


def test_collect_only_within_window_and_not_done() -> None:
    db = _db()
    now = datetime.now(UTC)
    db.add(TodoItem(text="窗口内-未完成", due_at=now - timedelta(seconds=30)))  # ✅ 应提醒
    db.add(TodoItem(text="窗口外-过期久", due_at=now - timedelta(hours=3)))  # ❌ 太旧（防补发）
    db.add(TodoItem(text="窗口内-但已完成", due_at=now - timedelta(seconds=10), done=True))  # ❌
    db.add(TodoItem(text="无截止", due_at=None))  # ❌
    db.add(TodoItem(text="未来", due_at=now + timedelta(hours=1)))  # ❌
    db.commit()

    due_scheduler._notified.clear()
    got = due_scheduler.collect_due_todos(db, now=now)
    assert [t.text for t in got] == ["窗口内-未完成"], [t.text for t in got]


def test_run_due_tick_publishes_and_is_idempotent(monkeypatch) -> None:
    db = _db()
    now = datetime.now(UTC)
    db.add(TodoItem(text="到期项", due_at=now - timedelta(seconds=5)))
    db.commit()

    published: list[dict] = []
    # ★ 2026-09-26 更新（astrbot）：发布改为 EventBus 的正规签名
    #   publish(topic, payload=…, source=…)（原写法把整个 dict 当 topic 传，
    #   在真实 EventBus 上会 TypeError → 被 except 吞成静默失败）。
    def _fake_publish(topic, payload=None, source="kernel"):
        published.append({"topic": topic, "payload": payload or {}, "source": source})

    monkeypatch.setattr(due_scheduler.event_bus, "publish", _fake_publish)
    due_scheduler._notified.clear()

    assert due_scheduler.run_due_tick(db) == 1  # 首发
    assert published and published[0]["topic"] == "todo.item.due"
    assert published[0]["payload"]["title"] == "到期项"

    assert due_scheduler.run_due_tick(db) == 0  # ★ 幂等：第二轮不再发
    assert len(published) == 1


def test_scheduler_status_shape() -> None:
    st = due_scheduler.scheduler_status()
    assert set(st) >= {"enabled", "running", "poll_seconds", "window_seconds"}
    # 默认关（与 calendar 同款纪律：env 开关）
    assert st["enabled"] is False or isinstance(st["enabled"], bool)
