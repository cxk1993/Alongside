"""插件路由：纪念日倒数（countdown）· V1 = 纯 API 插件。

════════════════════════════════════════════════════════════════════
★ 本文件是 Alongside 第一个**真实第三方插件**的路由（2026-09-23，astrbot）
  写它的时候踩到并绕开了一条结构约束，务必读完再照抄：

  **第三方插件不能用相对导入。**
  内核按【文件路径】加载第三方路由（core/plugins/discover.py:158 →
  load_python_module_from_file(router_path, f"plugin_thirdparty_{id}_router")），
  模块不属于任何包，因此 `from .schema import X` 会抛
  `ImportError: attempted relative import with no known parent package`。
  内置插件（modules/<id>/）走包导入，相对导入是好的——两者规则不同，别照抄。

  ★ 本文件的解法（可直接抄给下一个第三方插件作者）：
    1) 表模型放独立 `models.py`，由下面的 `_load_models()` **按路径**加载；
    2) 模块名固定为 `countdown_models`，与 `api/migrations/0001_init.py`
       里那份**逐字相同** —— 于是两边共用 `sys.modules` 里同一个对象，
       SQLAlchemy 只有一张 Table，不会出现「同名表重复定义」；
    3) 出入参模型与服务逻辑就近写在本文件里：第三方插件目前
       **没有"共享工具模块"的合法位置**（拆成 schema.py/service.py 反而
       要各自再写一遍路径加载）。这条已作为架构观察回报。
════════════════════════════════════════════════════════════════════

HTTP 契约（全项目统一，不许自创）：
  成功 → 直接返回资源 JSON（200/201），**不包 {code,data}**
  失败 → 抛 core.errors 的 AppError 子类，由内核统一转 RFC7807
"""

import importlib.util
import json
import sys
from datetime import date as DateType
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi import Path as FPath
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from core.deps import get_current_user, get_db, get_plugin_client
from core.errors import NotFoundError, ValidationError
from core.events import event_bus
from core.security import User

# ★ 本文件不要加 `from __future__ import annotations`（与 modules/habits/router.py
#   同因：会让 FastAPI 对 Annotated 路径参数取不到真实类型）。

_HERE = Path(__file__).resolve().parent
_MODEL_KEY = "countdown_models"  # ★ 必须与 0001_init.py 的 _MODEL_MODULE 逐字一致

router = APIRouter()

#: 日历日按本地时区取（与 habits 同源：倒数日不存在"几点"，只存在"哪一天"）
_LOCAL_TZ = ZoneInfo("Asia/Shanghai")

KIND_COUNTDOWN = "countdown"  # 一次性倒数日：过期就是过期，days_left 可为负
KIND_ANNIVERSARY = "anniversary"  # 每年重复：自动滚到下一次，days_left 恒 >= 0
KINDS = (KIND_COUNTDOWN, KIND_ANNIVERSARY)


# ════════════════════════════ 模型加载（按路径） ════════════════════════════
def _load_models() -> Any:
    """按文件路径加载 models.py；已加载过则复用 sys.modules 里那一份。"""
    cached = sys.modules.get(_MODEL_KEY)
    if cached is not None:
        return cached
    path = _HERE / "models.py"
    spec = importlib.util.spec_from_file_location(_MODEL_KEY, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"countdown 无法加载模型文件：{path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODEL_KEY] = mod
    spec.loader.exec_module(mod)
    return mod


_MANIFEST = json.loads((_HERE.parent / "manifest.json").read_text(encoding="utf-8"))


# ════════════════════════════════ 出入参 ════════════════════════════════
class CountdownCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120, description="纪念日名称")
    target_date: DateType = Field(..., description="目标日 YYYY-MM-DD（日历日，不含时刻）")
    kind: str = Field(default=KIND_COUNTDOWN, description=f"{KIND_COUNTDOWN} | {KIND_ANNIVERSARY}")
    note: str = Field(default="", max_length=500, description="备注（可选）")
    color: str = Field(default="var(--accent)", max_length=40, description="卡片颜色（设计令牌或 #rrggbb）")


class CountdownUpdate(BaseModel):
    """PATCH 语义：只改写出来的字段，None = 不动。"""

    title: str | None = Field(default=None, min_length=1, max_length=120, description="改名称（不传=不改）")
    target_date: DateType | None = Field(default=None, description="改目标日（YYYY-MM-DD 日历日）")
    kind: str | None = Field(
        default=None, description="改类型：countdown=一次性倒数（过期可为负）· anniversary=每年重复（恒 ≥0）"
    )
    note: str | None = Field(default=None, max_length=500, description="改备注")
    color: str | None = Field(default=None, max_length=40, description="改卡片颜色")
    archived: bool | None = Field(
        default=None, description="true=归档（从列表默认隐藏，**数据保留**）· false=取消归档"
    )


