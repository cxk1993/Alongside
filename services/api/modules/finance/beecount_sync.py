"""BeeCount 只读快照同步（T08B）。

流程：
  1. 经 MCP 读工具拉 get_ledger_stats + get_analytics_summary
  2. 提取可识别金额（整数分）→ finance_snapshot 结构化列
  3. 原始摘要写入 meta_json（可截断）
  4. 按 **日历日** upsert：同日再 sync 覆盖同一行，**不翻倍**

幂等归属：Alongside 侧 `finance_snapshot.date` 唯一约束是权威去重，
不依赖 BeeCount 的 sync 协议（我们刻意不走 /api/v1/sync/*）。

凭据与通道规则见 beecount_mcp.py 模块 docstring。
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlmodel import Session, col, select

from core.events import event_bus

from .beecount_mcp import (
    MCP_PATH,
    READ_TOOLS,
    TOOL_ANALYTICS,
    TOOL_LEDGER_STATS,
    SupportsReadToolCall,
    call_with_client,
    load_upstream_config,
)
from .models import FinanceSnapshot
from .schema import BeeCountSourceOut, FinanceSnapshotOut, SnapshotSyncOut

# meta_json 最大长度（字符）；超出截断，避免单行撑爆 SQLite 页
META_MAX_CHARS = 8000

# 主人本地时区（快照「按日」用 Asia/Shanghai 日历日，与 habits 一致）
_TZ_SHANGHAI = timezone(timedelta(hours=8))

# 总资产候选键（按优先级；BeeCount 具体字段以实测为准，缺省走 meta）
_ASSET_KEYS = (
    "balance_cents",
    "total_asset_cents",
    "total_asset",
    "total_balance_cents",
    "total_balance",
    "balance",
    "net_worth_cents",
    "net_worth",
)
_CASH_KEYS = ("cash_cents", "cash", "cash_balance_cents", "cash_balance")
_INVEST_KEYS = ("invest_cents", "invest", "investment_cents", "investment", "securities_cents")
_DEBT_KEYS = ("debt_cents", "debt", "liability_cents", "liability")


def local_today(now: datetime | None = None) -> date:
    """返回 Asia/Shanghai 日历日（datetime.date）。"""
    dt = now if now is not None else datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_TZ_SHANGHAI).date()


def _as_cents(value: Any) -> int:
    """任意标量 → 整数分。无法识别则 0（不抛错，原始值仍在 meta_json）。"""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 0
        try:
            d = Decimal(s)
        except (InvalidOperation, ValueError):
            return 0
        if "." in s:
            return int((d * 100).to_integral_value())
        return int(d)
    return 0


def _pick_cents(sources: list[Any], keys: tuple[str, ...]) -> int:
    for src in sources:
        if not isinstance(src, dict):
            continue
        for key in keys:
            if key in src:
                cents = _as_cents(src[key])
                if cents != 0 or src[key] in (0, "0", "0.0", "0.00"):
                    return cents
    return 0


def _truncate_meta(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= META_MAX_CHARS:
        return text
    return text[: META_MAX_CHARS - 20] + '…","__truncated__":true}'


def dump_snapshot(row: FinanceSnapshot, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """模型 → API 字典（与 FinanceSnapshotOut 字段一致）。"""
    parsed: dict[str, Any] | None
    if meta is not None:
        parsed = meta
    elif row.meta_json:
        try:
            loaded = json.loads(row.meta_json)
            parsed = loaded if isinstance(loaded, dict) else {"raw": loaded}
        except json.JSONDecodeError:
            parsed = {"raw": row.meta_json[:500]}
    else:
        parsed = None
    return {
        "id": row.id,
        "date": row.date,
        "total_asset": row.total_asset,
        "cash": row.cash,
        "invest": row.invest,
        "debt": row.debt,
        "meta": parsed,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def upsert_snapshot(
    db: Session,
    *,
    stats: Any,
    analytics: Any,
    upstream: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """把一次拉取结果幂等写入 finance_snapshot（date 唯一）。"""
    sources = [stats, analytics]
    total_asset = _pick_cents(sources, _ASSET_KEYS)
    cash = _pick_cents(sources, _CASH_KEYS)
    invest = _pick_cents(sources, _INVEST_KEYS)
    debt = _pick_cents(sources, _DEBT_KEYS)

    snapshot_date = local_today(now)
    synced_at = (now if now is not None else datetime.now(UTC)).astimezone(UTC)
    meta_payload = {
        "upstream": upstream,
        "synced_at": synced_at.isoformat(),
        "tools": [TOOL_LEDGER_STATS, TOOL_ANALYTICS],
        "ledger_stats": stats,
        "analytics": analytics,
    }
    meta_json = _truncate_meta(meta_payload)

    existing = db.exec(
        select(FinanceSnapshot).where(col(FinanceSnapshot.date) == snapshot_date)
    ).first()
    if existing is not None:
        existing.total_asset = total_asset
        existing.cash = cash
        existing.invest = invest
        existing.debt = debt
        existing.meta_json = meta_json
        db.add(existing)
        row = existing
        created = False
    else:
        row = FinanceSnapshot(
            date=snapshot_date,
            total_asset=total_asset,
            cash=cash,
            invest=invest,
            debt=debt,
            meta_json=meta_json,
        )
        db.add(row)
        created = True
    db.commit()
    db.refresh(row)

    payload = dump_snapshot(row, meta=meta_payload)
    event_bus.publish(
        "finance.snapshot.updated",
        {
            "id": row.id,
            "date": row.date.isoformat(),
            "total_asset": row.total_asset,
            "created": created,
            "upstream": upstream,
        },
        source="finance",
    )
    return payload


def sync_from_client(
    db: Session,
    client: SupportsReadToolCall,
    *,
    now: datetime | None = None,
    upstream: str = "injected",
) -> dict[str, Any]:
    """在给定 client 上执行只读同步（测试可注入 Mock / 假 client）。"""
    stats = client.call_tool(TOOL_LEDGER_STATS, {})
    analytics = client.call_tool(TOOL_ANALYTICS, {})
    return upsert_snapshot(
        db,
        stats=stats,
        analytics=analytics,
        upstream=upstream,
        now=now,
    )


def sync_snapshot(
    db: Session,
    *,
    client: SupportsReadToolCall | None = None,
    now: datetime | None = None,
) -> SnapshotSyncOut:
    """手动/定时触发一次只读同步。client 缺省时按 FINANCE_UPSTREAM 创建。

    mock → 离线假数据；mcp → 真打 BeeCount MCP（缺凭据会明确 503）。
    同日重复调用覆盖同一 finance_snapshot 行（date 唯一），不翻倍。
    """
    upstream_env = load_upstream_config()["upstream"]

    def _run(c: SupportsReadToolCall) -> dict[str, Any]:
        return sync_from_client(db, c, now=now, upstream=upstream_env)

    if client is not None:
        snap = _run(client)
    else:
        raw = call_with_client(_run, client=None)
        snap = raw if isinstance(raw, dict) else {}
    snapshot_out = FinanceSnapshotOut.model_validate(snap)
    return SnapshotSyncOut(ok=True, upstream=upstream_env, snapshot=snapshot_out)


def list_snapshots(
    db: Session,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> dict[str, Any]:
    """快照列表（date 倒序）。date_from/date_to 为 YYYY-MM-DD。"""
    stmt = select(FinanceSnapshot)
    if date_from:
        stmt = stmt.where(col(FinanceSnapshot.date) >= date.fromisoformat(date_from))
    if date_to:
        stmt = stmt.where(col(FinanceSnapshot.date) <= date.fromisoformat(date_to))
    rows = list(db.exec(stmt).all())
    rows.sort(key=lambda r: r.date, reverse=True)
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "items": [dump_snapshot(r) for r in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def beecount_source(db: Session) -> BeeCountSourceOut:
    """上游状态汇总（**不返回 token**）。"""
    cfg = load_upstream_config()
    info = last_sync_info(db)
    return BeeCountSourceOut(
        upstream=cfg["upstream"],
        configured=bool(cfg["configured"]),
        base_url_configured=bool(cfg["base_url"]),
        token_present=bool(cfg["token_present"]),
        last_sync=info["last_sync"],
        last_snapshot_date=info["last_snapshot_date"],
        snapshot_count=int(info["snapshot_count"]),
        write_enabled=False,
        read_tools=sorted(READ_TOOLS),
        mcp_path=MCP_PATH,
    )


def last_sync_info(db: Session) -> dict[str, Any]:
    """最近一次同步信息（供 /beecount/source）。"""
    rows = list(db.exec(select(FinanceSnapshot)).all())
    if not rows:
        return {"last_sync": None, "last_snapshot_date": None, "snapshot_count": 0}
    rows.sort(key=lambda r: (r.updated_at, r.date), reverse=True)
    latest = rows[0]
    return {
        "last_sync": latest.updated_at,
        "last_snapshot_date": latest.date,
        "snapshot_count": len(rows),
    }


def validate_snapshot_out(data: dict[str, Any]) -> FinanceSnapshotOut:
    """类型收窄：dict → FinanceSnapshotOut（mypy / schema 一致性）。"""
    return FinanceSnapshotOut.model_validate(data)
