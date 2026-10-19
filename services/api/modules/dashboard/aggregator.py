"""概览聚合器（T11 技术要点）。

硬规则：
  1. 插件之间不许 import —— dashboard 只通过 HTTP 调同机 Alongside API。
  2. 每个子调用超时 800ms；失败/超时返回 {"status": "timeout"|"error"}。
  3. 任何一个模块挂掉都不能让 overview 500 —— 对应卡片降级，其余照常。

自调地址：`DASHBOARD_SELF_BASE`（默认 http://127.0.0.1:18000）。
内网鉴权：`DASHBOARD_SELF_TOKEN` 优先；未设置时复用 core.security 签发的 admin access token。
测试注入：`set_fetch_override(mock_fetch)`，无需真起 8000。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from core.config import get_settings, read_setting
from core.security import create_access_token

# 单子调用超时（毫秒）—— 任务卡 T11 判分线
CALL_TIMEOUT_MS = 800
CALL_TIMEOUT_S = CALL_TIMEOUT_MS / 1000.0

# 概览要打的上游模块（id → 中文名 + 各自的 health/data 路径）
UPSTREAM_MODULES: list[dict[str, str]] = [
    {"id": "calendar", "name": "日程表", "health": "/api/v1/calendar/health"},
    {"id": "todo", "name": "待办", "health": "/api/v1/todo/health"},
    {"id": "habits", "name": "习惯", "health": "/api/v1/habits/health"},
    {"id": "finance", "name": "理财", "health": "/api/v1/finance/health"},
    {"id": "review", "name": "复盘", "health": "/api/v1/review/health"},
    {"id": "agents", "name": "AI 编排", "health": "/api/v1/agents/health"},
]

# 声明了 dashboard.card 的插件在 overview.cards 里给出提示
CARD_HINT_NOTE = (
    "其它插件挂在 dashboard.card 上的卡片由前端 SlotHost 渲染"
    "（calendar / todo / habits / agents 已声明该扩展点）"
)

GrowthPlaceholder = (
    "完整成长罗盘后补：三轴条目与 PATCH 写接口将在后续任务卡落地"
)

FetchFn = Callable[[str], Awaitable[Any]]

_fetch_override: FetchFn | None = None


def get_self_base() -> str:
    """同机 Alongside API 根地址（可被 env/.env 覆盖）。默认指向生产后端端口 18000。"""
    return read_setting("DASHBOARD_SELF_BASE", "http://127.0.0.1:18000").rstrip("/")


def get_self_token() -> str:
    """内网自调 token：env/.env 优先，否则复用测试同款 admin JWT。"""
    tok = read_setting("DASHBOARD_SELF_TOKEN", "").strip()
    if tok:
        return tok
    return create_access_token("admin")


def self_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_self_token()}", "Accept": "application/json"}


def local_today() -> str:
    """本地日历日（Asia/Shanghai 或 settings.tz）。"""
    tz = ZoneInfo(get_settings().tz)
    return datetime.now(tz).date().isoformat()


def _today_window() -> tuple[str, str]:
    """今日起止（带时区 ISO8601），给 calendar.events 用。"""
    tz = ZoneInfo(get_settings().tz)
    day = datetime.now(tz).date()
    start = datetime.combine(day, dtime.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def set_fetch_override(fn: FetchFn | None) -> None:
    """测试注入 HTTP 客户端（None 恢复默认 httpx）。"""
    global _fetch_override
    _fetch_override = fn


def get_fetch() -> FetchFn:
    return _fetch_override if _fetch_override is not None else default_fetch


async def default_fetch(path: str) -> Any:
    """默认 HTTP 客户端：GET {DASHBOARD_SELF_BASE}{path}，非 2xx 抛错。

    asyncio.wait_for 叠在 httpx timeout 上：即使客户端实现忽略 timeout，
    聚合层仍会切断慢调用（任务卡：超时返回 status=timeout，不拖垮首屏）。
    """
    url = f"{get_self_base()}{path}"

    async def _do() -> Any:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self_headers(), timeout=CALL_TIMEOUT_S)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code} for {path}")
            return resp.json()

    return await asyncio.wait_for(_do(), timeout=CALL_TIMEOUT_S + 0.05)


async def _safe_call(fetch: FetchFn, path: str) -> dict[str, Any]:
    """带超时与降级的单次子调用。"""
    started = time.perf_counter()
    try:
        data = await asyncio.wait_for(fetch(path), timeout=CALL_TIMEOUT_S)
        elapsed = int((time.perf_counter() - started) * 1000)
        return {
            "status": "ok",
            "path": path,
            "data": data,
            "elapsed_ms": elapsed,
        }
    except TimeoutError:
        elapsed = int((time.perf_counter() - started) * 1000)
        return {
            "status": "timeout",
            "path": path,
            "data": None,
            "detail": f"超过 {CALL_TIMEOUT_MS}ms",
            "elapsed_ms": elapsed,
        }
    except Exception as exc:  # noqa: BLE001 — 任何上游故障都降级，不 500
        elapsed = int((time.perf_counter() - started) * 1000)
        return {
            "status": "error",
            "path": path,
            "data": None,
            "detail": str(exc) or type(exc).__name__,
            "elapsed_ms": elapsed,
        }


def _status_entry(
    module_id: str,
    name: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": module_id,
        "name": name,
        "status": result.get("status", "error"),
        "detail": str(result.get("detail") or ""),
        "path": str(result.get("path") or ""),
        "elapsed_ms": result.get("elapsed_ms"),
    }


def _ok_payload(result: dict[str, Any]) -> dict[str, Any]:
    """成功时返回 {"status":"ok", ...原 data}，否则就是降级字典。"""
    if result.get("status") != "ok":
        return {
            "status": result.get("status", "error"),
            "detail": result.get("detail", ""),
            "path": result.get("path", ""),
        }
    data = result.get("data")
    if isinstance(data, dict):
        out = dict(data)
        out["status"] = "ok"
        out["_path"] = result.get("path", "")
        return out
    return {"status": "ok", "value": data, "_path": result.get("path", "")}


def _count_from(data: dict[str, Any], key: str) -> int | None:
    if data.get("status") != "ok":
        return None
    val = data.get(key)
    return val if isinstance(val, int) and not isinstance(val, bool) else None


def _cards_from_plugins(result: dict[str, Any]) -> list[dict[str, Any]]:
    """从 GET /api/v1/plugins 提取声明了 dashboard.card 的插件。"""
    if result.get("status") != "ok":
        return []
    body = result.get("data")
    if not isinstance(body, dict):
        return []
    plugins = body.get("plugins")
    if not isinstance(plugins, list):
        return []
    out: list[dict[str, Any]] = []
    for p in plugins:
        if not isinstance(p, dict):
            continue
        if p.get("valid") is False:
            continue
        pid = p.get("id")
        if not pid or pid == "dashboard":
            continue
        slots = p.get("slots") or []
        if "dashboard.card" not in slots:
            continue
        out.append(
            {
                "pluginId": str(pid),
                "name": str(p.get("name") or pid),
                "slot": "dashboard.card",
                "enabled": bool(p.get("enabled", True)),
            }
        )
    return out


async def aggregate_overview(fetch: FetchFn | None = None) -> dict[str, Any]:
    """并行聚合首屏数据。任何子调用失败都不抛出。"""
    fn = fetch or get_fetch()
    day = local_today()
    frm, to = _today_window()

    # 数据调用路径（path → 语义）
    data_paths = {
        # ★★ 必须 URL 编码：frm/to 形如 `2026-09-18T00:00:00+08:00`，那个裸 `+`
        #   到服务端会被解码成**空格**；而空格在 HTTP 请求行里是分隔符，
        #   **会把路径截断** → 最终得到 404（不是 422）。
        #   实测现象：概览页 calendar 项报
        #   `HTTP 404 for /api/v1/calendar/events?from=2026-09-18T00:00:00+08:00&to=...`
        "calendar_events": (
            f"/api/v1/calendar/events?from={quote(frm, safe='')}&to={quote(to, safe='')}"
        ),
        "todo_summary": "/api/v1/todo/summary",
        "habits_summary": "/api/v1/habits/summary",
        "finance_snapshots": "/api/v1/finance/snapshots?limit=1",
        "finance_summary": "/api/v1/finance/summary",
        "review_source": "/api/v1/review/source",
        "plugins": "/api/v1/plugins",
    }

    started = time.perf_counter()
    tasks: dict[str, Awaitable[dict[str, Any]]] = {
        f"data:{key}": _safe_call(fn, path) for key, path in data_paths.items()
    }
    for mod in UPSTREAM_MODULES:
        tasks[f"health:{mod['id']}"] = _safe_call(fn, mod["health"])

    keys = list(tasks.keys())
    results_list = await asyncio.gather(*[tasks[k] for k in keys])
    results = dict(zip(keys, results_list, strict=True))

    calendar = _ok_payload(results["data:calendar_events"])
    todo = _ok_payload(results["data:todo_summary"])
    habits = _ok_payload(results["data:habits_summary"])
    snapshots = _ok_payload(results["data:finance_snapshots"])
    finance_summary = _ok_payload(results["data:finance_summary"])
    review_source = _ok_payload(results["data:review_source"])
    plugins_result = results["data:plugins"]

    # 最新快照（列表 date 倒序，取第一条）
    latest_snapshot: dict[str, Any] = {"status": snapshots.get("status", "error")}
    if snapshots.get("status") == "ok":
        items = snapshots.get("items") or []
        if items and isinstance(items[0], dict):
            latest_snapshot = {"status": "ok", **items[0], "_path": data_paths["finance_snapshots"]}
        else:
            latest_snapshot = {
                "status": "ok",
                "empty": True,
                "hint": "暂无资产快照",
                "_path": data_paths["finance_snapshots"],
            }
    else:
        latest_snapshot = {
            "status": snapshots.get("status", "error"),
            "detail": snapshots.get("detail", ""),
            "_path": data_paths["finance_snapshots"],
        }

    system: list[dict[str, Any]] = []
    for mod in UPSTREAM_MODULES:
        system.append(
            _status_entry(mod["id"], mod["name"], results[f"health:{mod['id']}"])
        )

    cards = _cards_from_plugins(plugins_result)
    habits_done = _count_from(habits, "done")
    habits_total = _count_from(habits, "total")

    counts = {
        "calendar_events": _count_events(calendar),
        "todo_open": _count_from(todo, "today"),
        "habits_done": habits_done,
        "habits_total": habits_total,
    }

    return {
        "date": day,
        "today": {
            "date": day,
            "calendar": calendar,
            "todo": todo,
            "habits": habits,
            "counts": counts,
        },
        "money": {
            "snapshot": latest_snapshot,
            "summary": finance_summary,
        },
        "review": {
            "source": review_source,
        },
        "growth": {
            "axes": [],
            "placeholder": GrowthPlaceholder,
        },
        "system": system,
        "cards": cards,
        "cards_hint": CARD_HINT_NOTE,
        "meta": {
            "timeout_ms": CALL_TIMEOUT_MS,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "self_base": get_self_base(),
            "upstream_paths": data_paths,
            "degraded": [s["id"] for s in system if s["status"] != "ok"],
        },
    }


def _count_events(calendar: dict[str, Any]) -> int | None:
    """calendar /events 返回 list[EventOut]（或降级字典）。"""
    if calendar.get("status") != "ok":
        return None
    val = calendar.get("value")
    if isinstance(val, list):
        return len(val)
    items = calendar.get("items")
    if isinstance(items, list):
        return len(items)
    return None


async def aggregate_today(fetch: FetchFn | None = None) -> dict[str, Any]:
    """今日合并视图（日程 + 待办 + 习惯）。"""
    full = await aggregate_overview(fetch=fetch)
    today = dict(full["today"])
    today["meta"] = {
        "timeout_ms": CALL_TIMEOUT_MS,
        "source": "aggregator.aggregate_today",
    }
    return today


async def aggregate_health_of_system(fetch: FetchFn | None = None) -> dict[str, Any]:
    """各模块健康灯。"""
    fn = fetch or get_fetch()
    started = time.perf_counter()
    tasks = {m["id"]: _safe_call(fn, m["health"]) for m in UPSTREAM_MODULES}
    ids = list(tasks.keys())
    results = await asyncio.gather(*[tasks[i] for i in ids])
    by_id = dict(zip(ids, results, strict=True))
    system = [
        _status_entry(m["id"], m["name"], by_id[m["id"]]) for m in UPSTREAM_MODULES
    ]
    return {
        "date": local_today(),
        "system": system,
        "meta": {
            "timeout_ms": CALL_TIMEOUT_MS,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "degraded": [s["id"] for s in system if s["status"] != "ok"],
        },
    }
