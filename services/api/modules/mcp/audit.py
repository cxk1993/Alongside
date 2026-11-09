"""审计（T18 §E）：经 MCP 的操作写入内核 audit_log（T04 公共设施）。

字段映射（AuditLog 无 source 列，内核表不可改 —— 用 actor 前缀编码来源）：
  actor  = "mcp:{token_prefix}"   ← source='mcp' 的等价物，前端按此前缀过滤
  action = tool_name（成功） / "denied"（越权拒绝）
  target = "{METHOD} {path}"
  payload_hash = 请求体规范化 JSON 的 SHA-256（不落明文参数，与内核审计口径一致）
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlmodel import select

from core.deps import get_db, kernel_session
from db.models.system import AuditLog

MCP_ACTOR_PREFIX = "mcp:"


def _payload_digest(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def audit_call(
    *,
    token_prefix: str,
    tool_name: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    """写操作成功执行后落一条（读操作不强制，与 REST 口径一致）。"""
    with kernel_session() as db:
        db.add(
            AuditLog(
                actor=(MCP_ACTOR_PREFIX + token_prefix)[:64],
                action=tool_name[:64],
                target=f"{method} {path}"[:200],
                payload_hash=_payload_digest(payload),
            )
        )
        db.commit()


def audit_denied(
    *,
    token_prefix: str,
    tool_name: str,
    reason: str,
) -> None:
    """越权 / 未知工具等拒绝也落一条（同 T14 越权处理口径）。"""
    with kernel_session() as db:
        db.add(
            AuditLog(
                actor=(MCP_ACTOR_PREFIX + token_prefix)[:64],
                action="denied",
                target=f"{tool_name} {reason}"[:200],
            )
        )
        db.commit()


def list_mcp_audit(limit: int = 100) -> list[AuditLog]:
    """前端 CallLog 用：最近 N 条 MCP 流水（actor 以 mcp: 开头）。"""
    with kernel_session() as db:
        rows = db.exec(
            select(AuditLog)
            .where(AuditLog.actor.startswith(MCP_ACTOR_PREFIX))
            .order_by(AuditLog.at.desc())  # type: ignore[attr-defined]
            .limit(limit)
        ).all()
        return list(rows)
