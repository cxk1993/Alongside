"""雨课堂路由：只读同步课程、课堂活动、公告与考核，公告/临期考核可转待办。

════════════════════════════════════════════════════════════════════
★ 端点已用真实会话校准（2026-11-15，changjiang.yuketang.cn 区域）：
    /v2/api/web/userinfo
    /v2/api/web/courses/list?identity=2
    /v2/api/web/classrooms/{cid}?role=5
    /v2/api/web/logs/learn/{cid}?actype=-1&page=0&offset=N&sort=-1
    /v/discussion/v2/announcements/?cid=&limit=&offset=0&type=9
    /mooc-api/v1/lms/learn/course/chapter?cid=&sign=&term=latest&uv_id=

★ 边界（写死在代码里，不是文档里）：
  · 只发 GET，只读；没有「交作业 / 签到 / 答题」任何写回雨课堂的路径。
  · 会话来自用户自己在浏览器里复制的那一个 cookie，插件不碰账号密码。
  · 只允许 *.yuketang.cn / *.rainclassroom.com / *.xuetangx.com（client.py 校验）。

★ 为什么没有窗口（entry 为空）：第三方插件 UI 拿不到宿主 token（B3）；
  纯 API 插件是 sdu-feeds 已验证的形态。

★ 模块加载（r8 · N1）：本插件由内核以 `plugin_thirdparty_yuketang` 包注册
  （core/plugins/discover.py），models/client 走相对导入，卸载可整体清理。
  每端点必须 Depends(get_current_user)。
════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import ast
import json
import logging
import re
from datetime import datetime, timedelta, timezone
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

_CST = timezone(timedelta(hours=8))
_MAX_ITEMS = 100
#: 考核进待办的时间窗：只同步 30 天内截止的，避免把整学期灌进去
_DUE_WINDOW = timedelta(days=30)
#: 一次同步最多新建多少条（防止 11 门课 × 10 条把待办刷屏）
_MAX_CREATE = 60


# ════════════════════════════ 模块加载 ════════════════════════════
# ★ r8（N1）：不再手写 sys.modules 加载器。内核已把本插件的 `api/` 注册成包
#   `plugin_thirdparty_yuketang`（core/plugins/discover.py），所以直接相对导入即可，
#   兄弟模块与路由共用同一份模块对象 ⇒ 卸载时 unload_thirdparty_modules() 能一并清掉，
#   不会出现「新路由配旧表模型」。
from . import client as client_mod  # noqa: E402
from . import models  # noqa: E402


def _models() -> Any:
    return models


def _client_mod() -> Any:
    return client_mod


# ════════════════════════════ 入参 ════════════════════════════
class SessionIn(BaseModel):
    sessionid: str = Field(
        ..., min_length=8, max_length=500,
        description="浏览器 DevTools → Application → Cookies 里的 sessionid 值",
    )
    base_url: str = Field(
        default="https://www.yuketang.cn", max_length=300,
        description="标准版用默认值；学校私有云填自己的域名（如 https://changjiang.yuketang.cn）",
    )


class SyncIn(BaseModel):
    classroom_id: str | None = Field(default=None, description="只同步某个班；不传则全部课程")
    include_notices: bool = Field(default=True, description="公告是否也建成待办")
    max_items: int = Field(default=10, ge=1, le=_MAX_ITEMS, description="每门课最多取多少条")


# ════════════════════════════ 会话存取 ════════════════════════════
def _session_row(db: _DB) -> Any:
    models = _models()
    return db.exec(select(models.YuketangSession)).first()


def _require_session(db: _DB) -> Any:
    row = _session_row(db)
    if row is None:
        raise NotFoundError("还没配置雨课堂会话：先 POST /session 保存 sessionid")
    return row


def _mask(value: str) -> str:
    # ★ r8（N7）：既然打码就打全。原先留前 4 + 后 4 位对单用户毫无用处，
    #   却把会话指纹写进了响应体（也会进前端日志）。与 welearn 的 `_mask` 统一口径。
    return f"len={len(value)} 值已隐藏"


# ════════════════════════════ 归一化（平台改版只改这里） ════════════════════════════
def _data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _first(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def _truthy(value: Any) -> bool:
    """上游可能给 True / 1 / "1" / "true" / "0"。

    ★ 不能用裸 `bool(...)`：`bool("0")` 与 `bool("false")` 都是 True，
      会把「未签到 / 未完成」写成已完成，且静默无报错。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "t")
    return bool(value)


def _to_iso(value: Any) -> str | None:
    """秒/毫秒时间戳或日期串 → 带时区 ISO8601。"""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000.0 if float(value) > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(_CST).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=_CST)).isoformat()
    return None


