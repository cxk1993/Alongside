"""资产层服务：沉淀 → 查询 → 取代（append-only）/ 硬删（bidiu 2026-11-22）。

契约：contracts/asset.schema.json。三条硬规则：
1) append-only + 显式 supersede —— 更新 = 新建一条 + 旧条标 superseded（内容永不改写）
2) provenance 必填（agent + at）
3) 可单条硬删

SSE：assets.asset.created / assets.asset.superseded / assets.asset.deleted
（manifest emits 声明了就必须真发 · 判据 5）。
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from core.errors import NotFoundError, ValidationError
from core.events import event_bus
from core.logging import get_logger

from .models import Asset
from .schema import AssetCreate

log = get_logger("assets.service")

#: 资产写操作串行化（create 含 supersede 双向连线、delete 含反链清理）。
#: ★ 防的是「两个并发 create 同时 supersede 同一旧条」→ 旧条 superseded_by 被后写覆盖，
#:   第一个新条声称 supersedes 它但旧条不认（数据不一致）。
#: ★ 范围：单进程有效（uvicorn 单 worker）；多 worker 需 DB 级约束
#:   （如唯一部分索引 / 事务内 SELECT … 再判），与 proposals ISSUE-015 同类。
_WRITE_LOCK = threading.Lock()


def _dump(row: Asset) -> dict[str, Any]:
    """资产行 → 事件载荷（契约字段全带，时间 ISO）。"""

    def _iso(v: Any) -> Any:
        return v.isoformat() if isinstance(v, datetime) else v

    return {
        "schema_version": row.schema_version,
        "id": row.id,
        "type": row.type,
        "title": row.title,
        "body": row.body,
        "summary": row.summary,
        "provenance": row.provenance,
        "supersedes": row.supersedes,
        "status": row.status,
        "superseded_by": row.superseded_by,
        "tier": row.tier,
        "scope": row.scope,
        "exportable": row.exportable,
        "tags": row.tags,
        "refs": row.refs,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


class AssetsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ───────────────────────── 沉淀 ─────────────────────────

    def create(self, body: AssetCreate) -> Asset:
        """新建一条资产。带 supersedes 时同一事务里把旧条标 superseded + 反向指针。"""
        with _WRITE_LOCK:
            return self._create_locked(body)

    def _create_locked(self, body: AssetCreate) -> Asset:
        # supersedes 必指向真实存在的旧条（不猜、不静默丢弃）
        old_rows: list[Asset] = []
        for old_id in body.supersedes:
            old = self.db.get(Asset, old_id)
            if old is None:
                raise ValidationError(f"supersedes 指向不存在的 asset：{old_id}")
            if old.status == "superseded":
                raise ValidationError(f"asset {old_id} 已被取代，不能再被取代一次")
            old_rows.append(old)

        prov = body.provenance.model_dump()
        at = prov.get("at")
        prov["at"] = (at if at is not None else datetime.now(UTC)).isoformat()

        row = Asset(
            type=body.type,
            title=body.title,
            body=body.body,
            summary=body.summary,
            provenance=prov,
            supersedes=list(body.supersedes),
            status=body.status,
            tier=body.tier,
            scope=body.scope,
            exportable=body.exportable,
            tags=list(body.tags),
            refs=[r.model_dump() for r in body.refs],
        )
        for old in old_rows:
            old.status = "superseded"
            old.superseded_by = row.id
            self.db.add(old)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)

        event_bus.publish("assets.asset.created", _dump(row), source="assets")
        for old in old_rows:
            self.db.refresh(old)
            event_bus.publish("assets.asset.superseded", _dump(old), source="assets")
        log.info(
            "资产已沉淀",
            extra={"asset": row.id, "type": row.type, "supersedes": list(body.supersedes)},
        )
        return row

    # ───────────────────────── 查询 ─────────────────────────

    def get(self, asset_id: str) -> Asset:
        row = self.db.get(Asset, asset_id)
        if row is None:
            raise NotFoundError(f"资产不存在：{asset_id}")
        return row

    def list(
        self,
        *,
        status: str | None = None,
        type: str | None = None,  # noqa: A002 —— 参数名对齐契约字段
        tier: str | None = None,
        q: str | None = None,
        limit: int = 100,
    ) -> list[Asset]:
        stmt = select(Asset).order_by(Asset.created_at.desc())  # type: ignore[attr-defined]
        if status:
            stmt = stmt.where(Asset.status == status)
        if type:
            stmt = stmt.where(Asset.type == type)
        if tier:
            stmt = stmt.where(Asset.tier == tier)
        if q:
            like = f"%{q}%"
            stmt = stmt.where((Asset.title.like(like)) | (Asset.summary.like(like)))  # type: ignore[attr-defined]
        return list(self.db.exec(stmt.limit(limit)).all())

    # ───────────────────────── 硬删（契约硬规则 3） ─────────────────────────

    def delete(self, asset_id: str) -> None:
        with _WRITE_LOCK:
            self._delete_locked(asset_id)

    def _delete_locked(self, asset_id: str) -> None:
        row = self.get(asset_id)
        # 被别的资产 supersede 时，ssuperseded_by 指向它 —— 清理反链，避免悬挂指针
        children = self.db.exec(
            select(Asset).where(Asset.superseded_by == asset_id)
        ).all()
        for child in children:
            child.superseded_by = None
            self.db.add(child)
        self.db.delete(row)
        self.db.commit()
        event_bus.publish(
            "assets.asset.deleted", {"id": asset_id, "title": row.title}, source="assets"
        )
        log.info("资产已硬删", extra={"asset": asset_id, "children_unlinked": len(children)})