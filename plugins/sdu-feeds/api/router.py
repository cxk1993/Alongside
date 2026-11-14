"""sdu-feeds 路由：把山大公开站点的通知抓成统一条目。

════════════════════════════════════════════════════════════════════
★ 只抓内置站点：不提供自定义源，避免“用户输入的 URL 由服务器去抓”的 SSRF 面。

★ 这个插件为什么"没有界面"：
  前端内核只从宿主 `apps/web/src/apps/*/index.tsx` 发现入口（构建期 glob），
  第三方插件的 `web/` 目录不会被加载。所以 P0 故意做成**纯 API 插件**：
  抓来的条目通过 `/api/v1/sdu-feeds/items` 暴露，需要时一键转成待办
  （`todo.item.write`，optionalDependency，缺失时该端点报 403 而不是整插件挂掉）。

★ 第三方插件的两条硬约束（照 countdown 抄的，别踩第二遍）：
  1. 不能用相对导入 —— 内核按文件路径加载，`from .models import X` 会 ImportError；
     因此 models.py / parse.py 都走 `_load_by_path()`；
  2. 每个端点都必须显式 `Depends(get_current_user)` —— 内核挂载时只注入 db_scope，
     不会替你加鉴权（pi-agent 就是全端点裸奔的反例）。
════════════════════════════════════════════════════════════════════
"""

import importlib.util
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi import Path as FPath
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from core.deps import get_current_user, get_db, get_plugin_client
from core.errors import NotFoundError, ValidationError
from core.events import event_bus
from core.security import User
from db.base import utcnow

_HERE = Path(__file__).resolve().parent
_MODEL_KEY = "sdu_feeds_models"
_PARSE_KEY = "sdu_feeds_parse"
_MANIFEST = json.loads((_HERE.parent / "manifest.json").read_text(encoding="utf-8"))

_DB = Session  # 与 countdown 同写法：本插件内 db 依赖的类型别名
log = logging.getLogger(__name__)

router = APIRouter()

#: 抓取时用的 UA：表明身份、方便站点管理员在日志里认出这是个人工具
_USER_AGENT = "Alongside-sdu-feeds/0.1 (personal, low-rate; contact: local user)"
#: 单页最多取多少条（防止站点分页异常时一次灌进上千条）
_MAX_LIMIT = 100
#: 单页 HTML 上限（防止超大响应把进程撑爆）
_MAX_HTML_BYTES = 3_000_000
#: 只允许抓 *.sdu.edu.cn（防 SSRF 打内网/云元数据）；确需别的域名时显式覆盖
_ALLOWED_SUFFIX = (
    os.environ.get("SDU_FEEDS_ALLOWED_HOST_SUFFIX", ".sdu.edu.cn") or ""
).strip().lower()
#: 单次同步的默认上限
_DEFAULT_LIMIT = 30

_DATE_MODES = ("auto", "ymd", "ym+d", "md+y")
_STATES = ("unread", "read", "archived")


class SyncError(Exception):
    """抓取/解析失败（不抛给 HTTP，写进 source.last_error 并计入报告）。"""


# ════════════════════════════ 按路径加载同目录模块 ════════════════════════════
def _load_by_path(key: str, filename: str) -> Any:
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    path = _HERE / filename
    spec = importlib.util.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"sdu-feeds 无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _models() -> Any:
    return _load_by_path(_MODEL_KEY, "models.py")


def _parser() -> Any:
    return _load_by_path(_PARSE_KEY, "parse.py")


# ════════════════════════════ 内置预设（已用真实页面验证） ════════════════════════════
#: 每个预设的关键不是 URL，而是 `link_pattern` + `date_mode` 这一对：
#: 前者把导航栏排除掉，后者告诉解析器"这个站的日期长什么样"。
PRESETS: tuple[dict[str, str], ...] = (
    {
        "key": "cs-announcements",
        "name": "计算机学院 · 学院公告",
        "base_url": "https://www.cs.sdu.edu.cn/",
        "list_url": "https://www.cs.sdu.edu.cn/xygg.htm",
        "link_pattern": r"info/\d+/\d+\.htm",
        "date_mode": "auto",
    },
    {
        "key": "cs-talks",
        "name": "计算机学院 · 学术报告",
        "base_url": "https://www.cs.sdu.edu.cn/",
        "list_url": "https://www.cs.sdu.edu.cn/xsbg.htm",
        "link_pattern": r"info/\d+/\d+\.htm",
        "date_mode": "auto",
    },
    {
        "key": "bkjx-notices",
        "name": "本科生院 · 通知公告",
        "base_url": "https://www.bkjx.sdu.edu.cn/",
        "list_url": "https://www.bkjx.sdu.edu.cn/index/tzgg.htm",
        "link_pattern": r"content\.jsp\?urltype=news",
        "date_mode": "md+y",
    },
    {
        "key": "youth-notices",
        "name": "青春山大 · 重要通知",
        "base_url": "https://www.youth.sdu.edu.cn/",
        "list_url": "https://www.youth.sdu.edu.cn/list.jsp?urltype=tree.TreeTempUrl&wbtreeid=1004",
        "link_pattern": r"tz_content\.jsp",
        "date_mode": "ym+d",
    },
)