class CountdownOut(BaseModel):
    id: str
    title: str
    target_date: DateType
    kind: str
    note: str
    color: str
    archived: bool
    # ── 计算字段（服务端一次算清，前端与 MCP 工具都不必再算）──
    next_date: DateType = Field(description="下一次发生日（anniversary 会滚到明年）")
    days_left: int = Field(description="距下一次的天数；countdown 过期为负")
    is_today: bool
    is_past: bool = Field(description="仅 countdown 有意义：一次性目标日已过")
    created_at: datetime
    updated_at: datetime


class LandmarkOut(BaseModel):
    """给别的模块当"新起点"信号用（讨论帖 N1：Temporal Landmark 引擎）。"""

    id: str
    title: str
    kind: str
    on_date: DateType
    days_until: int
    note: str = ""


DbDep = Session
UserDep = User


# ════════════════════════════════ 纯函数（可单测） ════════════════════════════════
def next_occurrence(target: DateType, kind: str, today: DateType) -> DateType:
    """算出「下一次发生日」。

    - countdown：就是它自己（过去也不滚，交给 days_left 显示负数）。
    - anniversary：先取今年的同月同日；若已过则取明年。
      ★ 2/29 的纪念日在平年落到 2/28（不静默跳月、不抛 ValueError）。
    """
    if kind != KIND_ANNIVERSARY:
        return target
    cand = _clamp_year(target, today.year)
    if cand < today:
        cand = _clamp_year(target, today.year + 1)
    return cand


def _clamp_year(target: DateType, year: int) -> DateType:
    """把 date 换到指定年份，2/29 遇平年收敛到 2/28。"""
    try:
        return target.replace(year=year)
    except ValueError:  # 只有 2 月 29 日会走到这里
        return DateType(year, 2, 28)