def _parse_dt(value: Any) -> datetime | None:
    iso = _to_iso(value)
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def _normalize_course(raw: Any) -> dict[str, Any] | None:
    """courses/list 的一条：班级名在 name，课程名在 course.name。"""
    if not isinstance(raw, dict):
        return None
    classroom_id = _first(raw, ("classroom_id", "classroomId", "id"))
    if classroom_id in (None, ""):
        return None
    course = raw.get("course") if isinstance(raw.get("course"), dict) else {}
    teacher = raw.get("teacher") if isinstance(raw.get("teacher"), dict) else {}
    return {
        "classroom_id": str(classroom_id),
        "course_id": str(_first(raw, ("course_id", "courseId")) or _first(course, ("id",)) or ""),
        "name": str(_first(raw, ("name", "course_name")) or "未命名班级"),
        "course_name": str(_first(course, ("name", "title")) or ""),
        "teacher": str(_first(teacher, ("name", "teacher_name")) or ""),
        "term": str(_first(raw, ("term",)) or ""),
    }


def _normalize_activity(raw: Any) -> dict[str, Any] | None:
    """logs/learn 的一条：type=14 上课记录；title 一定有。"""
    if not isinstance(raw, dict):
        return None
    activity_id = _first(raw, ("id", "activity_id"))
    title = _first(raw, ("title", "name"))
    if activity_id in (None, "") or not title:
        return None
    return {
        "activity_id": str(activity_id),
        "title": str(title),
        "type": str(_first(raw, ("type", "actype")) or ""),
        "starts_at": _to_iso(_first(raw, ("create_time", "start_time"))),
        "is_finished": _truthy(raw.get("is_finished")),
        "attended": _truthy(raw.get("attend_status")),
    }


def _clean_content(value: Any) -> str:
    """公告正文的三种形态：dict（实测）、JSON/Python repr 字符串、裸 HTML。"""
    if isinstance(value, dict):
        text = str(value.get("app_text") or value.get("text") or "")
    elif isinstance(value, str):
        text = value.strip()
        if text[:1] in "{[":
            for parser in (json.loads, ast.literal_eval):
                try:
                    parsed = parser(text)
                except Exception:  # noqa: BLE001 —— 远端字符串可能有深嵌套(RecursionError)/超长(MemoryError)
                    continue
                if isinstance(parsed, dict):
                    text = str(parsed.get("app_text") or parsed.get("text") or "")
                    break
    else:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


def _normalize_notice(raw: Any) -> dict[str, Any] | None:
    """announcements 的一条：标题在 topic_name，正文在 content（嵌套字符串）。"""
    if not isinstance(raw, dict):
        return None
    ids = raw.get("ids") if isinstance(raw.get("ids"), list) else []
    notice_id = _first(raw, ("id", "notice_id")) or (ids[0] if ids else None)
    title = _first(raw, ("topic_name", "title", "name"))
    if notice_id in (None, "") or not title:
        return None
    return {
        "notice_id": str(notice_id),
        "title": str(title),
        "content": _clean_content(_first(raw, ("content",))),
        "published_at": _to_iso(_first(raw, ("app_publish_time", "create_time"))),
        "publisher": str(_first(raw, ("publisher_name", "creator_name")) or ""),
    }


def _normalize_leaf(raw: Any) -> dict[str, Any] | None:
    """章节树的一条：score_deadline 是考核截止时间（毫秒）。"""
    if not isinstance(raw, dict):
        return None
    leaf_id = _first(raw, ("id", "leaf_id"))
    name = _first(raw, ("name", "title"))
    if leaf_id in (None, "") or not name:
        return None
    deadline = _first(raw, ("score_deadline", "end_time"))
    return {
        "leaf_id": str(leaf_id),
        "name": str(name),
        "leaf_type": str(_first(raw, ("leaf_type",)) or ""),
        "deadline": _to_iso(deadline),
        "starts_at": _to_iso(_first(raw, ("start_time",))),
        "is_score": _truthy(raw.get("is_score")),
        "is_locked": _truthy(raw.get("is_locked")),
    }


def _flatten_leaves(payload: Any) -> list[dict[str, Any]]:
    chapters = (_data(payload) or {}).get("course_chapter") if isinstance(_data(payload), dict) else None
    out: list[dict[str, Any]] = []
    for chapter in chapters or []:
        for leaf in (chapter or {}).get("section_leaf_list") or []:
            item = _normalize_leaf(leaf)
            if item:
                out.append(item)
    return out


# ════════════════════════════ 取数 ════════════════════════════
def _fetch_courses(client: Any) -> list[dict[str, Any]]:
    payload = _data(client.courses()) or {}
    rows = payload.get("list") if isinstance(payload, dict) else None
    return [c for c in (_normalize_course(x) for x in rows or []) if c]


