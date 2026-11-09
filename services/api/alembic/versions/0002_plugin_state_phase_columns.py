"""0002 · plugin_state 补阶段机五列（phase / last_known_phase / phase_reason /
phase_changed_at / was_satisfied）

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-08

★ 为什么只加列、不改任何既有列
-----------------------------
新内核（`core/plugins/phase.py` + `core/plugins/state_store.py`）把「插件走到
哪一步」落成显式的五列；`enabled` 保留原语义（对外是否可用），存量读法
（前端 / MCP 工具面 / 既有断言）一个字不动。删列/改类型都是破坏性且不可逆的，
而这次的需求只是「多记一份真相」。

★ 存量行的默认值（保守口径，逐列说清）
-------------------------------------
  - `phase`            → `'PENDING'`（server_default）。它是 `Phase` 的合法值，
                         含义是「已发现、等待前置条件」——不说谎、也不会把
                         老行判成非法阶段而让状态机抛错。
                         刻意**不填 `'ACTIVE'`**：那等于宣布「它们已经在工作」，
                         启动对账会因此跳过重载。
  - `last_known_phase` → NULL = 未知。不伪造「转移起点」，否则对账会读出一个
                         从未发生过的转移。
  - `phase_reason`     → NULL = 从未记录。
  - `phase_changed_at` → NULL = 从未发生阶段变化。
  - `was_satisfied`    → NULL = 从未判定过。**不能填 0**：False 与 NULL 在
                         「依赖刚丧失」的判定里语义不同，填 0 会造出一次
                         假的「依赖丧失」。

★ 两条建库路径都要得到五列
--------------------------
  ① 新库：`db.models` 的模型已带这五列 ⇒ `Base.metadata.create_all()` 直接建全；
  ② 旧库：本迁移 `op.add_column` 补上（SQLite 的 ADD COLUMN 支持非空列 +
     server_default，不需要 batch 重建表）。
  ⇒ 两条路径的列名/类型/NULL 性一致。

★ 为什么加列前要 `inspect` 一下（不是洁癖，是必须）
--------------------------------------------------
  0001 的写法是 `Base.metadata.create_all()` —— **按当前模型**建表。
  于是「今天从零 upgrade head」时，0001 建出来的 `plugin_state` **已经带
  这五列**，紧随其后的本迁移再 `ADD COLUMN phase` 就会撞
  `duplicate column name: phase`（新建库装不起来）。
  同理，测试/探针常先 `create_all` 再 `upgrade head`。
  ⇒ 所以这里按实际列名差集补列：**缺哪个补哪个**。这也是本迁移能重入的原因。

★ 迁移只增不改：本文件合并后不许再动，改结构 = 加 0003。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "plugin_state"

#: 要加的列（顺序即建列顺序，与 db/models/system.py 的声明顺序一致）。
#: 类型长度取枚举最长值向上取整：阶段最长 "TERMINATED"(10) → 16；
#: 原因最长 "dependency-satisfied"(20) → 32。
_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("phase", sa.String(16), nullable=False, server_default="PENDING"),
    sa.Column("last_known_phase", sa.String(16), nullable=True),
    sa.Column("phase_reason", sa.String(32), nullable=True),
    # 与模型侧的 `TimestampTZ`（impl=DateTime）在 DDL 上等价：SQLite 落 DATETIME。
    # 时区换算在 ORM 层（写入转 UTC、读出转本地），DDL 不需要表达。
    sa.Column("phase_changed_at", sa.DateTime(), nullable=True),
    sa.Column("was_satisfied", sa.Boolean(), nullable=True),
)


def _existing_columns() -> set[str]:
    insp = sa.inspect(op.get_bind())
    if _TABLE not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    have = _existing_columns()
    for col in _COLUMNS:
        if col.name in have:
            continue  # 0001 用当前 metadata 建表时已经带上了这一列
        op.add_column(_TABLE, col)
    # ★ 补过一次列之后，存量行必须拿到合法阶段。
    #   `phase` 的 server_default 已覆盖「ADD COLUMN 时的老行」，
    #   但「0001 按新模型建表」那条路径没有 server_default，
    #   故对 NULL 做一次兜底 UPDATE（幂等：没有 NULL 就什么也不改）。
    op.execute(sa.text(f"UPDATE {_TABLE} SET phase = 'PENDING' WHERE phase IS NULL"))


def downgrade() -> None:
    # SQLite 的 DROP COLUMN 版本依赖强（3.35+），用 batch 重建表最稳。
    have = _existing_columns()
    targets = [c.name for c in reversed(_COLUMNS) if c.name in have]
    if not targets:
        return
    with op.batch_alter_table(_TABLE) as batch:
        for name in targets:
            batch.drop_column(name)
