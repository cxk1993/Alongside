"""插件路由：变更提案（签字中心的后端）。

HTTP 契约（全项目统一，不许自创）：
  成功 → 直接返回资源 JSON（200/201）
  失败 → 由内核统一转成 RFC7807 application/problem+json

★ 前缀由内核按 manifest.api.base 自动加，这里不要写 prefix=。
★ 路径参数用 Annotated[str, FPath()]；body 必填不写 = None。
★ 204 端点写 `-> None`（本文件不要加 future import）。
"""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi import Path as FPath
from sqlmodel import Session

from core.deps import get_current_user, get_db
from core.security import User

from .schema import ProposalCreate, ProposalOpOut, ProposalOut
from .service import ProposalsService

router = APIRouter()

# ★ 依赖别名只做类型，不要把 Depends 塞进 Annotated。
DbDep = Session
UserDep = User


def _out(row: object) -> ProposalOut:
    from .models import ChangeProposal

    r: ChangeProposal = row  # type: ignore[assignment]
    return ProposalOut(
        id=r.id,
        title=r.title,
        tool_name=r.tool_name,
        action=r.action,
        method=r.method,
        path=r.path,
        payload=r.payload if isinstance(r.payload, dict) else None,
        before=r.before if isinstance(r.before, dict) else None,
        actor=r.actor,
        status=r.status,
        created_at=r.created_at,
        executed_at=r.executed_at,
        executed_id=r.executed_id,
        executed_status=r.executed_status,
        executed_by=r.executed_by,
        undone_at=r.undone_at,
        undone_by=r.undone_by,
        undo_result=r.undo_result,
    )


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.post("", response_model=ProposalOut, status_code=status.HTTP_201_CREATED)
def create_proposal(
    body: ProposalCreate,
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> ProposalOut:
    """签发一条变更提案（pending）。**不执行任何业务动作**。"""
    return _out(ProposalsService(db).create(body))


#: 列表过滤的合法状态值（= models 里真实出现的四种，外加 `all` 表示不过滤）。
#  ★ 2026-11-22：此前 `?status=all` 会精确匹配到 0 行 —— 未知值静默给空集，
#    前端（或人）会误判「没有数据」。判据 5：不猜、不静默给错答案。
#    现在：`all` 与空串（`?status=`，部分 HTTP 客户端会这么发）等价于不传，
#    其它未定义值由 FastAPI 直接 422。
ProposalStatusFilter = Literal["pending", "approved", "rejected", "undone", "all", ""]


@router.get("", response_model=list[ProposalOut])
def list_proposals(
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
    status_filter: Annotated[ProposalStatusFilter | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ProposalOut]:
    """提案列表（签字中心数据源）。

    `?status=pending` 只看待签；`?status=all` 与省略等价（不过滤）；
    其余取值 422（不再静默返回空列表）。
    """
    effective = None if status_filter in (None, "", "all") else status_filter
    return [_out(r) for r in ProposalsService(db).list(effective, limit)]


@router.get("/{proposal_id}", response_model=ProposalOut)
def get_proposal(
    proposal_id: Annotated[str, FPath(min_length=1, description="提案 id")],
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> ProposalOut:
    return _out(ProposalsService(db).get(proposal_id))


@router.post("/{proposal_id}/approve", response_model=ProposalOpOut)
def approve_proposal(
    proposal_id: Annotated[str, FPath(min_length=1, description="提案 id")],
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> ProposalOpOut:
    """签字执行。失败不推进状态（可重试）。"""
    row, detail = ProposalsService(db).approve(proposal_id, by=_user.sub)
    return ProposalOpOut(id=row.id, status=row.status, detail=detail)


@router.post("/{proposal_id}/reject", response_model=ProposalOpOut)
def reject_proposal(
    proposal_id: Annotated[str, FPath(min_length=1, description="提案 id")],
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> ProposalOpOut:
    row = ProposalsService(db).reject(proposal_id)
    return ProposalOpOut(id=row.id, status=row.status, detail="已作废")


@router.post("/{proposal_id}/undo", response_model=ProposalOpOut)
def undo_proposal(
    proposal_id: Annotated[str, FPath(min_length=1, description="提案 id")],
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> ProposalOpOut:
    """撤销回放：delete→重建 / update→回写 / create→删除。"""
    row, detail = ProposalsService(db).undo(proposal_id, by=_user.sub)
    return ProposalOpOut(id=row.id, status=row.status, detail=detail)
