"""T04 数据层单测：mixin建表 / repo分页边界 / todo真实样例 / 迁移 / 并发 / 备份 / 字典一致。"""
from __future__ import annotations

import sys
import threading
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session, select

from core.config import get_settings
from db.base import Base, utcnow
from db.engine import make_engine
from db.models import AppSetting, AuditLog
from db.repo import Repo, decode_cursor, encode_cursor
from db.todo_parser import parse_todo_line, to_todo_line


@pytest.fixture(name="engine")
def engine_fixture(tmp_path):
    return make_engine(f"sqlite:///{tmp_path.as_posix()}/t.db")


# ---------------------------------------------------------------------------
# 步骤 2：Base / Mixin
# ---------------------------------------------------------------------------
def test_mixin_creates_table_and_defaults(engine: None) -> None:
    Base.metadata.create_all(engine)
    row = AuditLog(actor="t", action="test")
    with Session(engine) as s:
        s.add(row)
        s.commit()
        s.refresh(row)
    assert len(row.id) == 32
    assert row.created_at.tzinfo is not None  # UTC 往返保时区
    assert row.updated_at >= row.created_at


def test_naive_datetime_rejected(engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(AuditLog(actor="t", action="naive", at=datetime(2026, 9, 15, 8, 0)))
        with pytest.raises(Exception):  # noqa: B017,PT011 —— naive 必须在写入时报错
            s.commit()


# ---------------------------------------------------------------------------
# 步骤 6：repo 分页边界
# ---------------------------------------------------------------------------
def test_repo_pagination_boundaries(engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        repo = Repo(s, AppSetting)
        # 边界一：空结果
        items, nxt = repo.page()
        assert items == [] and nxt is None
        for i in range(7):
            repo.create(key=f"k{i}", value_json=str(i))
        # 第一页
        items, nxt = repo.page(limit=3)
        assert len(items) == 3 and nxt is not None
        # 中间页
        items, nxt2 = repo.page(limit=3, cursor=nxt)
        assert len(items) == 3 and nxt2 is not None
        # 最后一页：不足 limit → next_cursor 为 None
        items, nxt3 = repo.page(limit=3, cursor=nxt2)
        assert len(items) == 1 and nxt3 is None
        # 无效 cursor 抛错（不静默当第一页）
        with pytest.raises(ValueError):
            decode_cursor("garbage!!")
        assert decode_cursor(encode_cursor(5)) == 5


# ---------------------------------------------------------------------------
# 步骤 7：todo_parser 真实样例
# ---------------------------------------------------------------------------
REAL_SAMPLES = [
    "- [ ] 每周备份 (@2026-10-01) 🔺 🔁 every week on Sunday",
    "- [x] 交化学实验报告 (@2026-09-20)",
    "- [ ] 读南风窗 9 月刊 #阅读 #南风窗",
    "- [ ] 跑步 3 公里 🔽 🔁 every 3 days",
    "- [ ] 写周复盘 🔼 🔁 every week",
    "- [ ] 整理 ETF 网格策略 (@2026-10-15) 🔺 🔁 every week on Friday #invest",
    "- [ ] 给 mom 打电话 (@2026-09-16) 🔺 #family",
]


@pytest.mark.parametrize("line", REAL_SAMPLES)
def test_todo_parser_roundtrip(line: str) -> None:
    parsed = parse_todo_line(line)
    rebuilt = to_todo_line(parsed)
    reparsed = parse_todo_line(rebuilt)
    assert (
        parsed.done,
        parsed.text,
        parsed.due_at,
        parsed.priority,
        parsed.recur_rule,
        sorted(parsed.tags),
    ) == (
        reparsed.done,
        reparsed.text,
        reparsed.due_at,
        reparsed.priority,
        reparsed.recur_rule,
        sorted(reparsed.tags),
    )


def test_todo_parser_master_real_line() -> None:
    """主人的原句：done/text/due/priority/rrule 逐字段断言。"""
    t = parse_todo_line("- [ ] 每周备份 (@2026-10-01) 🔺 🔁 every week on Sunday")
    assert t.done is False
    assert t.text == "每周备份"
    assert t.due_at == date(2026, 10, 1)
    assert t.priority == "high"
    assert t.recur_rule == "FREQ=WEEKLY;BYDAY=SU"  # RRULE 化


def test_todo_parser_rejects_non_task() -> None:
    with pytest.raises(ValueError):
        parse_todo_line("这不是任务行")


# ---------------------------------------------------------------------------
# 步骤 5 + 9/10：迁移（内核 + 假插件）在临时库全流程
# ---------------------------------------------------------------------------
def _write_fake_plugin_migration(modules_dir: Path) -> None:
    mig = modules_dir / "probe_plugin" / "api" / "migrations"
    mig.mkdir(parents=True)
    (mig / "0001_init.py").write_text(
        "def upgrade(engine):\n"
        "    from sqlalchemy import text\n"
        "    with engine.begin() as c:\n"
        "        c.execute(text('CREATE TABLE probe_plugin_demo (id TEXT PRIMARY KEY)'))\n\n"
        "def downgrade(engine):\n"
        "    from sqlalchemy import text\n"
        "    with engine.begin() as c:\n"
        "        c.execute(text('DROP TABLE IF EXISTS probe_plugin_demo'))\n",
        encoding="utf-8",
    )


def test_migration_full_cycle(tmp_path, monkeypatch) -> None:
    import db.engine as dbe
    from db import migrate as M

    # ★ 关键隔离：test_health 导入 main 时已把全局引擎指向 dev 库（data/alongside.db），
    # init_engine 的幂等会让这里拿到全局引擎、把迁移跑在 dev 库上！
    # 先清掉全局状态（monkeypatch 结束后自动还原），本测试完全走 tmp 库。
    monkeypatch.setattr(dbe, "_engine", None)

    db_file = tmp_path / "m.db"
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    engine = M.init_engine(f"sqlite:///{db_file.as_posix()}")
    assert str(engine.url).endswith("m.db"), f"必须命中 tmp 库，而不是 {engine.url}"

    M._upgrade_head()  # 空库一次建成
    from sqlalchemy import text as stext

    with engine.connect() as c:
        names = {
            r[0]
            for r in c.execute(
                stext("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
    assert {"audit_log", "idempotency_key", "app_setting", "plugin_state",
            "plugin_setting"} <= names

    fake = tmp_path / "modules"
    _write_fake_plugin_migration(fake)
    executed = M.run_plugin_migrations(engine, fake)
    assert executed == ["probe_plugin/0001_init"]
    assert M.run_plugin_migrations(engine, fake) == []  # 幂等
    M._downgrade_base(fake)
    with engine.connect() as c:
        names = {
            r[0]
            for r in c.execute(
                stext("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
    assert names == {"alembic_version"}  # 干净回滚
    # monkeypatch 自动还原 dbe._engine；session_factory 按调用时的全局值取引擎，
    # 还原后 core.deps 的工厂重新指向 dev 库，无需手工处理。


# ---------------------------------------------------------------------------
# 步骤 11：10 并发写
# ---------------------------------------------------------------------------
def test_concurrent_writers_no_lock(tmp_path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path.as_posix()}/c.db")
    Base.metadata.create_all(engine)
    errors: list[str] = []
    lock = threading.Lock()

    def writer(i: int) -> None:
        try:
            with Session(engine) as s:
                s.add(AppSetting(key=f"c{i}", value_json=str(i)))
                s.commit()
        except Exception as ex:  # noqa: BLE001
            with lock:
                errors.append(f"{i}: {ex}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with Session(engine) as s:
        assert len(s.exec(select(AppSetting)).all()) == 10


# ---------------------------------------------------------------------------
# 步骤 12：备份 + 恢复演练
# ---------------------------------------------------------------------------
def test_backup_and_verify(tmp_path) -> None:
    import sqlite3

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import backup as bk  # noqa: PLC0415

    db = tmp_path / "main.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.execute("INSERT INTO t VALUES ('中文数据')")
    conn.commit()
    conn.close()

    dest = bk.backup(db, tmp_path / "backups")
    assert bk.verify(dest) is True
    assert bk.latest_backup(tmp_path / "backups") == dest


# ---------------------------------------------------------------------------
# 步骤 13：字典一致性
# ---------------------------------------------------------------------------
def test_dictionary_matches_models() -> None:
    from db.check_dictionary import compare

    ok, diffs = compare()
    assert ok, diffs


# ---------------------------------------------------------------------------
# UTC 往返（验收：跨天事件不乱时区）
# ---------------------------------------------------------------------------
def test_utc_roundtrip_cross_day(engine) -> None:
    Base.metadata.create_all(engine)
    src = datetime(2026, 9, 18, 20, 0, tzinfo=timezone(timedelta(hours=8)))  # 周五 20:00+08
    with Session(engine) as s:
        s.add(AuditLog(actor="t", action="crossday", at=src))
        s.commit()
    with Session(engine) as s:
        row = s.exec(select(AuditLog).where(AuditLog.action == "crossday")).one()
        assert row.at == src  # ★ 往返仍是**同一瞬间**（跨时区相等）—— 这是真正的不变量
        # ★ 2026-09-28（主人「统一为本地时区」）：读出不再补 UTC，而是转主人本地时区。
        #   库里**仍然存 UTC**（写入侧未动），只是进程内表示改为本地 —— 同一瞬间，换个表示。
        assert row.at.tzinfo == ZoneInfo(get_settings().tz)
    assert utcnow().tzinfo is not None
