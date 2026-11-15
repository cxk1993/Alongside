"""WE Learn 路由：只读课程、单元与进度；未完成单元可转待办。

════════════════════════════════════════════════════════════════════
★ 已用真实账号校准（2026-11-15，welearn.sflep.com）：
    GET /ajax/authCourse.aspx?action=gmc      → {"ret":0,"clist":[...]}
    GET /student/course_info.aspx?cid=<cid>    → HTML（单元 + 已完成/总数）
★ 边界：只发 GET；没有提交作业/刷课/答题的任何写回路径。
★ 鉴权只靠 Cookie（.AspNet.Cookies + ASP.NET_SessionId），无签名。
★ 模块加载：本插件由内核以 `plugin_thirdparty_welearn` 包注册，**可以相对导入**
  （r5 起已支持，见 core/plugins/discover.py）；勿再手写 sys.modules 加载器。
════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from core.deps import get_current_user, get_db, get_plugin_client
from core.errors import NotFoundError, UnauthorizedError, ValidationError
from core.events import event_bus
from core.security import User
from db.base import utcnow

_DB = Session
log = logging.getLogger(__name__)
router = APIRouter()

_MAX_ITEMS = 100
_MAX_CREATE = 40

#: 单元块：<h4 class="panel-title">… Unit 1 Travel … 已完成：19 /19
#: ★ r8：两侧数字都可能各自被 `<span>` 包住（span 上还可能带 class），
#:   旧写法只容忍第一个数字有 span ⇒ 真站若写成 `<span>19</span>/<span>19</span>` 会整页解析成空。
#:   （此为按结构推断的加固，尚无真实快照佐证；拿到脱敏快照后用测试对齐。）
_UNIT_RE = re.compile(
    r'<h4 class="panel-title">\s*<a[^>]*>(.*?)'
    r'已完成：\s*(?:<span[^>]*>)?\s*(\d+)\s*(?:</span>)?\s*/\s*'
    r'(?:<span[^>]*>)?\s*(\d+)',
    re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")


# ════════════════════════════ 模块加载 ════════════════════════════
# ★ r8（N1）：内核已把 `api/` 注册成包 `plugin_thirdparty_welearn`，走相对导入。
#   与路由共用同一份模块对象 ⇒ 卸载能一并清掉，不会「新路由配旧表模型」。
from . import client as client_mod  # noqa: E402
from . import models  # noqa: E402


def _models() -> Any:
    return models


def _client_mod() -> Any:
    return client_mod


class SessionIn(BaseModel):
    cookie: str = Field(
        ..., min_length=16, max_length=4000,
        description="浏览器 DevTools 里的完整 Cookie 串（要含 .AspNet.Cookies 和 ASP.NET_SessionId）",
    )
    base_url: str = Field(default="https://welearn.sflep.com", max_length=300)


class SyncIn(BaseModel):
    cid: str | None = Field(default=None, description="只同步某门课；不传则全部")
    include_done: bool = Field(default=False, description="已完成的单元是否也建待办")
    max_items: int = Field(default=20, ge=1, le=_MAX_ITEMS)


def _session_row(db: _DB) -> Any:
    models = _models()
    return db.exec(select(models.WelearnSession)).first()


def _require_session(db: _DB) -> Any:
    row = _session_row(db)
    if row is None:
        raise NotFoundError("还没配置 WE Learn 会话：先 POST /session")
    return row


def _mask(cookie: str) -> str:
    return f"len={len(cookie)} 值已隐藏"


# ════════════════════════════ HTML 解析 ════════════════════════════
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", text)).strip()


def parse_units(html: str) -> list[dict[str, Any]]:
    """从课程页 HTML 里抽单元与进度（平台改版只改这里）。"""
    units: list[dict[str, Any]] = []
    for name, done, total in _UNIT_RE.findall(html):
        title = _clean(name)
        if not title:
            continue
        done_n, total_n = _to_int(done), _to_int(total)
        if total_n <= 0:
            # 「课程说明」这类不是学习单元（0/0），跳过
            continue
        units.append(
            {
                "name": title,
                "done": done_n,
                "total": total_n,
                "percent": round(done_n * 100 / total_n) if total_n else 0,
                "finished": total_n > 0 and done_n >= total_n,
            }
        )
    return units


def _to_int(value: Any, default: int = 0) -> int:
    """上游数字可能是 "12" / "12%" / None —— 解析失败给默认值，不能抛。

    ★ 裸 `int(row.get("per") or 0)` 在平台改版把 `per` 写成 "12%" 时会 ValueError，
      而调用点只捕 WelearnError ⇒ 整窗 500。
    """
    try:
        text = str(value).strip().rstrip("%").strip()
        return int(float(text)) if ("." in text) else int(text)
    except (TypeError, ValueError):
        return default


def _course_name(rows: list[dict[str, Any]], cid: str) -> str:
    for row in rows:
        if str(row.get("cid")) == str(cid):
            return str(row.get("name") or "")
    return f"课程 {cid}"


def _courses(client: Any) -> list[dict[str, Any]]:
    payload = client.courses()
    rows = payload.get("clist") if isinstance(payload, dict) else None
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("cid") in (None, ""):
            continue
        out.append(
            {
                "cid": str(row.get("cid")),
                "scid": str(row.get("scid") or row.get("cid")),
                "name": str(row.get("name") or "未命名课程"),
                "task_count": _to_int(row.get("taskcount")),
                "percent": _to_int(row.get("per")),
            }
        )
    return out


# ════════════════════════════ 端点 ════════════════════════════
@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.post("/session")
def save_session(
    body: SessionIn,
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    models = _models()
    client_mod = _client_mod()

    try:
        # 构造可能抛 shape 错（域名不在白名单 / 非 https），必须在 try 内 → 返回人话 4xx。
        client = client_mod.WelearnClient(body.cookie, body.base_url)
        payload = client.courses()
    except client_mod.WelearnError as exc:
        raise UnauthorizedError(f"会话校验失败：{exc}") from exc

    rows = payload.get("clist") or []
    name = str((rows[0] or {}).get("name") or "")[:120] if rows else ""

    row = _session_row(db)
    if row is None:
        row = models.WelearnSession(cookie=body.cookie, base_url=body.base_url)
    row.cookie = body.cookie
    row.base_url = body.base_url
    row.user_name = name
    row.last_verified_at = utcnow()
    row.last_error = ""
    db.add(row)
    db.commit()
    return {"ok": True, "courses": len(rows), "first_course": name, "base_url": row.base_url}


@router.delete("/session")
def delete_session(
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, bool]:
    models = _models()
    for row in db.exec(select(models.WelearnSession)).all():
        db.delete(row)
    db.commit()
    return {"deleted": True}


@router.get("/status")
def status(
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _session_row(db)
    if row is None:
        return {"configured": False, "hint": "POST /session 保存 Cookie"}
    return {
        "configured": True,
        "base_url": row.base_url,
        "cookie": _mask(row.cookie),
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
    }


@router.get("/courses")
def list_courses(
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _require_session(db)
    client_mod = _client_mod()
    try:
        courses = _courses(client_mod.WelearnClient(row.cookie, row.base_url))
    except client_mod.WelearnError as exc:
        if exc.kind == "auth":
            raise UnauthorizedError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    return {"count": len(courses), "courses": courses}


@router.get("/units")
def list_units(
    cid: str = Query(..., min_length=1),
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _require_session(db)
    client_mod = _client_mod()
    client = client_mod.WelearnClient(row.cookie, row.base_url)
    try:
        courses = _courses(client)
        html = client.course_page(cid)
    except client_mod.WelearnError as exc:
        if exc.kind == "auth":
            raise UnauthorizedError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    units = parse_units(html)
    return {
        "cid": cid,
        "course": _course_name(courses, cid),
        "count": len(units),
        "units": units,
    }


@router.post("/sync")
def sync_to_todo(
    body: SyncIn,
    request: Request,
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """把「还没刷完的单元」写成待办（幂等）。"""
    models = _models()
    client_mod = _client_mod()
    row = _require_session(db)
    client = client_mod.WelearnClient(row.cookie, row.base_url)

    try:
        courses = _courses(client)
    except client_mod.WelearnError as exc:
        if exc.kind == "auth":
            raise UnauthorizedError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc

    if body.cid:
        courses = [c for c in courses if c["cid"] == body.cid]
        if not courses:
            raise NotFoundError(f"课程列表里没有 cid={body.cid}")

    todo_client = get_plugin_client(request, ["todo.item.write"])
    created: list[dict[str, Any]] = []
    skipped_known = 0
    skipped_done = 0
    failed: list[dict[str, str]] = []

    for course in courses:
        if len(created) >= _MAX_CREATE:
            break
        cid = course["cid"]
        try:
            units = parse_units(client.course_page(cid))
        except client_mod.WelearnError as exc:
            failed.append({"cid": cid, "error": str(exc)})
            continue

        for unit in units[: body.max_items]:
            if len(created) >= _MAX_CREATE:
                break
            if unit["finished"] and not body.include_done:
                skipped_done += 1
                continue
            key = f"{cid}:{unit['name']}"
            known = db.exec(
                select(models.WelearnSynced).where(models.WelearnSynced.unit_key == key)
            ).first()
            if known is not None:
                skipped_known += 1
                continue

            title = f"[WE Learn] {course['name']} · {unit['name']}（{unit['done']}/{unit['total']}）"
            try:
                todo = todo_client.post(
                    "/api/v1/todo/items",
                    json={"text": title[:500], "tags": ["WE Learn", course["name"][:40]]},
                )
            except Exception as exc:  # noqa: BLE001
                failed.append({"unit": key, "error": f"{type(exc).__name__}: {exc}"})
                continue
            todo_id = str((todo or {}).get("id") or "")[:64]
            if not todo_id:
                failed.append({"unit": key, "error": "todo 返回里没有 id"})
                continue
            db.add(
                models.WelearnSynced(
                    unit_key=key, course_id=cid, title=unit["name"][:300],
                    todo_id=todo_id, synced_at=utcnow(),
                )
            )
            db.commit()
            created.append({"unit": key, "title": title[:120], "todo_id": todo_id})

    if created:
        try:
            event_bus.publish(
                "welearn.synced",
                {"count": len(created), "titles": [c["title"] for c in created[:10]]},
                source="welearn",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("welearn.synced 发布失败（已忽略）：%s", exc)

    result: dict[str, Any] = {
        "created": len(created),
        "skipped_known": skipped_known,
        "skipped_done": skipped_done,
        "failed": failed,
        "items": created,
        "courses": len(courses),
    }
    if not created and not failed:
        result["hint"] = "没有未完成单元可同步（或都已建过待办）"
    return result


__all__ = ["parse_units", "router"]
