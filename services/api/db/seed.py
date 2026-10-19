"""种子数据（T04）：python -m db.seed

★ 模型归属修正后的实现方式（卡片「★ 模型归属」一节的落地解释）：
  - 内核表（app_setting 默认项）：直接灌。
  - 业务表（growth_axis / growth_item / habit / habit_log / calendar_event）：
    表由各插件迁移创建（总纲 §1.5 字段）。本脚本在**表已存在**时灌入，
    不存在时明确打印跳过原因——等对应插件卡交付后重跑本命令即可补齐。
    （这样 seed 的内容与 §1.5 的数据字典始终对得上，又不会越界替插件建表。）

幂等：所有种子行使用固定 id（uuid5 命名空间），重复执行不会灌出重复数据。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlmodel import Session

from db.base import utcnow

_NS = uuid.UUID("6f1d2c0a-1111-4c92-9a3e-4a6f1e2b7d04")  # T04 种子数据专用命名空间


def _sid(name: str) -> str:
    """种子行的确定性 id：同一名字永远同一个 id → 天然幂等。"""
    return uuid.uuid5(_NS, name).hex


def _now() -> str:
    return utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")


# ════════════════════════════════════════════════════════════════
# 种子内容（数据本身来自任务卡 T04 详细要求 #5）
# ════════════════════════════════════════════════════════════════
GROWTH_AXES: list[tuple[str, str, float]] = [
    # (名称, 展示名, 权重)
    ("理财", "理财", 1.0),
    ("海外赚钱", "海外赚钱", 1.0),
    ("投资自身", "投资自身", 1.0),
]

GROWTH_ITEMS: dict[str, list[str]] = {
    "理财": [
        "etf学习",
        "基础股市学习",
        "个人资产与流水统计",
        "AI+：ETF分析",
        "AI+：股市分析",
        "AI+：信息源整合",
    ],
    "海外赚钱": [
        "赚钱路径收集",
        "拉人入伙",
        "闷声发大财",
        "多信源",
    ],
    "投资自身": [
        "读南风窗",
        "健康",
        "读小说",
        "玩gal",
        "规律作息",
        "护肤",
        "健康饮食",
        "眼保健操",
    ],
}

HABITS: list[dict[str, str]] = [
    {"name": "规律作息", "target": "每日 23:30 前睡觉", "rule": "daily"},
    {"name": "护肤", "target": "每日晚間护肤", "rule": "daily"},
    {"name": "健康饮食", "target": "三餐规律", "rule": "daily"},
    {"name": "眼保健操", "target": "每日 2 组", "rule": "daily"},
]

# 示例日程：跨天（周五 20:00 → 周六 02:00）+ 三层嵌套
_FRIDAY_20 = datetime(2026, 9, 18, 20, 0, tzinfo=timezone(timedelta(hours=8)))


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


SAMPLE_EVENTS: list[dict[str, object]] = [
    {
        "id": _sid("event:复习周周五晚"),
        "title": "周五晚会·复习周（示例）",
        "color": "var(--accent)",
        "start_at": _utc(_FRIDAY_20),
        "end_at": _utc(_FRIDAY_20 + timedelta(hours=6)),  # 跨到周六 02:00
        "all_day": 0,
        "span_days": 2,
        "parent_id": None,
        "sort": 0,
        "source": "manual",
    },
    {
        "id": _sid("event:无机化学"),
        "title": "无机化学（示例·子块）",
        "color": "var(--accent)",
        "start_at": _utc(_FRIDAY_20),
        "end_at": _utc(_FRIDAY_20 + timedelta(hours=2)),
        "all_day": 0,
        "span_days": 1,
        "parent_id": _sid("event:复习周周五晚"),
        "sort": 1,
        "source": "manual",
    },
]


def _table_exists(bind: Engine | Connection, name: str) -> bool:
    """表是否存在。bind 收 Engine 或 Connection —— `inspect()` 两者都接受，
    而调用处传的是 `session.connection()`（Connection），签名不该窄于实际用法。"""
    return inspect(bind).has_table(name)


def _rows_exist(session: Session, table: str) -> bool:
    r = session.execute(text(f"SELECT COUNT(*) FROM {table}")).first()  # noqa: S608
    return bool(r and r[0])


def _seed_app_setting(session: Session) -> str:
    defaults = {"app.name": '"Alongside"', "app.tz": '"Asia/Shanghai"'}
    n = 0
    for key, value_json in defaults.items():
        row = session.execute(
            text("SELECT id FROM app_setting WHERE key = :k"), {"k": key}
        ).first()
        if row:
            continue
        session.execute(
            text(
                "INSERT INTO app_setting (id, key, value_json, created_at, updated_at) "
                "VALUES (:id, :k, :v, :now, :now)"
            ),
            {"id": _sid(f"setting:{key}"), "k": key, "v": value_json, "now": _now()},
        )
        n += 1
    session.commit()
    return f"app_setting：新增 {n} 条默认项"


def _seed_growth(session: Session) -> list[str]:
    logs: list[str] = []
    if not _table_exists(session.connection(), "growth_axis"):
        return ["growth_axis/growth_item：表未创建（等 growth 插件迁移后重跑 seed）"]
    if _rows_exist(session, "growth_axis"):
        return ["growth_axis：已有数据，跳过"]
    for name, display, weight in GROWTH_AXES:
        session.execute(
            text(
                "INSERT INTO growth_axis (id, name, weight, created_at, updated_at) "
                "VALUES (:id, :name, :weight, :now, :now)"
            ),
            {"id": _sid(f"axis:{name}"), "name": display, "weight": weight, "now": _now()},
        )
    if _table_exists(session.connection(), "growth_item"):
        for axis_name, items in GROWTH_ITEMS.items():
            axis_id = _sid(f"axis:{axis_name}")
            for title in items:
                session.execute(
                    text(
                        "INSERT INTO growth_item (id, axis_id, title, status, progress, note, "
                        "created_at, updated_at) VALUES "
                        "(:id, :axis_id, :title, 'todo', 0, '', :now, :now)"
                    ),
                    {"id": _sid(f"item:{axis_name}:{title}"), "axis_id": axis_id,
                     "title": title, "now": _now()},
                )
        logs.append(f"growth：{len(GROWTH_AXES)} 条主线 + "
                    f"{sum(len(v) for v in GROWTH_ITEMS.values())} 条条目")
    session.commit()
    return logs


def _seed_habits(session: Session) -> list[str]:
    if not _table_exists(session.connection(), "habit"):
        return ["habit：表未创建（等 habit 插件迁移后重跑 seed）"]
    if _rows_exist(session, "habit"):
        return ["habit：已有数据，跳过"]
    for h in HABITS:
        session.execute(
            text(
                "INSERT INTO habit (id, name, target, rule, created_at, updated_at) "
                "VALUES (:id, :name, :target, :rule, :now, :now)"
            ),
            {"id": _sid(f"habit:{h['name']}"), **h, "now": _now()},
        )
    session.commit()
    return [f"habit：{len(HABITS)} 条习惯项（{('、'.join(h['name'] for h in HABITS))}）"]


def _seed_sample_events(session: Session) -> list[str]:
    if not _table_exists(session.connection(), "calendar_event"):
        return ["calendar_event：表未创建（等 calendar 插件迁移后重跑 seed）"]
    n = 0
    for ev in SAMPLE_EVENTS:
        row = session.execute(
            text("SELECT id FROM calendar_event WHERE id = :id"), {"id": ev["id"]}
        ).first()
        if row:
            continue
        cols = ", ".join(ev.keys())
        params = dict(ev)
        params["created_at"] = params["updated_at"] = _now()
        session.execute(
            text(f"INSERT INTO calendar_event ({cols}, created_at, updated_at) "
                 f"VALUES (:{', :'.join(ev.keys())}, :created_at, :updated_at)"),  # noqa: S608
            params,
        )
        n += 1
    session.commit()
    return [f"calendar_event：示例日程 {n} 条（跨天 + 嵌套）"]


def run(engine: Engine) -> list[str]:
    """灌种子数据。返回逐表结果说明（供验收贴输出）。"""
    logs: list[str] = []
    with Session(engine) as session:
        if _table_exists(engine, "app_setting"):
            logs.append(_seed_app_setting(session))
        else:
            logs.append("app_setting：表未创建——请先执行 python -m db.migrate upgrade head")
        logs.extend(_seed_growth(session))
        logs.extend(_seed_habits(session))
        logs.extend(_seed_sample_events(session))
    return logs


def main() -> int:
    from db.engine import init_engine

    engine = init_engine()
    print("[seed] 灌入种子数据：")
    for line in run(engine):
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