# ════════════════════════════════ 出入参 ════════════════════════════════
#: 站点只来自内置预设（PRESETS）：不提供“自定义源”输入口。
#: 这样既不会把任意 URL 交给服务器去抓（SSRF），manifest 的 net 权限声明也和实际一致。
class SourceOut(BaseModel):
    id: str
    key: str
    name: str
    base_url: str
    list_url: str
    link_pattern: str
    date_mode: str
    enabled: bool
    last_synced_at: datetime | None
    last_error: str
    last_new_count: int


class ItemOut(BaseModel):
    id: str
    source_id: str
    url: str
    title: str
    published_on: date | None = Field(default=None, description="发布日期；站点没给就是 null")
    state: str
    todo_id: str | None


class ItemStateIn(BaseModel):
    state: str = Field(..., description="unread | read | archived")


class PromoteIn(BaseModel):
    due_at: datetime | None = Field(
        default=None,
        description="可选截止时间（带时区 ISO8601）；不传则建一条无截止的待办",
    )


class SyncIn(BaseModel):
    source_id: str | None = Field(default=None, description="只同步某个源；留空=全部启用的源")
    limit: int = Field(
        default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT, description="单源单次最多取多少条"
    )


class SyncResult(BaseModel):
    source_id: str
    key: str
    name: str
    status: str = Field(description="ok | blocked | error")
    fetched: int = 0
    parsed: int = 0
    new: int = 0
    updated: int = 0
    error: str = ""


class SyncReport(BaseModel):
    results: list[SyncResult]
    new_total: int


# ════════════════════════════════ 抓取与入库 ════════════════════════════════
def _http_get(url: str) -> str:
    """抓一个列表页。

    ★ 安全：URL 先过 `_validate_url`（只允许 *.sdu.edu.cn）；重定向**手动跟**，
      每一跳都重新校验，避免“合法站点 302 到内网/云元数据”把域名白名单绕过；
      响应大小有上限，避免超大字面把进程撑爆。
    """
    current = _validate_url(url)
    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"}
    for _hop in range(4):
        try:
            with (
                httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client,
                client.stream("GET", current, headers=headers) as resp,
            ):
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location") or ""
                        if not location:
                            raise SyncError(f"HTTP {resp.status_code} 缺少 Location：{current}")
                        current = _validate_url(urljoin(current, location))
                        continue
                    if resp.status_code >= 400:
                        raise SyncError(f"HTTP {resp.status_code}：{current}")
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > _MAX_HTML_BYTES:
                            raise SyncError("页面超过 3MB 上限，已放弃抓取")
                        chunks.append(chunk)
                    text = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
                    final_url = str(resp.url)
                    break
        except httpx.HTTPError as exc:
            raise SyncError(f"网络请求失败：{type(exc).__name__}: {exc}") from exc
    else:
        raise SyncError("重定向次数过多（>4）")

    # 青春山大等站点对校外 IP 会跳到权限页 —— 必须报"去连校园网"，而不是"解析到 0 条"
    if "ipauth" in final_url or "没有访问当前栏目的权限" in text:
        raise SyncError("站点要求校园网/VPN（被 IP 权限页拦截）")
    return text


def _publish_new(source: Any, item: Any) -> None:
    """发一条事件；总线没登记发布者时只记日志，不让同步失败。"""
    try:
        event_bus.publish(
            "sdu.feed.item.new",
            {
                "source": source.key,
                "source_id": source.id,
                "item_id": item.id,
                "title": item.title,
                "url": item.url,
                "published_on": item.published_on.isoformat() if item.published_on else None,
            },
            source="sdu-feeds",
        )
    except Exception as exc:  # noqa: BLE001 —— 事件是副作用，不能反过来打断抓取
        log.warning("sdu-feeds 事件发布失败：%s", exc)


