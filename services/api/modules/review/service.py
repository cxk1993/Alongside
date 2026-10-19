"""复盘业务逻辑：落库查询、趋势/对比、周报代理、批注、source 状态。

★ 对 Work-Review 只读；本层只写 Alongside 自己的 review_daily / review_note。
★ 事件 review.day.ingested 在 ingest 成功后 publish。
★ 趋势是复盘的灵魂：后端算好自然语言结论，前端少猜。
★ 桥离线：已落库日期仍可看；上游请求快速失败，不无限重试。
"""
from __future__ import annotations

import json
import time
from datetime import date as DateType
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from core.errors import AppError, NotFoundError, ServiceUnavailableError, ValidationError
from core.events import event_bus

from .client import BridgeOfflineError, ReviewClient, ReviewUpstreamError
from .ingest import fetch_and_store
from .models import ReviewDaily, ReviewNote
from .parser import format_duration
from .schema import (
    CompareOut,
    DayDetail,
    DayListItem,
    DaysOut,
    HealthOut,
    HourlyRow,
    IngestOut,
    MetricDelta,
    NamedRow,
    NotesListOut,
    RawOut,
    ReviewNoteOut,
    SourceOut,
    TrendOut,
    TrendPoint,
    WeeklyOut,
)

SH_TZ = ZoneInfo("Asia/Shanghai")

# 周报短时缓存（不落库）
_WEEKLY_TTL_S = 300.0
_weekly_cache: dict[str, tuple[float, dict[str, Any]]] = {}

# source 最近一次探测缓存，避免每次 /source 都打上游
_SOURCE_PROBE_TTL_S = 20.0
_source_probe: dict[str, Any] = {"at": 0.0, "online": None, "version": None, "message": ""}


def local_today() -> DateType:
    return datetime.now(SH_TZ).date()


