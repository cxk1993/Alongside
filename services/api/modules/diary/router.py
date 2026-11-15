"""日记路由（薄壳：定位/视图端点，数据读写经 T15 的 HTTP API）。

★ 前缀由内核按 manifest.api.base 自动加，这里不要写 prefix=。
★ 本卡不直连 docs_* 表 —— 经 ISSUE-005 A 案 get_plugin_client 调 T15
  /api/v1/docs/...（requires 已声明，内核机器强制「声明即授权」）。
★ 本文件不要加 `from __future__ import annotations`（204 端点的坑）。
"""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi import Path as FPath
from typing import Annotated
from sqlmodel import Session

from core.deps import get_current_user, get_db, get_plugin_client
from core.security import User

from .schema import (
    DiaryCaptureOut,
    DiaryConsolidateIn,
    DiaryConsolidateOut,
    DiaryEntryOut,
    DiaryEntryUpdateIn,
    DiaryInboxOut,
    DiaryMonthOut,
    DiaryTodayOut,
    DiaryTodaySummaryOut,
)
from .service import DiaryService

router = APIRouter()

_MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"
with _MANIFEST_PATH.open(encoding="utf-8") as _f:
    _MANIFEST = json.load(_f)

DbDep = Session
UserDep = User

# diary 需要的能力（与 manifest.requires 一致，get_plugin_client 会校验）
_DOCS_CAPS = ["docs.node.read", "docs.node.write", "docs.search"]


