"""U2 小日历 · 日聚合（r7 起走能力发现 + HTTP 环回，dashboard 不再直读他家模型）。

★ 为什么重写：旧实现 `try: from modules.calendar.models import ...` 直读四家模型，
  违反本模块纪律（router.py:8「插件之间不许 import——聚合走 HTTP」），且新插件
  永远进不了月历/单日聚合。现在与 today-summary 的 BFF 同款：**能力发现 + 环回聚合**。

契约（声明 `provides: "x.day.peek"` 的模块必须实现，键名固定、条目形状自由）：
  GET /api/v1/<id>/day-peek?date=YYYY-MM-DD   → {"items": [...]}   该源当日条目
  GET /api/v1/<id>/day-dots?from=…&to=…       → {"dates": ["YYYY-MM-DD", …]}

发现：`request.app.state.modules`（运行时动态——热装插件声明即参与，无需重启）。
降级：单家超时/失败 → 该源当日缺席（缺一不塌），不影响其余。
复用 aggregator 的 fetch 机制（self-base / 内网 token / 测试注入 set_fetch_override）。
"""
from __future__ import annotations

import asyncio
from datetime import date as _date
from typing import Any

from .aggregator import _safe_call, get_fetch


def _providers(app: Any) -> list[str]:
    """从模块注册表发现声明了 x.day.peek 的模块（运行时动态）。"""
    mods = getattr(getattr(app, "state", None), "modules", None) or {}
    out: list[str] = []
    for pid, manifest in mods.items():
        if not isinstance(manifest, dict):
            continue
        if "x.day.peek" in (manifest.get("provides") or []):
            out.append(str(pid))
    return sorted(out)


async def day_peek(app: Any, d: _date) -> dict[str, Any]:
    """单日聚合：并行环回各 provider 的 day-peek，软失败缺席。"""
    fn = get_fetch()
    providers = _providers(app)
    tasks = [
        _safe_call(fn, f"/api/v1/{pid}/day-peek?date={d.isoformat()}")
        for pid in providers
    ]
    results = await asyncio.gather(*tasks) if tasks else []

    list_out: dict[str, list[Any]] = {}
    marks: dict[str, int] = {}
    dots: dict[str, bool] = {}
    for pid, res in zip(providers, results, strict=True):
        if res.get("status") != "ok":
            continue  # 单家失败 → 该源缺席，不拖累其余
        data = res.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        list_out[pid] = items
        marks[pid] = len(items)
        dots[pid] = bool(items)
    return {"date": d.isoformat(), "marks": marks, "dots": dots, "list": list_out}


async def day_dots(app: Any, start: _date, end: _date) -> dict[str, list[str]]:
    """月视图打点：date → [provider-id, …]（各源报日期，这里并集合并）。"""
    fn = get_fetch()
    providers = _providers(app)
    tasks = [
        _safe_call(
            fn,
            f"/api/v1/{pid}/day-dots?from={start.isoformat()}&to={end.isoformat()}",
        )
        for pid in providers
    ]
    results = await asyncio.gather(*tasks) if tasks else []

    out: dict[str, list[str]] = {}
    for pid, res in zip(providers, results, strict=True):
        if res.get("status") != "ok":
            continue
        data = res.get("data")
        dates = data.get("dates") if isinstance(data, dict) else None
        if not isinstance(dates, list):
            continue
        for ds in dates:
            if isinstance(ds, str) and start.isoformat() <= ds <= end.isoformat():
                out.setdefault(ds, []).append(pid)
    return {k: sorted(v) for k, v in sorted(out.items())}