def _fetch_activities(client: Any, cid: str, limit: int) -> list[dict[str, Any]]:
    payload = _data(client.activities(cid, limit=limit)) or {}
    rows = payload.get("activities") if isinstance(payload, dict) else None
    return [a for a in (_normalize_activity(x) for x in rows or []) if a]


def _fetch_notices(client: Any, cid: str, limit: int) -> list[dict[str, Any]]:
    payload = _data(client.notices(cid, limit=limit)) or {}
    rows = payload.get("results") if isinstance(payload, dict) else None
    return [n for n in (_normalize_notice(x) for x in rows or []) if n]


def _fetch_leaves(client: Any, cid: str) -> list[dict[str, Any]]:
    info = _data(client.classroom(cid)) or {}
    sign = str((info or {}).get("course_sign") or "")
    uv_id = str((info or {}).get("uv_id") or "")
    if not sign or not uv_id:
        return []
    return _flatten_leaves(client.chapter(cid, sign, uv_id))


def _due_soon(leaf: dict[str, Any]) -> bool:
    deadline = _parse_dt(leaf.get("deadline"))
    if deadline is None:
        return False
    now = datetime.now(timezone.utc)
    return now <= deadline <= now + _DUE_WINDOW


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
    """保存并当场验证会话；验证不过不落库。"""
    models = _models()
    client_mod = _client_mod()

    try:
        # 构造也可能抛 shape 错（域名不在白名单 / 非 https），必须在 try 内 →
        # 否则会掉进兜底 500「服务器内部错误」，而 base_url 填错恰恰是最常见的首个坑。
        client = client_mod.YuketangClient(body.sessionid, body.base_url)
        info = client.userinfo()
    except client_mod.YuketangError as exc:
        raise UnauthorizedError(
            f"会话校验失败：{exc}（学校私有云请把 base_url 填成自己的域名，如 changjiang.yuketang.cn）"
        ) from exc

    rows = _data(info) or []
    first = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else {})
    name = str(_first(first, ("name", "user_name", "nickname")) or "")[:120]

    row = _session_row(db)
    if row is None:
        row = models.YuketangSession(session_id=body.sessionid, base_url=body.base_url)
    row.session_id = body.sessionid
    row.base_url = body.base_url
    row.user_name = name
    row.last_verified_at = utcnow()
    row.last_error = ""
    db.add(row)
    db.commit()
    return {"ok": True, "user_name": name, "base_url": row.base_url}


@router.delete("/session")
def delete_session(
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, bool]:
    models = _models()
    for row in db.exec(select(models.YuketangSession)).all():
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
        return {"configured": False, "hint": "POST /session 保存 sessionid"}
    return {
        "configured": True,
        "base_url": row.base_url,
        "session": _mask(row.session_id),
        "user_name": row.user_name,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "last_error": row.last_error,
    }


