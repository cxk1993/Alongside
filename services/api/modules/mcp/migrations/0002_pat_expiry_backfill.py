"""插件迁移 0002：存量 PAT 过期补救（T11 · bidiu 2026-11-22）。

背景：S2（d70d68f）只补了「新建/改权时写 expires_at」的写入路径；而**在此之前**
签发的 PAT 全部 ``expires_at = NULL``（永不过期）——开放接待处（外部 agent 接入）
之前必须收口。

策略：给存量 NULL 一律补 **+30 天宽限期**（保护性动作；不做破坏性失效）。
**幂等**：补过之后没有 NULL 行，再跑是空操作。

实现走 ORM（与内核同一套 datetime 序列化），不用裸 SQL——SQLite 的 TimestampTZ
存/读格式必须一致，否则鉴权侧 `expires_at < now` 解析会踩雷。

★ 只增不改：已执行过的迁移文件不许再动，要改就加 0003。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_MODEL_MODULE = "mcp_models"

#: 宽限期（天）——给已发出去的钥匙留出自然淘汰窗口
GRACE_DAYS = 30


def _load_models() -> Any:
    # 全量 pytest / lifespan：create_app 可能已 import 包路径 models，
    # 再按文件路径 exec 会重复定义 Table。优先复用已加载模块。
    for key in ("modules.mcp.models", _MODEL_MODULE):
        existing = sys.modules.get(key)
        if existing is not None:
            return existing
    path = Path(__file__).resolve().parent.parent / "models.py"
    spec = importlib.util.spec_from_file_location(_MODEL_MODULE, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODEL_MODULE] = mod
    spec.loader.exec_module(mod)
    return mod


def upgrade(engine: Any) -> None:
    from core.db_scope import SCOPE_KEY
    from sqlmodel import Session, select

    models = _load_models()
    deadline = datetime.now(timezone.utc) + timedelta(days=GRACE_DAYS)
    with Session(engine) as session:
        # ★ 内核特权：迁移是内核级操作（同 core.deps.kernel_session 的口径）。
        #   不声明作用域 = deny-all（G13/G16 数据边界），裸 Session 会被 authorizer 拒。
        session.info[SCOPE_KEY] = None
        rows = session.exec(
            select(models.McpPat).where(models.McpPat.expires_at.is_(None))
        ).all()
        for row in rows:
            row.expires_at = deadline
            session.add(row)
        session.commit()


def downgrade(engine: Any) -> None:
    """不可回滚：无法区分「本迁移补的过期」与「本来就有过期」——按纪律不猜。

    需要回滚时，人工按 audit_log / created_at 窗口处置。
    """
    return None