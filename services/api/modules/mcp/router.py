"""MCP 路由（T18）。

HTTP 契约（全项目统一，不许自创）：
  成功 → 直接返回资源 JSON（200/201/202/204）
  失败 → 由内核统一转成 RFC7807 application/problem+json

★ 本文件**不要**加 `from __future__ import annotations`（内核纪律，
  见 modules/calendar/router.py 头注）。
★ 前缀由内核按 manifest.api.base 自动加（/api/v1/mcp），这里不写 prefix=。
★ 内部一律包路径导入（内置插件的 router.py 走包导入，相对导入可用）。

端点总览：
  POST /                MCP Streamable HTTP（AI 客户端，PAT 鉴权）
  GET  /tools           调试用：当前暴露的全部 tool（登录用户）
  GET  /audit-logs      经 MCP 的操作流水（登录用户）
  POST /pats            创建 PAT（明文只回一次）
  GET  /pats            PAT 列表
  PATCH /pats/{id}      改 scopes
  DELETE /pats/{id}     吊销（置 revoked_at 即失效）
"""
import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi import Path as FPath
from fastapi.responses import JSONResponse
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from core.deps import get_current_user, get_db
from core.errors import ForbiddenError, NotFoundError, ValidationError
from core.mcp_writes import is_mcp_call
from core.security import User

from . import audit
from .auth import (
    PatContext,
    generate_pat,
    mark_pat_revoked,
    require_pat,
)
from .mcp_server import handle_message
from .models import McpPat
from .registry_adapter import build_tool_map
from .schemas import (
    AuditOut,
    PatCreatedOut,
    PatCreateIn,
    PatOut,
    PatScopesPatch,
    ToolOut,
)

router = APIRouter()

_MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"
with _MANIFEST_PATH.open(encoding="utf-8") as _f:
    _MANIFEST = json.load(_f)

DbDep = Session
UserDep = User
PatDep = PatContext