@router.get("/courses")
def list_courses(
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _require_session(db)
    client_mod = _client_mod()
    try:
        courses = _fetch_courses(client_mod.YuketangClient(row.session_id, row.base_url))
    except client_mod.YuketangError as exc:
        if exc.kind == "auth":
            raise UnauthorizedError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    return {"count": len(courses), "courses": courses}


@router.get("/activities")
def list_activities(
    classroom_id: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=_MAX_ITEMS),
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _require_session(db)
    client_mod = _client_mod()
    try:
        items = _fetch_activities(
            client_mod.YuketangClient(row.session_id, row.base_url), classroom_id, limit
        )
    except client_mod.YuketangError as exc:
        raise ValidationError(str(exc)) from exc
    return {"classroom_id": classroom_id, "count": len(items), "items": items}


@router.get("/notices")
def list_notices(
    classroom_id: str = Query(..., min_length=1),
    limit: int = Query(default=10, ge=1, le=_MAX_ITEMS),
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _require_session(db)
    client_mod = _client_mod()
    try:
        items = _fetch_notices(
            client_mod.YuketangClient(row.session_id, row.base_url), classroom_id, limit
        )
    except client_mod.YuketangError as exc:
        raise ValidationError(str(exc)) from exc
    return {"classroom_id": classroom_id, "count": len(items), "items": items}


@router.get("/assignments")
def list_assignments(
    classroom_id: str = Query(..., min_length=1),
    days: int = Query(default=30, ge=1, le=180, description="只看这么多天内截止的"),
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """考核/作业截止时间（来自课程章节树，不是另建一份真相）。"""
    row = _require_session(db)
    client_mod = _client_mod()
    try:
        leaves = _fetch_leaves(
            client_mod.YuketangClient(row.session_id, row.base_url), classroom_id
        )
    except client_mod.YuketangError as exc:
        raise ValidationError(str(exc)) from exc

    now = datetime.now(timezone.utc)
    selected = []
    for leaf in leaves:
        deadline = _parse_dt(leaf.get("deadline"))
        if deadline is not None and now <= deadline <= now + timedelta(days=days):
            selected.append(leaf)
    selected.sort(key=lambda x: x.get("deadline") or "")
    return {"classroom_id": classroom_id, "count": len(selected), "items": selected}


@router.post("/sync")
def sync_to_todo(
    body: SyncIn,
    request: Request,
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """把公告与临期考核写成待办（幂等：同一条只建一次）。

    跨插件调用走 get_plugin_client；能力写在 manifest.optionalDependencies，
    没声明会 403（ADR-0002 声明即授权）。todo 不在时本端点 403，其余功能照常。
    """
    models = _models()
    client_mod = _client_mod()
    row = _require_session(db)
    client = client_mod.YuketangClient(row.session_id, row.base_url)

    try:
        courses = _fetch_courses(client)
    except client_mod.YuketangError as exc:
        if exc.kind == "auth":
            raise UnauthorizedError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc

    if body.classroom_id:
        courses = [c for c in courses if c["classroom_id"] == body.classroom_id]
        if not courses:
            raise NotFoundError(f"课程里没有 classroom_id={body.classroom_id}")

    todo_client = get_plugin_client(request, ["todo.item.write"])

    created: list[dict[str, Any]] = []
    skipped_known = 0
    failed: list[dict[str, str]] = []

    def already(key: str) -> bool:
        return db.exec(
            select(models.YuketangSynced).where(models.YuketangSynced.activity_key == key)
        ).first() is not None

    def push(key: str, title: str, cid: str, due: str | None, tag: str) -> None:
        payload: dict[str, Any] = {"text": f"[雨课堂] {title}"[:500], "tags": ["雨课堂", tag]}
        if due:
            payload["due_at"] = due
        try:
            todo = todo_client.post("/api/v1/todo/items", json=payload)
        except Exception as exc:  # noqa: BLE001 —— 单条失败不拖垮整批
            failed.append({"item": key, "error": f"{type(exc).__name__}: {exc}"})
            return
        todo_id = str((todo or {}).get("id") or "")[:64]
        if not todo_id:
            failed.append({"item": key, "error": "todo 返回里没有 id"})
            return
        db.add(
            models.YuketangSynced(
                activity_key=key, classroom_id=cid, title=title[:300],
                todo_id=todo_id, synced_at=utcnow(),
            )
        )
        db.commit()
        created.append({"item": key, "title": title[:120], "todo_id": todo_id})

    for course in courses:
        if len(created) >= _MAX_CREATE:
            break
        cid = course["classroom_id"]
        label = f"{course.get('course_name') or course['name']}（{course['name']}）"

        if body.include_notices:
            # ★ 公告是增强项：单门课公告接口 4xx（改版/权限/限流）只记 failed，
            #   不能掀翻整次 sync —— 否则同批课程全都拿不到结果摘要。
            try:
                notices = _fetch_notices(client, cid, body.max_items)
            except client_mod.YuketangError as exc:
                failed.append({"classroom_id": cid, "stage": "notices", "error": str(exc)})
                notices = []
            for notice in notices:
                key = f"{cid}:notice:{notice['notice_id']}"
                if already(key):
                    skipped_known += 1
                    continue
                push(key, f"{label} 公告 · {notice['title']}", cid, None, course["name"])
                if len(created) >= _MAX_CREATE:
                    break

        try:
            leaves = _fetch_leaves(client, cid)
        except client_mod.YuketangError as exc:
            failed.append({"classroom_id": cid, "error": str(exc)})
            continue
        for leaf in leaves:
            if len(created) >= _MAX_CREATE:
                break
            if not _due_soon(leaf):
                continue
            key = f"{cid}:leaf:{leaf['leaf_id']}"
            if already(key):
                skipped_known += 1
                continue
            push(key, f"{label} · {leaf['name']}", cid, leaf.get("deadline"), course["name"])

    _publish(created)

    result: dict[str, Any] = {
        "created": len(created),
        "skipped_known": skipped_known,
        "failed": failed,
        "items": created,
        "courses": len(courses),
    }
    if not created and not failed:
        result["hint"] = (
            "这门课目前没有新公告、也没有 30 天内截止的考核。"
            "公告默认会同步；如果只想要考核，传 include_notices=false。"
        )
    return result


def _publish(created: list[dict[str, Any]]) -> None:
    if not created:
        return
    try:
        event_bus.publish(
            "yuketang.synced",
            {"count": len(created), "titles": [c["title"] for c in created[:10]]},
            source="yuketang",
        )
    except Exception as exc:  # noqa: BLE001 —— 事件是增强，不是同步的前提
        log.warning("yuketang.synced 发布失败（已忽略）：%s", exc)


__all__ = ["router"]