class _ClientDocsAdapter:
    """把内核注入的 InternalHttpClient 适配成 DocsAdapter 协议（tree/create/patch/remove）。

    ISSUE-005 A 案平滑替换：原 _HttpxDocsAdapter（自签 token + 自管 httpx）
    换成内核注入的 client；DiaryService 的协议不变，零侵入。
    ★ T17 幂等修复：新增 remove（软删），供 ensure_diary_root 对账时清理重复根。
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def tree(self) -> list[dict]:
        return self._client.get("/api/v1/docs/nodes") or []

    def create(self, parent_id: str | None, kind: str, name: str, meta: dict | None = None) -> dict:
        return self._client.post(
            "/api/v1/docs/nodes",
            json={"parent_id": parent_id, "kind": kind, "name": name, "meta_json": meta},
        )

    def patch(self, node_id: str, body: dict) -> dict:
        return self._client.patch(f"/api/v1/docs/nodes/{node_id}", json=body)

    def remove(self, node_id: str) -> None:
        """软删节点（T15 回收站语义，可恢复）。"""
        self._client.delete(f"/api/v1/docs/nodes/{node_id}")


def _svc(request: Request, user: UserDep, tz: str = "Asia/Shanghai") -> DiaryService:
    """构造 DiaryService：用内核注入的内部调用 client 调 docs。"""
    client = get_plugin_client(request, _DOCS_CAPS)
    return DiaryService(_ClientDocsAdapter(client), tz=tz)


@router.get("/health")
def health() -> dict[str, bool]:
    """每个插件都必须有 health —— 内核据此判断"该能力是否可用"。"""
    return {"ok": True}


@router.get("/manifest")
def manifest() -> dict:
    """模块清单。"""
    return _MANIFEST


@router.get("/today", response_model=DiaryTodayOut)
def today(
    request: Request,
    db: DbDep = Depends(get_db),
    user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """今天（按 Asia/Shanghai）对应的日记节点。"""
    svc = _svc(request, user)
    d = svc.today()
    entry = svc.get_or_create_entry(d.isoformat())
    return {
        "date": d.isoformat(),
        "node_id": entry["node_id"],
        "path": entry["path"],
        "exists": entry["exists"],
    }


@router.get("/today-summary", response_model=DiaryTodaySummaryOut)
def today_summary(
    request: Request,
    db: DbDep = Depends(get_db),
    user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """今日日记摘要（U2 小日历聚合 · + 协调令63）。

    规范 v1 形状：{title, items:[{text,state}], link}
    BFF 透传 data（R-1 内核零业务），本模块只返回当日摘要数据。
    复用 /today 的 get_or_create_entry，加 marks/items 字段。
    """
    svc = _svc(request, user)
    d = svc.today()
    # ★ 门禁复核修复二（实跑）：不用 get_or_create_entry——GET 摘要端点
    #   不得有写副作用（会把"今日无日记"的库凭空建出节点）。只读判定当日存在性。
    exists = d.isoformat() in svc.month_days(d.year, d.month)
    return {
        "title": f"今日日记 {1 if exists else 0} 项",
        # ★ 门禁复核修复（实跑 500）：get_or_create_entry 无 name 键，
        #   旧代码 entry.get("name") → None 压 text: str 必炸。日期即日记节点名。
        "items": [{"text": d.isoformat(), "state": "info"}] if exists else [],
        "link": "/diary",
    }


@router.get("/entry", response_model=DiaryEntryOut)
def get_entry(
    request: Request,
    date: str | None = Query(None, description="日期 YYYY-MM-DD；缺省=今天"),
    db: DbDep = Depends(get_db),
    user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """定位某日日记（get-or-create 幂等）。"""
    return _svc(request, user).get_or_create_entry(date)


@router.post("/entry", response_model=DiaryEntryOut)
def ensure_entry(
    request: Request,
    date: str | None = Query(None, description="日期 YYYY-MM-DD；缺省=今天"),
    db: DbDep = Depends(get_db),
    user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """确保某日节点存在（get-or-create）。"""
    return _svc(request, user).get_or_create_entry(date)


@router.get("/month", response_model=DiaryMonthOut)
def month(
    request: Request,
    year: int = Query(..., description="年，如 2026"),
    month: int = Query(..., description="月，如 9"),
    db: DbDep = Depends(get_db),
    user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """该月已有日记的日期集合（月历高亮）。"""
    svc = _svc(request, user)
    days = svc.month_days(year, month)
    return {"year": year, "month": month, "days": days}


@router.get("/inbox", response_model=DiaryInboxOut)
def inbox(
    request: Request,
    db: DbDep = Depends(get_db),
    user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """列出收件箱条目。"""
    return {"items": _svc(request, user).inbox_items()}


@router.post("/capture", response_model=DiaryCaptureOut)
def capture(
    request: Request,
    db: DbDep = Depends(get_db),
    user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """随手记：在收件箱下新建一条。"""
    return _svc(request, user).capture()


@router.post("/consolidate", response_model=DiaryConsolidateOut)
def consolidate(
    request: Request,
    body: DiaryConsolidateIn,
    db: DbDep = Depends(get_db),
    user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """归纳：把收件箱条目移到目标日期月份下。"""
    return _svc(request, user).consolidate(body.node_id, body.date)

@router.patch("/entry/{node_id}", response_model=DiaryEntryOut)
def update_entry(
    node_id: Annotated[str, FPath(description="日记节点 id —— 从日记树里取")],
    request: Request,
    body: DiaryEntryUpdateIn,
    db: DbDep = Depends(get_db),
    user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """编辑日记：改标题 / 改日期（移动节点到新日期目录）。

    正文内容编辑走 docs 的 PUT /nodes/{id}/content（本薄壳不重复代理）。
    """
    svc = _svc(request, user)
    result = svc.update_entry(node_id, title=body.title, raw_date=body.date)
    return {
        "node_id": result["id"],
        "path": result.get("path", ""),
        "exists": True,
        "date": result.get("meta_json", {}).get("diary_date", ""),
    }


@router.delete("/entry/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(
    node_id: Annotated[str, FPath(description="日记节点 id —— 从日记树里取")],
    request: Request,
    db: DbDep = Depends(get_db),
    user: UserDep = Depends(get_current_user),
) -> None:
    """软删日记（docs 回收站语义，可恢复）。"""
    _svc(request, user).delete_entry(node_id)


# ── r7 · 日聚合能力（x.day.peek）：dashboard 环回聚合供数 ──
@router.get("/day-peek")
def day_peek(
    request: Request,
    date: str | None = Query(None, description="YYYY-MM-DD；缺省=今天"),
    user: UserDep = Depends(get_current_user),
) -> dict:
    """当日日记条目（只读，不做 get-or-create 写副作用）。"""
    svc = _svc(request, user)
    target = date or svc.today().isoformat()
    days = set(svc.month_days(int(target[:4]), int(target[5:7])))
    exists = target in days
    return {"items": [{"text": target, "state": "info"}] if exists else []}


@router.get("/day-dots")
def day_dots(
    request: Request,
    from_: str | None = Query(None, alias="from", description="起始日期"),
    to: str | None = Query(None, description="结束日期"),
    user: UserDep = Depends(get_current_user),
) -> dict:
    """区间内有日记的日期清单（逐月 month_days 并集后过滤）。只读。"""
    svc = _svc(request, user)
    today_iso = svc.today().isoformat()
    start = from_ or today_iso[:8] + "01"
    end = to or today_iso
    y, m = int(start[:4]), int(start[5:7])
    dates: set[str] = set()
    while (y, m) <= (int(end[:4]), int(end[5:7])):
        dates.update(svc.month_days(y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return {"dates": sorted(d for d in dates if start <= d <= end)}
