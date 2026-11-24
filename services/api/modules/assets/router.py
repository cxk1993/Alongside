"""插件路由：资产层（可沉淀的经验）。

HTTP 契约（全项目统一，不许自创）：
  成功 → 直接返回资源 JSON（200/201）
  失败 → 由内核统一转成 RFC7807 application/problem+json

★ 前缀由内核按 manifest.api.base 自动加，这里不要写 prefix=。
★ 路径参数用 Annotated[str, FPath()]；body 必填不写 = None。
★ 204 端点写 `-> None`（本文件不要加 future import）。
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi import Path as FPath
from sqlmodel import Session

from core.deps import get_current_user, get_db
from core.security import User

from .models import Asset
from .schema import AssetCreate, AssetOut
from .service import AssetsService

router = APIRouter()

DbDep = Session
UserDep = User


def _out(row: object) -> AssetOut:
    r: Asset = row  # type: ignore[assignment]
    return AssetOut(
        schema_version=r.schema_version,
        id=r.id,
        type=r.type,
        title=r.title,
        body=r.body,
        summary=r.summary,
        provenance=r.provenance,
        supersedes=r.supersedes,
        status=r.status,
        superseded_by=r.superseded_by,
        tier=r.tier,
        scope=r.scope,
        exportable=r.exportable,
        tags=r.tags,
        refs=r.refs,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


@router.get("/health")
def assets_health() -> dict[str, bool]:
    """健康检查：资产模块是否已激活（不碰数据库）。"""
    return {"ok": True}


@router.post("", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
def create_asset(
    body: AssetCreate,
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> AssetOut:
    """沉淀一条资产（带 supersedes 时同事务完成取代双向连线）。"""
    return _out(AssetsService(db).create(body))


@router.get("", response_model=list[AssetOut])
def list_assets(
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
    status: str | None = Query(default=None, description="active/superseded/draft"),
    type: str | None = Query(default=None, description="note/skill/checklist/decision/procedure"),
    tier: str | None = Query(default=None, description="public/work/private/sealed"),
    q: str | None = Query(default=None, description="标题/摘要模糊匹配"),
    limit: int = Query(default=100, ge=1, le=500, description="最多返回几条（1~500，默认 100）"),
) -> list[AssetOut]:
    """按条件筛资产列表（全部条件可选，不传就是全量前 N 条）。

    典型用法：`?type=decision&tier=work&q=提案` = 「工作层里跟提案有关的决策卡」。
    排序：新建的在前。`limit` 上限 500，防止一次拉爆。
    """
    return [
        _out(r)
        for r in AssetsService(db).list(status=status, type=type, tier=tier, q=q, limit=limit)
    ]


@router.get("/{asset_id}", response_model=AssetOut)
def read_asset(
    asset_id: Annotated[str, FPath(max_length=40, description="资产 id（as_ 开头）")],
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> AssetOut:
    """读一条资产的完整内容（含被它取代 / 取代它的双向连线）。

    不存在时返回 404，不返回空对象 —— 「没有」和「有一条空的」是两回事。
    """
    return _out(AssetsService(db).get(asset_id))


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(
    asset_id: Annotated[str, FPath(max_length=40, description="资产 id（as_ 开头）")],
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> None:
    """硬删（契约硬规则 3：用户有权真正删除）。被取代链上的反链一并清理。"""
    AssetsService(db).delete(asset_id)