def _pat_out(row: McpPat) -> PatOut:
    return PatOut(
        id=row.id,
        name=row.name,
        token_prefix=row.token_prefix,
        scopes=[s for s in (row.scopes or "").split(",") if s],
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


# ───────────────────────── MCP 协议端点（AI 客户端） ─────────────────────────


@router.get("/health")
def health() -> dict[str, bool]:
    """每个插件都必须有 health —— 内核据此判断"该能力是否可用"。"""
    return {"ok": True}


@router.get("/manifest")
def manifest() -> dict:
    """模块清单（前端 / AI 发现能力用）。"""
    return _MANIFEST


@router.post("")
async def mcp_http(
    request: Request,
    pat: PatDep = Depends(require_pat),
) -> Response:
    """MCP Streamable HTTP 入口（stateless request-response）。

    - JSON-RPC 请求 → application/json 单响应
    - notification → 202（无 body）
    - 客户端 Accept 应含 application/json 或 text/event-stream（宽松校验）

    ★ handle_message 里含同步 HTTP 转发（forward → httpx），必须丢线程池跑：
      若在事件循环里同步等转发，tools/call 调本服务自己的 REST 端点会
      自死锁（事件循环被占住，目标请求永远得不到调度，实测踩过）。
    """
    accept = request.headers.get("accept", "")
    if accept and not any(
        candidate in accept
        for candidate in ("application/json", "text/event-stream", "*/*")
    ):
        raise ValidationError(
            "MCP 端点要求 Accept 含 application/json 或 text/event-stream"
        )
    try:
        msg = await request.json()
    except ValueError as exc:
        raise ValidationError(f"请求体不是合法 JSON：{exc}") from exc
    if not isinstance(msg, dict):
        # 批量消息（数组）暂不支持 —— 单条请求即可覆盖三原语。
        raise ValidationError("MCP 消息必须是单个 JSON-RPC 对象")
    result = await run_in_threadpool(handle_message, msg, pat)
    if result is None:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return JSONResponse(result)


# ───────────────────────── 调试 / 审计（登录用户） ─────────────────────────


@router.get("/tools", response_model=list[ToolOut])
def tools_debug(_user: UserDep = Depends(get_current_user)) -> list[ToolOut]:
    """当前暴露给 AI 的全部工具（= tools/list 的纯 JSON 版，给人看）。"""
    return [
        ToolOut(
            name=t.name,
            description=t.description,
            method=t.method,
            path=t.path,
            scope=t.scope,
            plugin_id=t.plugin_id,
        )
        for t in build_tool_map()
    ]


@router.get("/audit-logs", response_model=list[AuditOut])
def audit_logs(
    limit: int = Query(100, ge=1, le=500, description="返回条数上限（1–500，默认 100）"),
    _user: UserDep = Depends(get_current_user),
) -> list[AuditOut]:
    """经 MCP 的操作流水（actor 以 mcp: 开头的 audit_log 行）。"""
    return [
        AuditOut(id=r.id, actor=r.actor, action=r.action, target=r.target, at=r.at)
        for r in audit.list_mcp_audit(limit)
    ]


# ───────────────────────── PAT 管理（登录用户） ─────────────────────────


@router.post("/pats", response_model=PatCreatedOut, status_code=status.HTTP_201_CREATED)
def create_pat(
    body: PatCreateIn,
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> PatCreatedOut:
    """创建 PAT。**明文只在本次响应出现一次**，落库只有哈希。

    ★ S2（bidiu 2026-11-21）：expires_at 补上**写入路径**——此前该列有校验、无来源，
    所有 PAT 事实永不过期。现在可选传入，缺省 None=不设期限（向后兼容）。
    """
    plain, token_hash, prefix = generate_pat()
    row = McpPat(
        name=body.name,
        token_hash=token_hash,
        token_prefix=prefix,
        scopes=",".join(body.scopes),
        expires_at=body.expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return PatCreatedOut(
        id=row.id,
        name=row.name,
        token=plain,
        token_prefix=prefix,
        scopes=body.scopes,
        created_at=row.created_at,
    )


@router.get("/pats", response_model=list[PatOut])
def list_pats(
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> list[PatOut]:
    """PAT 列表（前缀 / 名称 / scopes / 最后使用 / 是否吊销）。无明文。"""
    rows = db.exec(select(McpPat).order_by(McpPat.created_at.desc())).all()  # type: ignore[attr-defined]
    return [_pat_out(r) for r in rows]


@router.patch("/pats/{pat_id}", response_model=PatOut)
def patch_pat(
    pat_id: Annotated[str, FPath(min_length=1, description="PAT id（从 GET /pats 列表里取）")],
    body: PatScopesPatch,
    request: Request,
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> PatOut:
    """改 PAT scopes（重新授权 / 收窄范围）。

    ★ S1（bidiu 2026-11-21）：MCP 来路一律拒绝。
    forward() 打到本端点的是 admin service JWT + X-Alongside-Client: mcp——
    不拦的话，一枚持有 mcp:patch scope 的 PAT 可经 tools/call 把自己（或别的
    PAT）scopes 改成全权限 = 自我提权。密钥管理是主人登录会话的专属动作。
    """
    if is_mcp_call(request):
        raise ForbiddenError(
            "PAT 改权只允许主人登录会话操作；MCP tools/call 来路一律拒绝（防自我提权）"
        )
    row = db.get(McpPat, pat_id)
    if row is None:
        raise NotFoundError(f"PAT 不存在：{pat_id}")
    row.scopes = ",".join(body.scopes)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _pat_out(row)


@router.delete("/pats/{pat_id}")
def revoke_pat(
    pat_id: Annotated[str, FPath(min_length=1, description="PAT id（从 GET /pats 列表里取）")],
    request: Request,
    db: DbDep = Depends(get_db),
    user: UserDep = Depends(get_current_user),
) -> Response:
    """吊销 PAT：置 revoked_at，之后该 PAT 立即 401。幂等。

    ★ S1（bidiu 2026-11-21）：MCP 来路一律拒绝——吊销他人/自己的密钥
    同样属密钥管理，只归主人登录会话。
    """
    if is_mcp_call(request):
        raise ForbiddenError(
            "PAT 吊销只允许主人登录会话操作；MCP tools/call 来路一律拒绝"
        )
    mark_pat_revoked(db, pat_id, by=user.sub)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