def _sync_one(db: Session, source: Any, limit: int) -> tuple[SyncResult, list[Any]]:
    """抓一个源。返回（报告, 新条目列表）。异常只进报告，不向上抛。"""
    models = _models()
    try:
        html = _http_get(source.list_url)
        parsed = _parser().parse_list(
            html,
            source.base_url,
            source.link_pattern,
            date_mode=source.date_mode,
            limit=limit,
        )
    except SyncError as exc:
        source.last_synced_at = utcnow()
        source.last_error = str(exc)[:500]
        source.last_new_count = 0
        db.add(source)
        db.commit()
        status = "blocked" if "校园网" in str(exc) else "error"
        return (
            SyncResult(
                source_id=source.id, key=source.key, name=source.name,
                status=status, error=str(exc)[:500],
            ),
            [],
        )
    except Exception as exc:  # noqa: BLE001 —— 解析器炸了也不能拖垮别的源
        source.last_error = f"{type(exc).__name__}: {exc}"[:500]
        source.last_synced_at = utcnow()
        db.add(source)
        db.commit()
        return (
            SyncResult(
                source_id=source.id, key=source.key, name=source.name,
                status="error", error=source.last_error,
            ),
            [],
        )

    new_rows: list[Any] = []
    updated = 0
    for entry in parsed:
        row = db.exec(
            select(models.SduFeedItem).where(
                models.SduFeedItem.source_id == source.id,
                models.SduFeedItem.url == entry.url,
            )
        ).first()
        if row is not None:
            changed = False
            if entry.title and row.title != entry.title:
                row.title = entry.title
                changed = True
            if entry.published_on and row.published_on != entry.published_on:
                row.published_on = entry.published_on
                changed = True
            if changed:
                db.add(row)
                updated += 1
            continue
        row = models.SduFeedItem(
            source_id=source.id,
            url=entry.url,
            title=entry.title,
            published_on=entry.published_on,
        )
        db.add(row)
        new_rows.append(row)

    source.last_synced_at = utcnow()
    source.last_error = ""
    source.last_new_count = len(new_rows)
    db.add(source)
    db.commit()

    for row in new_rows:
        db.refresh(row)
        _publish_new(source, row)

    return (
        SyncResult(
            source_id=source.id, key=source.key, name=source.name, status="ok",
            fetched=len(parsed), parsed=len(parsed), new=len(new_rows), updated=updated,
        ),
        new_rows,
    )


def _validate_url(url: str) -> str:
    """只允许 http(s) + *.sdu.edu.cn；拒绝内网/回环/伪装地址（防 SSRF）。"""
    raw = (url or "").strip()
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValidationError("list_url / base_url 必须是 http(s) 绝对地址")
    if not parts.hostname:
        raise ValidationError("list_url / base_url 缺少主机名")
    if parts.username or parts.password:
        raise ValidationError("list_url / base_url 不允许带用户名/密码")
    host = parts.hostname.lower().rstrip(".")
    allowed_root = _ALLOWED_SUFFIX.lstrip(".")
    if _ALLOWED_SUFFIX and not (host == allowed_root or host.endswith(_ALLOWED_SUFFIX)):
        raise ValidationError(f"只允许抓取 {_ALLOWED_SUFFIX} 的站点，拒绝：{host}")
    return raw


#: ORM → dict。不用 `from_attributes` 自动序列化，与 countdown 的 `to_out()` 同口径：
#: 出参字段就是协议，显式写出来才能在改模型时看到「这个字段被谁用了」。
def _source_out(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "key": row.key,
        "name": row.name,
        "base_url": row.base_url,
        "list_url": row.list_url,
        "link_pattern": row.link_pattern,
        "date_mode": row.date_mode,
        "enabled": row.enabled,
        "last_synced_at": row.last_synced_at,
        "last_error": row.last_error or "",
        "last_new_count": row.last_new_count or 0,
    }


def _item_out(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_id": row.source_id,
        "url": row.url,
        "title": row.title,
        "published_on": row.published_on,
        "state": row.state,
        "todo_id": row.todo_id,
    }


# ════════════════════════════════ 端点 ════════════════════════════════
@router.get("/health")
def health() -> dict[str, bool]:
    """内核据此判断「该能力是否可用」。"""
    return {"ok": True}


@router.get("/manifest")
def manifest() -> dict[str, Any]:
    """回显 manifest 原文，便于前端/调试核对。"""
    return _MANIFEST


