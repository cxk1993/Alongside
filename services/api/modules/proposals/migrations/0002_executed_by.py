"""插件迁移 0002：提案审计字段——executed_by / undone_by（bidiu 2026-11-22）。

背景：approve/undo 的内部 HTTP 用 admin service 身份（USER_SUB），但「谁签的字」
必须作为**业务事实**落库——否则多人/多 AI 场景下审计分不清签字人。
（契约精神：留得下证据。）

SQLite 的 ALTER TABLE ADD COLUMN 是幂等的安全操作（checkfirst 语义由 inspector 判）。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text

NEW_COLUMNS = (
    ("executed_by", "VARCHAR(80)"),
    ("undone_by", "VARCHAR(80)"),
)


def upgrade(engine: Any) -> None:
    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns("proposals_change_proposal")}
    with engine.begin() as conn:
        for name, ddl_type in NEW_COLUMNS:
            if name not in existing:
                conn.execute(
                    text(f"ALTER TABLE proposals_change_proposal ADD COLUMN {name} {ddl_type}")
                )


def downgrade(engine: Any) -> None:
    """SQLite 不支持 DROP COLUMN（老版本）——按纪律不猜、不回滚，留痕说明。

    需要回滚时：重建表（0001 的 create + 数据迁移），属于破坏性操作，人工执行。
    """
    return None