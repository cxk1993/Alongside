"""从 Work-Review 拉取某日并落库（Alongside 自己的库，幂等 upsert）。

★ 只读上游：先 GET /v1/reports/{date}，再 POST export-markdown，可选 hourly。
★ raw_path = `work-review:<date>`（来源标识，不是文件路径）。
★ 同 date 重复 ingest → 覆盖，不产生第二条。
★ 空响应 → 仍然落一行 is_empty=true，前端显示「当日无记录」，不崩。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from datetime import date as DateType
from typing import Any

from sqlmodel import Session, select

from .client import ReviewClient
from .models import ReviewDaily
from .parser import format_duration, normalize_report_payload, parse_duration


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return val if isinstance(val, list) else []


def upsert_daily(
    db: Session,
    day: DateType,
    payload: dict[str, Any],
    *,
    raw_md: str,
    raw_path: str,
) -> ReviewDaily:
    """按 date 幂等 upsert。"""
    existing = db.exec(select(ReviewDaily).where(ReviewDaily.date == day)).first()
    category = payload.get("categories") or []
    apps = payload.get("apps") or []
    domains = payload.get("domains") or []
    hourly = payload.get("hourly") or []
    ai = str(payload.get("ai_analysis_md") or "")
    is_empty = bool(payload.get("empty")) and not (category or apps or domains or ai)
    now = datetime.now(UTC)

    if existing is None:
        row = ReviewDaily(
            date=day,
            category_json=_dumps(category),
            app_json=_dumps(apps),
            domain_json=_dumps(domains),
            hourly_json=_dumps(hourly),
            ai_analysis_md=ai or None,
            raw_md=raw_md or None,
            raw_path=raw_path,
            is_empty=is_empty,
            synced_at=now,
        )
        db.add(row)
    else:
        existing.category_json = _dumps(category)
        existing.app_json = _dumps(apps)
        existing.domain_json = _dumps(domains)
        existing.hourly_json = _dumps(hourly)
        existing.ai_analysis_md = ai or None
        existing.raw_md = raw_md or None
        existing.raw_path = raw_path
        existing.is_empty = is_empty
        existing.synced_at = now
        db.add(existing)
        row = existing
    db.commit()
    db.refresh(row)
    return row


def _hourly_item(item: dict[str, Any]) -> dict[str, Any] | None:
    hour = item.get("hour", item.get("h"))
    try:
        h = int(hour)
    except (TypeError, ValueError):
        return None
    dur = item.get("duration_text") or item.get("duration")
    sec = item.get("seconds")
    sec_i = parse_duration(dur) if sec is None else int(sec)
    return {
        "hour": h,
        "seconds": sec_i,
        "duration_text": str(dur or format_duration(sec_i)),
    }


def fetch_and_store(
    db: Session,
    day: DateType,
    client: ReviewClient | None = None,
) -> dict[str, Any]:
    """拉取某日 → 归一 → 落库。返回落库摘要（供 ingest 接口与事件）。"""
    client = client or ReviewClient()
    report_raw = client.get_report(day)
    report = report_raw if isinstance(report_raw, dict) else {}
    markdown = client.export_markdown(day)

    hourly_ext = client.get_hourly(day)
    payload = normalize_report_payload(report, markdown)
    if hourly_ext and isinstance(hourly_ext, list):
        ext_rows: list[dict[str, Any]] = []
        for item in hourly_ext:
            if not isinstance(item, dict):
                continue
            parsed = _hourly_item(item)
            if parsed is not None:
                ext_rows.append(parsed)
        if ext_rows:
            payload["hourly"] = ext_rows
            if any(r["seconds"] for r in ext_rows):
                payload["empty"] = False
            else:
                payload["empty"] = bool(payload.get("empty", False))

    raw_path = f"work-review:{day.isoformat()}"
    daily = upsert_daily(db, day, payload, raw_md=markdown, raw_path=raw_path)

    category = _loads(daily.category_json)
    apps = _loads(daily.app_json)
    return {
        "date": daily.date.isoformat(),
        "id": daily.id,
        "raw_path": daily.raw_path,
        "is_empty": daily.is_empty,
        "category_count": len(category),
        "app_count": len(apps),
        "has_ai": bool(daily.ai_analysis_md),
        "has_raw": bool(daily.raw_md),
        "synced_at": daily.synced_at.isoformat() if daily.synced_at else None,
        "mode": client.mode,
        "path": client.path_kind,
    }