@router.get("/sources", response_model=list[SourceOut])
def list_sources(
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[Any]:
    models = _models()
    rows = db.exec(select(models.SduFeedSource).order_by(models.SduFeedSource.key)).all()
    return [_source_out(row) for row in rows]


@router.post("/sources/seed")
def seed_sources(
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """把内置预设补齐（幂等：已存在的 key 跳过）。"""
    models = _models()
    created: list[str] = []
    for preset in PRESETS:
        exists = db.exec(
            select(models.SduFeedSource).where(models.SduFeedSource.key == preset["key"])
        ).first()
        if exists is not None:
            continue
        db.add(models.SduFeedSource(**preset))
        created.append(preset["key"])
    db.commit()
    total = len(db.exec(select(models.SduFeedSource)).all())
    return {"created": created, "count": total}


@router.post("/sync", response_model=SyncReport)
def sync(
    body: SyncIn | None = None,
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Any:
    """抓取内置预设源（可单源、可全量）。失败的源写进 last_error，不影响其他源。"""
    models = _models()
    preset_keys = {item["key"] for item in PRESETS}
    limit = (body.limit if body else _DEFAULT_LIMIT) or _DEFAULT_LIMIT
    if body is not None and body.source_id:
        source = db.get(models.SduFeedSource, body.source_id)
        if source is None:
            raise NotFoundError(f"源不存在：{body.source_id}")
        if source.key not in preset_keys:
            raise ValidationError("只允许同步内置预设源（自定义源功能已移除）")
        targets = [source]
    else:
        targets = [
            row
            for row in db.exec(
                select(models.SduFeedSource).where(models.SduFeedSource.enabled == True)  # noqa: E712
            ).all()
            if row.key in preset_keys
        ]
    if not targets:
        raise ValidationError("没有可同步的源：先 POST /sources/seed")

    results: list[SyncResult] = []
    for source in targets:
        result, _rows = _sync_one(db, source, min(limit, _MAX_LIMIT))
        results.append(result)
    return SyncReport(results=results, new_total=sum(r.new for r in results))


@router.get("/items", response_model=list[ItemOut])
def list_items(
    source_id: str | None = Query(default=None, description="只看某个源"),
    state: str | None = Query(default=None, description="unread | read | archived"),
    limit: int = Query(default=50, ge=1, le=200),
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[Any]:
    models = _models()
    stmt = select(models.SduFeedItem)
    if source_id:
        stmt = stmt.where(models.SduFeedItem.source_id == source_id)
    if state:
        if state not in _STATES:
            raise ValidationError(f"state 只能是 {' / '.join(_STATES)}")
        stmt = stmt.where(models.SduFeedItem.state == state)
    stmt = stmt.order_by(
        models.SduFeedItem.published_on.desc(), models.SduFeedItem.created_at.desc()
    ).limit(limit)
    return [_item_out(row) for row in db.exec(stmt).all()]


@router.post("/items/{item_id}/state", response_model=ItemOut)
def set_item_state(
    item_id: str = FPath(description="条目 id"),
    body: ItemStateIn = ...,
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Any:
    models = _models()
    if body.state not in _STATES:
        raise ValidationError(f"state 只能是 {' / '.join(_STATES)}")
    item = db.get(models.SduFeedItem, item_id)
    if item is None:
        raise NotFoundError(f"条目不存在：{item_id}")
    item.state = body.state
    db.add(item)
    db.commit()
    db.refresh(item)
    return _item_out(item)


@router.post("/items/{item_id}/promote")
def promote_item(
    item_id: str,
    request: Request,
    body: PromoteIn | None = None,
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """把一条通知转成待办（幂等：转过就直接回旧的 todo_id）。

    跨插件调用走 `get_plugin_client`，能力写在 manifest.optionalDependencies 里 ——
    没声明能力会 403（ADR-0002「声明即授权」），这是内核唯一真正执行的权限点。
    """
    models = _models()
    item = db.get(models.SduFeedItem, item_id)
    if item is None:
        raise NotFoundError(f"条目不存在：{item_id}")
    if item.todo_id:
        return {"item_id": item.id, "todo_id": item.todo_id, "created": False}

    source = db.get(models.SduFeedSource, item.source_id)
    prefix = f"[{source.name}] " if source is not None else ""
    payload: dict[str, Any] = {
        "text": f"{prefix}{item.title}"[:500],
        "tags": ["山大", source.key if source is not None else "sdu-feeds"],
    }
    if body is not None and body.due_at is not None:
        if body.due_at.tzinfo is None:
            raise ValidationError("due_at 必须带时区（如 2026-11-20T23:59:00+08:00）")
        payload["due_at"] = body.due_at.isoformat()

    client = get_plugin_client(request, ["todo.item.write"])
    created = client.post("/api/v1/todo/items", json=payload)
    todo_id = str((created or {}).get("id") or "")[:64] or None
    if not todo_id:
        raise ValidationError("todo 返回里没有 id，无法回填")
    item.todo_id = todo_id
    item.state = "read"
    db.add(item)
    db.commit()
    return {"item_id": item.id, "todo_id": todo_id, "created": True}


@router.get("/stats")
def stats(
    db: _DB = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    models = _models()
    rows = db.exec(
        select(models.SduFeedItem.source_id, func.count())  # type: ignore[arg-type]
        .group_by(models.SduFeedItem.source_id)
    ).all()
    by_source = {row[0]: int(row[1]) for row in rows}
    total = sum(by_source.values())
    return {"total": total, "by_source": by_source}


def _origin(url: str) -> str:
    """从列表页 URL 取站点根（作为相对链接基准）。"""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/"
