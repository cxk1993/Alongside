"""提案层服务：签发 → 签字执行 → 作废 / 撤销回放（bidiu 2026-11-21 · B1）。

★ 边界（ADR-0002）：本模块**不 import 任何业务插件**。执行/撤销一律走
  `core.deps.InternalHttpClient`（内核级内部 HTTP），与 mcp 的 forward.py 同源，
  但零业务词：本文件只认「方法 + 路径 + JSON」，不认识 todo / calendar。

★ 撤销的语义（诚实版）：
  · delete → 用 before 快照 **重建**（新 id、同内容）——不是把原 id 塞回去
  · update → 用 before 原值 **回写**（同一 id）
  · create → **删除**刚创建的那条（用执行时记下的 executed_id）
  V1 不提供「部分字段回滚」；快照是整行。

★ 执行身份：签字人（登录会话）的 service JWT，**不带** X-Alongside-Client: mcp
  ——语义是「AI 提议、人执行」。这也让待办的「AI 必须带 tags」护栏不误伤回放。
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from sqlmodel import Session, select

from core.errors import NotFoundError, ValidationError
from core.logging import get_logger
from core.security import USER_SUB, create_access_token

from .models import ChangeProposal
from .schema import ProposalCreate

log = get_logger("proposals.service")

#: 撤销重建时要剥掉的**系统字段**（id 由后端生成、时间戳由后端写）。
#: 只认系统级字段名，不认业务字段——保持零业务词。
_SYSTEM_KEYS: frozenset[str] = frozenset({"id", "created_at", "updated_at"})

#: 路径占位符（与 mcp/forward.py 同一套语法；本模块不依赖它，故本地实现）。
_PATH_PARAM_RE = re.compile(r"\{([^{}/]+)\}")

_DEFAULT_BASE = "http://127.0.0.1:18000"
_TIMEOUT = 15.0


def internal_base() -> str:
    """同进程服务的基址（与 mcp/forward.py 同一环境变量口径）。"""
    return os.environ.get("INTERNAL_API_BASE", _DEFAULT_BASE)


def _substitute_path(path: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """把 `{name}` 用 payload 同名值代入，并从 body 摘掉这些键。

    缺参数 ⇒ 抛 ValidationError（不猜、不打出 404 让人猜）。
    值做 URL 转义（防 `../` 拼出跨段路径）。
    """
    body = dict(payload or {})
    missing: list[str] = []
    for name in _PATH_PARAM_RE.findall(path or ""):
        if name in body and body[name] is not None:
            path = path.replace("{" + name + "}", quote(str(body.pop(name)), safe=""))
        else:
            missing.append(name)
    if missing:
        raise ValidationError(f"执行路径需要参数 {missing}，但 payload 里没有")
    return path, body


def _collection_path(path: str) -> str:
    """把「单资源路径」降成「集合路径」：剥掉末尾的 `/{占位}` 或 `/{id}` 段。

    `/api/v1/todo/items/{item_id}` → `/api/v1/todo/items`
    只做**结构**判断（末段是否是占位符），不认业务名。
    """
    parts = path.rstrip("/").split("/")
    # 末段整段是占位符 → 剥掉；否则原样返回（调用方应确认这是单资源路径）
    if parts and _PATH_PARAM_RE.fullmatch(parts[-1]):
        return "/".join(parts[:-1])
    return path.rstrip("/")


def _clean_for_recreate(before: Any) -> dict[str, Any]:
    """把 before 快照洗干净成「可重建的正文」：剥系统字段。"""
    if not isinstance(before, dict):
        raise ValidationError("撤销需要 before 快照（dict），这条提案没有")
    return {k: v for k, v in before.items() if k not in _SYSTEM_KEYS}


def _request(method: str, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    """打一次内部 HTTP（service JWT）。返回 (状态码, JSON 或 None)；非 2xx 不抛。

    与 mcp/forward.py 同一通道口径（INTERNAL_API_BASE + service JWT），
    但**不带** X-Alongside-Client: mcp —— 语义是「AI 提议、人执行」。
    """
    real_path, body = _substitute_path(path, payload)
    headers = {"Authorization": f"Bearer {create_access_token(USER_SUB)}"}
    try:
        with httpx.Client(base_url=internal_base(), timeout=_TIMEOUT) as c:
            m = method.upper()
            if m == "GET":
                resp = c.get(real_path, params=body or None, headers=headers)
            else:
                resp = c.request(m, real_path, json=body if body else None, headers=headers)
    except Exception as exc:  # noqa: BLE001 —— 网络失败转可读错误（状态不推进）
        raise ValidationError(f"内部执行失败：{type(exc).__name__}: {exc}") from exc
    if resp.status_code == 204 or not resp.content:
        return resp.status_code, None
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {"raw": resp.text[:500]}


def _digest(data: Any, limit: int = 2000) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, default=str)[:limit] if data is not None else ""
    except Exception:  # noqa: BLE001
        return str(data)[:limit]


class ProposalsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ───────────────────────── 签发 / 查询 ─────────────────────────

    def create(self, body: ProposalCreate) -> ChangeProposal:
        row = ChangeProposal(
            title=body.title,
            tool_name=body.tool_name,
            action=body.action,
            method=body.method or "",
            path=body.path or "",
            payload=body.payload or {},
            before=body.before,
            actor=body.actor or "human",
            status="pending",
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        log.info("提案已签发", extra={"proposal": row.id, "tool": row.tool_name, "actor": row.actor})
        return row

    def get(self, pid: str) -> ChangeProposal:
        row = self.db.get(ChangeProposal, pid)
        if row is None:
            raise NotFoundError(f"提案不存在：{pid}")
        return row

    def list(self, status: str | None = None, limit: int = 100) -> list[ChangeProposal]:
        stmt = select(ChangeProposal).order_by(ChangeProposal.created_at.desc())  # type: ignore[attr-defined]
        if status:
            stmt = stmt.where(ChangeProposal.status == status)
        return list(self.db.exec(stmt.limit(limit)).all())

    # ───────────────────────── 签字 / 作废 ─────────────────────────

    def approve(self, pid: str) -> tuple[ChangeProposal, str]:
        """签字：按提案记下的方法/路径/参数正向执行一次内部 HTTP。

        ★ 执行失败**不推进状态**（签字人可重试）；成功才落 approved + 执行证据。
        """
        row = self.get(pid)
        if row.status != "pending":
            raise ValidationError(f"只有 pending 的提案能签字，当前 {row.status!r}")
        if not row.method or not row.path:
            raise ValidationError("提案缺少 method/path（签发时没带，无法执行）")
        status, data = _request(row.method, row.path, dict(row.payload or {}))
        if status >= 400:
            raise ValidationError(f"执行失败 HTTP {status}：{_digest(data, 300)}")
        row.status = "approved"
        row.executed_at = datetime.now(UTC)
        row.executed_status = status
        row.executed_id = _extract_id(data)
        detail = _digest(data)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        log.info("提案已签字执行", extra={"proposal": row.id, "http": status, "executed_id": row.executed_id})
        return row, detail

    def reject(self, pid: str) -> ChangeProposal:
        row = self.get(pid)
        if row.status != "pending":
            raise ValidationError(f"只有 pending 的提案能作废，当前 {row.status!r}")
        row.status = "rejected"
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    # ───────────────────────── 撤销回放 ─────────────────────────

    def undo(self, pid: str) -> tuple[ChangeProposal, str]:
        """撤销：按 action 回放 before。**失败不推进状态**（可重试）。"""
        row = self.get(pid)
        if row.status != "approved":
            raise ValidationError(f"只有已执行的提案能撤销，当前 {row.status!r}")

        if row.action == "create":
            # 撤销 create = 删掉刚建的那条（用执行时记下的 id）
            if not row.executed_id:
                raise ValidationError("这条 create 提案没有记下执行产物 id，无法撤销")
            status, data = _request("DELETE", row.path, {"id": row.executed_id})
        elif row.action == "update":
            # 撤销 update = 用 before 原值回写同一资源
            body = self._with_path_params(row, _clean_for_recreate(row.before))
            status, data = _request(row.method or "PATCH", row.path, body)
        elif row.action == "delete":
            # 撤销 delete = 用 before 整行重建（新 id、同内容）
            status, data = _request("POST", _collection_path(row.path), _clean_for_recreate(row.before))
        else:  # pragma: no cover —— 签发时 validate_action 已拦
            raise ValidationError(f"不支持撤销的动作：{row.action!r}")

        if status >= 400:
            raise ValidationError(f"撤销失败 HTTP {status}：{_digest(data, 300)}")
        row.status = "undone"
        row.undone_at = datetime.now(UTC)
        detail = _digest(data)
        row.undo_result = detail
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        log.info("提案已撤销", extra={"proposal": row.id, "action": row.action, "http": status})
        return row, detail

    def _with_path_params(self, row: ChangeProposal, body: dict[str, Any]) -> dict[str, Any]:
        """撤销回放时，路径占位符的值从原 payload 里取回（body 是 before，没有 id）。"""
        merged = dict(body)
        for name in _PATH_PARAM_RE.findall(row.path or ""):
            if name not in merged and isinstance(row.payload, dict) and name in row.payload:
                merged[name] = row.payload[name]
        return merged


def _extract_id(data: Any) -> str | None:
    """从执行结果里尽力取 `id`（create 类撤销定位用）。取不到就 None，不猜。"""
    if isinstance(data, dict):
        val = data.get("id")
        return str(val) if val else None
    return None