def _loads(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [x for x in val if isinstance(x, dict)] if isinstance(val, list) else []


def _named(rows: list[dict[str, Any]]) -> list[NamedRow]:
    out: list[NamedRow] = []
    for r in rows:
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        sec = int(r.get("seconds") or 0)
        dur = str(r.get("duration_text") or format_duration(sec))
        out.append(NamedRow(name=name, seconds=sec, duration_text=dur))
    return out


def _hourly(rows: list[dict[str, Any]]) -> list[HourlyRow]:
    out: list[HourlyRow] = []
    for r in rows:
        try:
            hour = int(r.get("hour") or -1)
        except (TypeError, ValueError):
            continue
        sec = int(r.get("seconds") or 0)
        dur = str(r.get("duration_text") or format_duration(sec))
        out.append(HourlyRow(hour=hour, seconds=sec, duration_text=dur))
    out.sort(key=lambda x: x.hour)
    return out


def _sum_seconds(rows: list[NamedRow]) -> int:
    return sum(r.seconds for r in rows)


def _map_client_error(exc: Exception) -> AppError:
    if isinstance(exc, BridgeOfflineError):
        return ServiceUnavailableError(exc.detail, title="桥离线")
    if isinstance(exc, ReviewUpstreamError):
        return ServiceUnavailableError(exc.detail, title="Work-Review 不可达")
    if isinstance(exc, AppError):
        return exc
    return ServiceUnavailableError(f"复盘上游异常：{exc}")


def _note_out(n: ReviewNote) -> ReviewNoteOut:
    return ReviewNoteOut(
        id=n.id,
        date=n.date,
        content_md=n.content_md,
        created_at=n.created_at,
    )


class ReviewService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ───────────────────────── 上游状态 ─────────────────────────
    def _probe_upstream(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and now - float(_source_probe.get("at") or 0) < _SOURCE_PROBE_TTL_S:
            return dict(_source_probe)
        client = ReviewClient()
        result: dict[str, Any] = {
            "at": now,
            "online": False,
            "version": None,
            "message": "",
            "mode": client.mode,
            "path": client.path_kind,
            "bridge": client.bridge,
        }
        try:
            h = client.health()
            version = h.get("version")
            result["online"] = True
            result["version"] = str(version) if version is not None else None
            result["message"] = "ok"
        except Exception as exc:  # 明确失败信息，不甩栈
            result["online"] = False
            result["message"] = str(getattr(exc, "detail", None) or exc)
            if isinstance(exc, BridgeOfflineError):
                result["message"] = "桥离线"
        _source_probe.clear()
        _source_probe.update(result)
        return dict(result)

    def health(self) -> HealthOut:
        client = ReviewClient()
        try:
            data = client.health()
            return HealthOut(
                ok=True,
                upstream_mode=client.mode,
                path=client.path_kind,
                bridge=client.bridge,
                version=str(data.get("version") or "") or None,
                status=str(data.get("status") or "") or None,
                recording=data.get("recording"),
                paused=data.get("paused"),
                message="ok",
            )
        except Exception as exc:
            msg = str(getattr(exc, "detail", None) or exc)
            return HealthOut(
                ok=False,
                upstream_mode=client.mode,
                path=client.path_kind,
                bridge=client.bridge,
                message=msg or "Work-Review 不可达 / token 无效",
            )

    def source(self) -> SourceOut:
        probe = self._probe_upstream()
        client = ReviewClient()
        last_any = self.db.exec(
            select(ReviewDaily).order_by(col(ReviewDaily.date).desc())
        ).first()
        last_sync = last_any.synced_at if last_any is not None else None

        bridge = bool(probe.get("bridge", client.bridge))
        online = bool(probe.get("online"))
        path = str(probe.get("path") or client.path_kind)
        if bridge:
            path = "bridge"
        message = str(probe.get("message") or "")
        if bridge and not online:
            message = "桥离线"
        elif not online and not message:
            message = "Work-Review 不可达 / token 无效"
        elif online:
            message = "ok"

        return SourceOut(
            mode=str(probe.get("mode") or client.mode),
            path=path,
            bridge=bridge,
            bridge_online=(None if not bridge else online),
            upstream_online=online,
            upstream_version=probe.get("version"),
            last_sync_at=last_sync,
            message=message,
        )

    # ───────────────────────── 读库 ─────────────────────────
    def list_days(
        self,
        frm: DateType | None = None,
        to: DateType | None = None,
        page: int = 1,
        size: int = 20,
    ) -> DaysOut:
        if page < 1:
            page = 1
        if size < 1:
            size = 1
        if size > 500:
            size = 500
        stmt = select(ReviewDaily)
        if frm is not None:
            stmt = stmt.where(col(ReviewDaily.date) >= frm)
        if to is not None:
            stmt = stmt.where(col(ReviewDaily.date) <= to)
        rows = list(self.db.exec(stmt).all())
        rows.sort(key=lambda r: r.date, reverse=True)
        total = len(rows)
        start = (page - 1) * size
        end = start + size
        page_rows = rows[start:end]
        items: list[DayListItem] = []
        for r in page_rows:
            categories = _named(_loads(r.category_json))
            apps = _loads(r.app_json)
            items.append(
                DayListItem(
                    date=r.date,
                    is_empty=bool(r.is_empty),
                    total_seconds=_sum_seconds(categories),
                    category_count=len(categories),
                    app_count=len(apps),
                    has_ai=bool(r.ai_analysis_md),
                    has_raw=bool(r.raw_md),
                    raw_path=r.raw_path or f"work-review:{r.date.isoformat()}",
                    top_categories=sorted(categories, key=lambda x: x.seconds, reverse=True)[:3],
                    synced_at=r.synced_at,
                )
            )
        return DaysOut(
            items=items,
            total=total,
            page=page,
            size=size,
            has_more=end < total,
        )

    def _get_row(self, day: DateType) -> ReviewDaily | None:
        return self.db.exec(select(ReviewDaily).where(ReviewDaily.date == day)).first()

    def _notes_for(self, day: DateType) -> list[ReviewNoteOut]:
        stmt = (
            select(ReviewNote)
            .where(ReviewNote.date == day)
            .order_by(col(ReviewNote.created_at))
        )
        return [_note_out(n) for n in self.db.exec(stmt).all()]

    def get_day(self, day: DateType) -> DayDetail:
        row = self._get_row(day)
        notes = self._notes_for(day)
        src = self.source()
        if row is None:
            return DayDetail(
                date=day,
                is_empty=True,
                empty_hint="当日无记录（尚未 ingest 或上游无数据）",
                notes=notes,
                source=src,
            )
        categories = _named(_loads(row.category_json))
        apps = _named(_loads(row.app_json))
        domains = _named(_loads(row.domain_json))
        hourly = _hourly(_loads(row.hourly_json))
        total = _sum_seconds(categories)
        empty = bool(row.is_empty) and total == 0 and not apps and not row.ai_analysis_md
        return DayDetail(
            date=row.date,
            is_empty=empty,
            empty_hint="当日无记录" if empty else "",
            categories=categories,
            apps=apps,
            domains=domains,
            hourly=hourly,
            ai_analysis_md=row.ai_analysis_md or "",
            total_seconds=total,
            raw_path=row.raw_path or f"work-review:{row.date.isoformat()}",
            has_raw=bool(row.raw_md),
            synced_at=row.synced_at,
            notes=notes,
            source=src,
        )

    def get_raw(self, day: DateType) -> RawOut:
        row = self._get_row(day)
        if row is None or not row.raw_md:
            return RawOut(
                date=day,
                markdown="",
                raw_path=f"work-review:{day.isoformat()}",
                found=False,
            )
        return RawOut(
            date=day,
            markdown=row.raw_md,
            raw_path=row.raw_path or f"work-review:{day.isoformat()}",
            found=True,
        )

    def _window_rows(self, days: int, end: DateType | None = None) -> list[ReviewDaily]:
        end_d = end or local_today()
        start_d = end_d - timedelta(days=max(1, days) - 1)
        rows = list(
            self.db.exec(
                select(ReviewDaily).where(
                    col(ReviewDaily.date) >= start_d, col(ReviewDaily.date) <= end_d
                )
            ).all()
        )
        rows.sort(key=lambda r: r.date)
        return rows

    def trend(self, metric: str = "total", days: int = 7) -> TrendOut:
        metric = (metric or "total").lower()
        if metric not in ("total", "category", "app"):
            raise ValidationError(f"metric 非法：{metric}（可用 total | category | app）")
        if days < 1 or days > 90:
            raise ValidationError("days 需在 1–90，常用 7 或 30")
        rows = self._window_rows(days)
        points: list[TrendPoint] = []
        labels: list[str] = []
        totals: list[int] = []
        for r in rows:
            categories = _named(_loads(r.category_json))
            apps = _named(_loads(r.app_json))
            total = _sum_seconds(categories)
            totals.append(total)
            labels.append(r.date.isoformat())
            if metric == "total":
                values: dict[str, int] = {}
            elif metric == "category":
                values = {c.name: c.seconds for c in categories}
            else:
                values = {a.name: a.seconds for a in apps}
            points.append(TrendPoint(date=r.date, total_seconds=total, values=values))
        if totals:
            avg = sum(totals) / len(totals)
            last = totals[-1]
            conclusion = (
                f"近 {len(totals)} 天有记录，日均 {format_duration(int(avg))}，"
                f"最近一日 {format_duration(last)}"
            )
        else:
            conclusion = "区间内暂无已落库的复盘数据，可先 ingest。"
        return TrendOut(
            metric=metric, days=days, points=points, labels=labels, conclusion=conclusion
        )

    def compare(self, day: DateType, against: DateType) -> CompareOut:
        row_a = self._get_row(day)
        row_b = self._get_row(against)

        def _cats(row: ReviewDaily | None) -> dict[str, int]:
            if row is None:
                return {}
            return {c.name: c.seconds for c in _named(_loads(row.category_json))}

        cats_a, cats_b = _cats(row_a), _cats(row_b)
        keys = sorted(
            set(cats_a) | set(cats_b),
            key=lambda k: -(cats_a.get(k, 0) + cats_b.get(k, 0)),
        )
        metrics: list[MetricDelta] = []
        for key in keys:
            a = cats_a.get(key, 0)
            b = cats_b.get(key, 0)
            delta = a - b
            dt = (
                f"多 {format_duration(delta)}"
                if delta >= 0
                else f"少 {format_duration(-delta)}"
            )
            metrics.append(
                MetricDelta(
                    key=key,
                    date_seconds=a,
                    against_seconds=b,
                    delta_seconds=delta,
                    delta_text=dt,
                )
            )
        total_a = sum(cats_a.values())
        total_b = sum(cats_b.values())
        total_delta = total_a - total_b
        if not cats_a and not cats_b:
            conclusion = "两日均无已落库数据，无法对比。"
        elif not cats_a:
            conclusion = (
                f"{day.isoformat()} 尚未入库；{against.isoformat()} "
                f"合计 {format_duration(total_b)}。"
            )
        elif not cats_b:
            conclusion = (
                f"{against.isoformat()} 尚未入库；{day.isoformat()} "
                f"合计 {format_duration(total_a)}。"
            )
        else:
            top_key = (
                max(keys, key=lambda k: abs(cats_a.get(k, 0) - cats_b.get(k, 0)))
                if keys
                else ""
            )
            top_delta = cats_a.get(top_key, 0) - cats_b.get(top_key, 0)
            if top_delta >= 0:
                top_txt = f"{top_key} 比对照日多 {format_duration(top_delta)}"
            else:
                top_txt = f"{top_key} 比对照日少 {format_duration(-top_delta)}"
            if total_delta >= 0:
                total_txt = f"今日比对照日多 {format_duration(total_delta)}"
            else:
                total_txt = f"今日比对照日少 {format_duration(-total_delta)}"
            conclusion = (
                f"{total_txt}（合计 {format_duration(total_a)} vs "
                f"{format_duration(total_b)}）；{top_txt}。"
            )
        return CompareOut(date=day, against=against, metrics=metrics, conclusion=conclusion)

    def weekly(self, day: DateType) -> WeeklyOut:
        client = ReviewClient()
        cache_key = f"{client.mode}:{day.isoformat()}"
        cached = _weekly_cache.get(cache_key)
        if cached and time.time() - cached[0] < _WEEKLY_TTL_S:
            data = cached[1]
            return WeeklyOut(
                date=day,
                available=True,
                week_start=data.get("week_start"),
                week_end=data.get("week_end"),
                total_seconds=int(data.get("total_seconds") or 0),
                days=list(data.get("days") or []),
                summary=str(data.get("summary") or ""),
                source=str(data.get("source") or client.mode),
                cached=True,
            )

        try:
            data = client.get_weekly_review(day)
        except Exception as exc:
            # 桥离线 / 上游失败：明确语义；已落库日期仍可在 /day 看
            msg = str(getattr(exc, "detail", None) or exc)
            if isinstance(exc, BridgeOfflineError) or client.bridge:
                msg = "桥离线"
            return WeeklyOut(
                date=day,
                available=False,
                offline_hint=msg or "周报不可用",
                source=client.path_kind,
            )
        total = int(data.get("total_seconds") or 0)
        days_raw = data.get("days")
        days_list: list[dict[str, Any]] = []
        if isinstance(days_raw, list):
            days_list = [d for d in days_raw if isinstance(d, dict)]
        if not total and days_list:
            total = sum(int(d.get("total_seconds") or 0) for d in days_list)
        week_start = data.get("week_start")
        week_end = data.get("week_end")
        summary = data.get("summary")
        source_name = data.get("source")
        out = WeeklyOut(
            date=day,
            available=True,
            week_start=str(week_start) if week_start else None,
            week_end=str(week_end) if week_end else None,
            total_seconds=total,
            days=days_list,
            summary=str(summary) if summary else f"本周合计 {format_duration(total)}",
            source=str(source_name or client.mode),
            cached=False,
        )
        _weekly_cache[cache_key] = (
            time.time(),
            {
                "week_start": out.week_start,
                "week_end": out.week_end,
                "total_seconds": out.total_seconds,
                "days": out.days,
                "summary": out.summary,
                "source": out.source,
            },
        )
        return out

    # ───────────────────────── 写（只写 Alongside） ─────────────────────────
    def ingest_all(self) -> dict[str, Any]:
        """★ 全量同步历史日报（astrbot 下场 · 主人「同步理应同步历史所有日报」）。

        步骤：① 向 Work-Review 要日期清单 → ② 逐个 fetch_and_store（**幂等**）。
        ★ 单个日期失败**不中断**整批（如实计数，不虚报成功）。
        """
        client = ReviewClient()
        try:
            dates = client.list_report_dates()
        except Exception as exc:
            raise _map_client_error(exc) from exc
        ok = skipped = failed = 0
        errors: list[str] = []
        for d in dates:
            try:
                day = DateType.fromisoformat(d)
            except ValueError:
                skipped += 1
                continue
            try:
                fetch_and_store(self.db, day, client)
                ok += 1
            except Exception as exc:  # 单日失败不中断
                failed += 1
                if len(errors) < 5:
                    errors.append(f"{d}: {type(exc).__name__}")
        if ok:
            event_bus.publish(
                "review.all.ingested",
                {"total": len(dates), "ok": ok, "failed": failed},
                source="review",
            )
        return {
            "total": len(dates),
            "ok": ok,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
        }

    def ingest(self, day: DateType | None = None) -> IngestOut:
        target = day or local_today()
        client = ReviewClient()
        try:
            summary = fetch_and_store(self.db, target, client)
        except Exception as exc:
            raise _map_client_error(exc) from exc
        event_bus.publish("review.day.ingested", summary, source="review")
        return IngestOut(
            date=target,
            id=str(summary.get("id") or ""),
            raw_path=str(summary.get("raw_path") or f"work-review:{target.isoformat()}"),
            is_empty=bool(summary.get("is_empty")),
            category_count=int(summary.get("category_count") or 0),
            app_count=int(summary.get("app_count") or 0),
            has_ai=bool(summary.get("has_ai")),
            has_raw=bool(summary.get("has_raw")),
            synced_at=(
                datetime.fromisoformat(summary["synced_at"])
                if summary.get("synced_at")
                else None
            ),
            mode=str(summary.get("mode") or client.mode),
            path=str(summary.get("path") or client.path_kind),
        )

    def add_note(self, day: DateType, content_md: str) -> ReviewNoteOut:
        text = (content_md or "").strip()
        if not text:
            raise ValidationError("批注内容不能为空")
        note = ReviewNote(date=day, content_md=text)
        self.db.add(note)
        self.db.commit()
        self.db.refresh(note)
        return _note_out(note)

    def delete_note(self, note_id: str) -> None:
        """删除一条主人批注（主人⑤「增减笔记」的"减"半边）。

        ★ 只删 Alongside 自己的 review_note 表记录，**不写回 review_daily，更不写回 Work-Review**。
        """
        note = self.db.get(ReviewNote, note_id)
        if note is None:
            raise NotFoundError(f"批注不存在：{note_id}")
        self.db.delete(note)
        self.db.commit()

    def list_notes(self, day: DateType | None = None) -> NotesListOut:
        if day is None:
            stmt = select(ReviewNote).order_by(col(ReviewNote.date).desc())
        else:
            stmt = (
                select(ReviewNote)
                .where(ReviewNote.date == day)
                .order_by(col(ReviewNote.created_at))
            )
        rows = list(self.db.exec(stmt).all())
        return NotesListOut(date=day, items=[_note_out(n) for n in rows])
