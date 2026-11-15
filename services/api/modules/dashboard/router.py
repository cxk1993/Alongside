"""插件路由：成长罗盘（dashboard）。

HTTP 契约（全项目统一）：
  成功 → 直接返回资源 JSON（200）
  失败 → 由内核统一转成 RFC7807 application/problem+json

★ 前缀由内核按 manifest.api.base 自动加，这里不要写 prefix=。
★ 插件之间不许 import —— 聚合走 HTTP（aggregator.py）。
★ 本模块只读：growth 写接口后补。
"""
import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi import Path as FPath
from fastapi import Query

from core.deps import get_current_user, get_db
from core.security import User

from .aggregator import (
    aggregate_health_of_system,
    aggregate_overview,
    aggregate_today,
    get_self_base,
)
from .schema import HealthOfSystemOut, HealthOut, OverviewOut

router = APIRouter()

_MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"
with _MANIFEST_PATH.open(encoding="utf-8") as _f:
    _MANIFEST = json.load(_f)

UserDep = User


@router.get("/health", response_model=HealthOut)
def health() -> dict[str, bool]:
    """每个插件都必须有 health —— 内核据此判断"该能力是否可用"。"""
    return {"ok": True}


@router.get("/manifest")
def manifest() -> dict:
    """模块清单（前端 / AI 发现能力用）。"""
    return _MANIFEST


@router.get("/self-base")
def self_base(_user: UserDep = Depends(get_current_user)) -> dict[str, str]:
    """聚合器自调地址（诊断用；可被 DASHBOARD_SELF_BASE 覆盖）。"""
    return {"base": get_self_base()}


@router.get("/overview", response_model=OverviewOut)
async def overview(
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """★ 首屏一次取全：date / today / money / review / system / cards。

    单模块失败或超时只会让对应卡片降级，整包永远 200。
    """
    return await aggregate_overview()


@router.get("/today")
async def today(
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """今日合并视图（日程数 / 待办 / 习惯 x/n）。"""
    return await aggregate_today()


@router.get("/day-peek")
async def day_peek_endpoint(
    request: Request,
    date: str | None = Query(None, description="要看的日期（YYYY-MM-DD 日历日；不传=今天）"),
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """U2 小日历：单日聚合（r7 起按 x.day.peek 能力发现 + 环回聚合）。只读。"""
    from datetime import date as _date

    from .day_peek import day_peek as _peek

    d = _date.fromisoformat(date) if date else _date.today()
    return await _peek(request.app, d)


@router.get("/day-dots")
async def day_dots_endpoint(
    request: Request,
    from_: Annotated[str | None, Query(alias="from", description="起始日期（YYYY-MM-DD 日历日）")] = None,
    to: str | None = Query(None, description="结束日期（YYYY-MM-DD 日历日）"),
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """U2 小日历：区间打点（月视图；r7 起按 x.day.peek 能力发现）。只读。"""
    from datetime import date as _date

    from .day_peek import day_dots as _dots

    start = _date.fromisoformat(from_) if from_ else _date.today().replace(day=1)
    end = _date.fromisoformat(to) if to else start
    return await _dots(request.app, start, end)


@router.get("/health-of-system", response_model=HealthOfSystemOut)
async def health_of_system(
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """各模块健康状况（哪个模块挂了要显示出来）。"""
    return await aggregate_health_of_system()


@router.get("/growth")
async def growth(
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """成长罗盘三轴：最小闭环返回空数组 + 占位说明（完整罗盘后补）。"""
    return {
        "axes": [],
        "placeholder": (
            "完整成长罗盘后补：三轴条目与 PATCH 写接口将在后续任务卡落地"
        ),
        "source": "placeholder",
    }


@router.get("/cards/for-slot/{slot_name}")
async def cards_for_slot(
    slot_name: Annotated[str, FPath(description="扩展点名，如 dashboard.card")],
    _user: UserDep = Depends(get_current_user),
) -> dict[str, Any]:
    """诊断：overview.cards 提示对应的扩展点（前端用 SlotHost 渲染真卡片）。"""
    full = await aggregate_overview()
    return {
        "slot": slot_name,
        "declared": [c for c in full.get("cards", []) if c.get("slot") == slot_name],
        "hint": full.get("cards_hint", ""),
        "note": "真卡片组件由前端 SlotHost 从各插件 slots 注册表渲染，后端只给提示。",
    }
