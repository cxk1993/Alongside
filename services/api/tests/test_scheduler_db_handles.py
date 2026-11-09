"""★ 防回潮判据 —— scheduler 等**非 Depends 场景必须拿到 Session 实例**。

背景（根因的连带炸点）：`get_db` 生成器化后，`db = get_db()` 直调拿到的是
**generator 对象** → `db.exec(...)` 抛 AttributeError → 被 except 吞掉只留 warning →
**日历提醒 / 财务同步 / 健康 reconcile / push 联动 / 待办联动五条后台线静默全灭**
（既有测试网不覆盖这些路径，53 绿也测不出）。

本文件即要求的「修后加一枚最小回归测试」的落地：
  1. **源码级防回潮**：五处（表格见下）不得再出现 `= get_db()` 直调；
  2. **契约级**：`get_db` 必须是生成器函数（根因修复本体）；
  3. **行为级**：`db_session()` 产出实例且**退出必 close**（连接回池）。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from core import deps

API_DIR = Path(__file__).resolve().parents[1]

# ★ 点名的五处（**只增不减**：将来新增直调点必须补进这张表）
SCHEDULER_DB_CALLSITES = [
    "modules/calendar/reminder_scheduler.py",
    "modules/finance/scheduler.py",
    "modules/health/reconcile_scheduler.py",
    "modules/push/push_link.py",
    "modules/todo/health_link.py",
]


def test_scheduler_db_handles_use_session_not_generator() -> None:
    """源码级防回潮：五处**不得**出现 `= get_db()` 直调（会拿到 generator）。"""
    offenders: list[str] = []
    for rel in SCHEDULER_DB_CALLSITES:
        path = API_DIR / rel
        if not path.exists():  # 文件被重命名/移除时不误报
            continue
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"=\s*get_db\(\)", src):
            line_no = src[: m.start()].count("\n") + 1
            offenders.append(f"{rel}:{line_no}  {m.group(0)}")
    assert not offenders, (
        "★ 防回潮：以下直调点会拿到 generator 而非 Session —— "
        "请改用 `with kernel_session() as db:`：\n" + "\n".join(offenders)
    )


def test_get_db_is_generator_for_depends() -> None:
    """`get_db` 必须是**生成器函数** —— FastAPI 才会在请求结束自动 close（根因修复本体）。"""
    assert inspect.isgeneratorfunction(deps.get_db), (
        "get_db 退回普通函数会让 139 处 Depends(get_db) 重新裸漏连接（根因回潮）"
    )


def test_db_session_yields_instance_and_closes(monkeypatch) -> None:
    """`kernel_session()` 必须产出**实例**（非 generator），且**退出即 close**。"""
    closed = {"n": 0}

    class FakeSession:
        def close(self) -> None:
            closed["n"] += 1

    monkeypatch.setattr(deps, "_engine_factory", lambda: FakeSession())
    with deps.kernel_session() as db:
        assert isinstance(db, FakeSession), "非 Depends 场景拿到的不该是 generator"
    assert closed["n"] == 1, "退出必须 close —— 否则连接不回池（根因复发）"