def to_out(item: Any, today: DateType) -> dict[str, Any]:
    nd = next_occurrence(item.target_date, item.kind, today)
    days = (nd - today).days
    return {
        "id": item.id,
        "title": item.title,
        "target_date": item.target_date,
        "kind": item.kind,
        "note": item.note,
        "color": item.color,
        "archived": item.archived,
        "next_date": nd,
        "days_left": days,
        "is_today": days == 0,
        "is_past": item.kind == KIND_COUNTDOWN and days < 0,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _validate_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ValidationError(f"kind 非法：{kind!r}（合法 {' | '.join(KINDS)}）")


# ════════════════════════════════ 路由 ════════════════════════════════
@router.get("/health")
def health() -> dict[str, bool]:
    """每个插件都必须有 health —— 内核据此判断"该能力是否可用"。"""
    return {"ok": True}


@router.get("/manifest")
def manifest() -> dict[str, Any]:
    """回显 manifest 原文（与内置插件同一惯例，能力目录/排障要用）。"""
    return _MANIFEST


@router.get("/items", response_model=list[CountdownOut])
def list_items(
    include_archived: bool = Query(False, description="是否连**已归档**的一起返回（默认 false）"),
    only_upcoming: bool = Query(False, description="只返回未过期的（countdown 过期即排除）"),
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """全部纪念日，按「距下一次多少天」升序（最近的排最前）。"""
    today = datetime.now(_LOCAL_TZ).date()
    stmt = select(_load_models().CountdownItem)
    rows = db.exec(stmt).all()
    out = [to_out(r, today) for r in rows if include_archived or not r.archived]
    if only_upcoming:
        out = [x for x in out if not x["is_past"]]
    out.sort(key=lambda x: (x["days_left"], x["title"]))
    return out


@router.post("/items", response_model=CountdownOut, status_code=status.HTTP_201_CREATED)
def create_item(
    body: CountdownCreate,
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """新建一条纪念日 / 倒数日。"""
    _validate_kind(body.kind)
    models = _load_models()
    item = models.CountdownItem(
        title=body.title.strip(),
        target_date=body.target_date,
        kind=body.kind,
        note=body.note,
        color=body.color,
    )
    if not item.title:
        raise ValidationError("title 不能为空")
    db.add(item)
    db.commit()
    db.refresh(item)
    out = to_out(item, datetime.now(_LOCAL_TZ).date())
    event_bus.publish("countdown.item.created", out, source="countdown")
    return out


@router.get("/items/{item_id}", response_model=CountdownOut)
def get_item(
    item_id: Annotated[str, FPath(description="纪念日 id")],
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """读单个纪念日/倒数日（含按**主人本地日**实时算出的 `days_left`）。"""
    today = datetime.now(_LOCAL_TZ).date()
    item = _get_or_404(db, item_id)
    return to_out(item, today)


@router.patch("/items/{item_id}", response_model=CountdownOut)
def update_item(
    item_id: Annotated[str, FPath(description="纪念日 id")],
    body: CountdownUpdate,
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """改纪念日（部分更新）。⚠️ 改 `kind` 会走合法性校验；`title` 不许改成空白。"""
    item = _get_or_404(db, item_id)
    patch = body.model_dump(exclude_none=True)
    if "kind" in patch:
        _validate_kind(patch["kind"])
    if "title" in patch and not patch["title"].strip():
        raise ValidationError("title 不能为空")
    for field, value in patch.items():
        setattr(item, field, value)
    db.add(item)
    db.commit()
    db.refresh(item)
    out = to_out(item, datetime.now(_LOCAL_TZ).date())
    event_bus.publish("countdown.item.updated", out, source="countdown")
    return out


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: Annotated[str, FPath(description="纪念日 id")],
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> None:
    """删除一个纪念日（不可恢复）。"""
    item = _get_or_404(db, item_id)
    db.delete(item)
    db.commit()
    event_bus.publish("countdown.item.deleted", {"id": item_id}, source="countdown")


@router.get("/today", response_model=list[CountdownOut])
def today_items(
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """今天到点的纪念日（anniversary 每年今天命中；countdown 只在目标日当天命中）。"""
    today = datetime.now(_LOCAL_TZ).date()
    rows = db.exec(select(_load_models().CountdownItem)).all()
    return [
        to_out(r, today)
        for r in rows
        if not r.archived and next_occurrence(r.target_date, r.kind, today) == today
    ]


@router.get("/landmarks", response_model=list[LandmarkOut])
def landmarks(
    window: int = Query(
        7, ge=0, le=3660, description="未来 N 天内（含今天）；上限十年，覆盖远期倒数日"
    ),
    db: DbDep = Depends(get_db),
    _user: UserDep = Depends(get_current_user),
) -> list[LandmarkOut]:
    """★ 未来 window 天内的「心理新起点」——供 dashboard / 复盘 / sidecar 卡片消费。

    依据：Dai, Milkman & Riis (2014) The Fresh Start Effect（*Management Science* 60(10)）：
    人在时间里程碑之后更可能开始追求目标。系统里已有的纪念日本就是**私人里程碑**，
    这个端点把它们从「被看的数字」变成「可被其他模块利用的触发点」。
    """
    today = datetime.now(_LOCAL_TZ).date()
    rows = db.exec(select(_load_models().CountdownItem)).all()
    items: list[LandmarkOut] = []
    for r in rows:
        if r.archived:
            continue
        nd = next_occurrence(r.target_date, r.kind, today)
        days = (nd - today).days
        if 0 <= days <= window:
            items.append(
                LandmarkOut(
                    id=r.id, title=r.title, kind=r.kind, on_date=nd, days_until=days, note=r.note
                )
            )
    items.sort(key=lambda x: (x.days_until, x.title))
    return items


@router.get("/example-cross-plugin")
def example_cross_plugin(request: Request) -> dict[str, Any]:
    """跨插件调用示例（ISSUE-005 A 案的可运行版本）。

    ★★ 关键纠正（2026-09-23 实跑发现，别照 `create_plugin.py --with-example` 抄）：
      `core.deps.get_plugin_client(request, target_capabilities)` 的第二个参数
      **没有默认值**，它的设计是【路由内函数直调】，**不是 FastAPI 依赖**。
      写成 `Depends(get_plugin_client)` 时，FastAPI 会把 `list[str]` 当请求体，
      GET 端点必然 **422 body Field required** —— 的首单模板 就踩了这个。
      正确姿势如下（target 必须 ⊆ manifest.requires，否则 403）：
    """
    client = get_plugin_client(request, ["catalog.read"])
    resp = client.get("/api/v1/catalog/health")
    return {"catalog_health": resp.status_code == 200, "status": resp.status_code}


# ════════════════════════════════ 内部工具 ════════════════════════════════
def _get_or_404(db: Session, item_id: str) -> Any:
    models = _load_models()
    item = db.get(models.CountdownItem, item_id)
    if item is None:
        raise NotFoundError(f"纪念日不存在：{item_id}")
    return item
