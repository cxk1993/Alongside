"""★ dispatch(mode="pi") 真执行测试。

数据库隔离：./data/tmp_t12_pi.db（照 test_agents.py 的口径，绝不碰主库）。
★ 用假 pi_client（鸭子类型）注入，不真起 pi —— 这一层只验证**编排逻辑**。
"""
from __future__ import annotations
from core.db_scope import declare_scope  # 2026-11-09 作用域翻转：显式声明

import os

os.environ["DB_PATH"] = "./data/tmp_t12_pi.db"

import pytest  # noqa: E402
from sqlmodel import Session, text  # noqa: E402

from db.engine import get_engine, init_engine  # noqa: E402
from modules.agents.models import AgentAgent, AgentDispatch, AgentTask  # noqa: E402
from modules.agents.schema import DispatchIn  # noqa: E402
from modules.agents.service import AgentsService  # noqa: E402


class FakePiClient:
    """假内部调用客户端：记录调用，按预设返回或抛错。"""

    def __init__(self, *, reply="任务完成", degraded=False, raise_exc=None):
        self.calls: list[tuple[str, dict]] = []
        self.reply = reply
        self.degraded = degraded
        self.raise_exc = raise_exc

    def post(self, path: str, **kw):
        self.calls.append((path, kw))
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.degraded:
            return {"degraded": True, "level": "L3", "detail": "pi 处于熔断态"}
        return {"reply": self.reply, "level": "L1", "settled": True, "degraded": False}


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    init_engine()
    engine = get_engine()
    AgentTask.__table__.create(bind=engine, checkfirst=True)
    AgentAgent.__table__.create(bind=engine, checkfirst=True)
    AgentDispatch.__table__.create(bind=engine, checkfirst=True)
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    engine = get_engine()
    with declare_scope(Session(engine)) as s:
        for tbl in ("agents_dispatch", "agents_task", "agents_agent"):
            try:
                s.exec(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        s.commit()
    yield


@pytest.fixture()
def db():
    engine = get_engine()
    with declare_scope(Session(engine)) as s:
        yield s


def _wait_settled(db: Session, task_id: str, timeout: float = 10.0) -> AgentTask:
    """★ dispatch 已异步化 —— 轮询等后台线程把任务推到终态。

    终态 = done / failed（running / queued 都算未收敛）。
    """
    import time as _t

    deadline = _t.time() + timeout
    t = None
    while _t.time() < deadline:
        db.expire_all()  # ★ 必须：后台线程在**另一个 session** 里写的，本 session 有缓存
        t = db.get(AgentTask, task_id)
        if t is not None and t.status in ("done", "failed", "cancelled"):
            return t
        _t.sleep(0.05)
    assert t is not None
    return t


@pytest.fixture()
def task(db: Session) -> AgentTask:
    t = AgentTask(title="查一下今天的待办", description="并汇总条数")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_pi_dispatch_writes_result_and_done(db: Session, task: AgentTask):
    """★ 成功：调 /api/v1/pi-agent/chat，结果写回 task.result，状态 → done。"""
    client = FakePiClient(reply="今天有 2 条待办")
    out = AgentsService(db).dispatch(task.id, DispatchIn(mode="pi"), pi_client=client)
    # ★ 立即返回 running（异步）
    assert out["status"] == "sent" or task.status in ("running", "queued")

    t = _wait_settled(db, task.id)          # 等后台线程跑完
    assert client.calls, "必须真的调了 pi-agent"
    path, kw = client.calls[0]
    assert path == "/api/v1/pi-agent/chat"
    body = kw.get("json") or {}
    assert body["session_id"] == f"task-{task.id}"   # ★ 任务块独立会话
    assert "查一下今天的待办" in body["message"]      # 标题进 prompt
    assert "汇总条数" in body["message"]              # 说明进 prompt

    assert t.status == "done"
    assert t.result == "今天有 2 条待办"
    assert t.result_status == "ok"


def test_pi_dispatch_degraded_marks_failed(db: Session, task: AgentTask):
    """★ Pi 降级（熔断）→ 任务落 failed + 原因入 result（编排台看得见）。"""
    client = FakePiClient(degraded=True)
    AgentsService(db).dispatch(task.id, DispatchIn(mode="pi"), pi_client=client)
    t = _wait_settled(db, task.id)
    assert t.status == "failed"
    assert "降级" in (t.result or "")
    assert t.result_status == "failed"


def test_pi_dispatch_exception_is_soft(db: Session, task: AgentTask):
    """★ 执行异常不抛给调用方（fail-soft）—— 落 failed + 错误摘要。"""
    client = FakePiClient(raise_exc=RuntimeError("pi 子进程挂了"))
    AgentsService(db).dispatch(task.id, DispatchIn(mode="pi"), pi_client=client)
    t = _wait_settled(db, task.id)
    assert t.status == "failed"
    assert "pi 子进程挂了" in (t.result or "")


def test_pi_dispatch_without_client_raises(db: Session, task: AgentTask):
    """★ 未传 client（= 能力未声明 / pi-agent 未启用）→ 明确报错，不静默。"""
    from core.errors import ValidationError

    with pytest.raises(ValidationError) as ei:
        AgentsService(db).dispatch(task.id, DispatchIn(mode="pi"))
    assert "pi.chat.write" in str(ei.value)


def test_pi_dispatch_returns_immediately(db: Session, task: AgentTask):
    """★ 核心：dispatch **不等 Pi 跑完就返回**（否则 180s 会把 HTTP 挂死）。"""
    import time as _t

    class SlowClient(FakePiClient):
        def post(self, path: str, **kw):
            _t.sleep(1.5)          # 模拟 Pi 慢
            return super().post(path, **kw)

    client = SlowClient(reply="慢任务完成")
    t0 = _t.time()
    AgentsService(db).dispatch(task.id, DispatchIn(mode="pi"), pi_client=client)
    elapsed = _t.time() - t0
    assert elapsed < 1.0, f"dispatch 应立即返回，实测 {elapsed:.2f}s"
    # 后台仍在跑 → 稍后收敛
    t = _wait_settled(db, task.id, timeout=10)
    assert t.status == "done" and t.result == "慢任务完成"


def test_other_modes_still_offline(db: Session, task: AgentTask):
    """★ 回归：非 pi 模式仍只记账（不调外网）—— 行为不变。"""
    out = AgentsService(db).dispatch(task.id, DispatchIn(mode="poll"))
    db.refresh(task)
    assert task.status == "queued"        # 推进到 queued，但不 done
    assert out["status"] == "sent"
