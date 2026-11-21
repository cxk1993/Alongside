"""Sight 聚合器（只读，零业务表）。

硬规则（全部继承自 dashboard/aggregator 的实证，那条路已经踩过一遍）：

  1. **插件之间不许 import** —— 只通过 HTTP 环回调同机 Alongside API。
  2. **每个子调用 800ms 超时**；失败/超时写进 `degraded`，**绝不让 /now 500**。
  3. **模块级共享 AsyncClient** —— 11-15 实测：4 路并发各建一个 client，总耗时 844ms
     且全部超时；复用同一连接池后降到 ~400ms 全部成功。
  4. **计数一律从上游响应字段读**，不硬编码。
  5. **不装深度（I5）**：没接的源写 `degraded(not_implemented)`，不编。

v1 只打三个源：`calendar` / `todo` / `habits`。
finance / health / notes / diary **故意不接**——它们要么没有稳定的只读端点，
要么字段还没定；硬接就是假装看得到。

自调凭据：`SIGHT_SELF_BASE` → 回退 `DASHBOARD_SELF_BASE` → 默认 `http://127.0.0.1:18000`。
测试注入：`set_fetch_override(mock_fetch)`，无需真起后端。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from core.config import get_settings, read_setting
from core.security import create_access_token

from .tensions import compute as compute_tensions

logger = logging.getLogger(__name__)

# 单子调用超时（毫秒）—— 与 dashboard 对齐，保持全项目同一个体感
# ★ 2026-11-22 02:12：800 → 1500。理由不是「慢就放宽」，而是**这两个调用不是一类东西**：
#   · dashboard 的 800ms 打的是 `/health` 探针（几乎零成本）
#   · sight 打的是 `/events` 这类 data 端点（查库 + 序列化 + 时区换算）
#   用同一个阈值 = 拿探针的标准要求查询。
#   实测（汐瑶 T15 真栈 ×6 次，结果完全一致）：三个源稳定卡在 866ms 总耗时，
#   每个子调用都贴着 800ms 被切断 → 不是冷启动、不是偶发，是**阈值本身定错了**。
#   1500ms 给 2 倍余量；同时每源耗时进日志——若真超过 1000ms，要查根因，
#   不是继续放宽。放宽阈值只能让「看不见」变成「看不见得更久」。
CALL_TIMEOUT_MS = 1500
CALL_TIMEOUT_S = CALL_TIMEOUT_MS / 1000.0

FetchFn = Callable[[str], Awaitable[Any]]
_fetch_override: FetchFn | None = None
_shared_client: httpx.AsyncClient | None = None

# v1 接的源（顺序稳定，便于测试与日志比对）
SOURCES: list[str] = ["calendar", "todo", "habits"]

# 明确「不接」的源 —— 写进 degraded，让调用方知道「看不到」不是「没有」
NOT_WIRED: list[tuple[str, str]] = [
    ("finance", "not_implemented"),
    ("health", "not_implemented"),
    ("notes", "not_implemented"),
    ("diary", "not_implemented"),
    ("course", "not_implemented"),
]


# ----------------------------- 自调凭据 -----------------------------
#
# ★ 这三个函数做了**进程级缓存**。原因：`read_setting` 每次都会重读 .env 文件
#   （`core/config._dotenv_values` 无缓存），而 sight 一次 `/now` 要调 `self_headers()`
#   三次（每源一次）+ `get_self_base()` 若干次 → 一次请求读十几次磁盘。
#   单次几 ms 看着不算什么，但它正好发生在超时细线上。
#   **能省的固定开销必须先省掉，再去谈「是不是真的慢」。**

_base_cache: str | None = None
_token_cache: str | None = None
#: token 失效时刻（epoch 秒）。`float("inf")` = 外部注入的 SIGHT_SELF_TOKEN，不猜过期。
_token_deadline: float = 0.0


def get_self_base() -> str:
    """同机 API 根地址。优先 SIGHT_SELF_BASE，其次沿用 DASHBOARD_SELF_BASE，最后默认 18000。

    进程内缓存：地址在运行期不变，每请求重读 .env 是纯浪费。
    """
    global _base_cache
    if _base_cache is None:
        for key in ("SIGHT_SELF_BASE", "DASHBOARD_SELF_BASE"):
            v = (read_setting(key, "") or "").strip()
            if v:
                _base_cache = v.rstrip("/")
                break
        else:
            _base_cache = "http://127.0.0.1:18000"
    return _base_cache


def get_self_token() -> str:
    """内网自调 token：SIGHT_SELF_TOKEN 优先，否则现签一个 admin JWT。

    ★ 2026-11-22 修（bidiu）：原实现写着「JWT 自带过期，但本进程寿命远短于它，故不做
      过期校验」——**这个前提是错的**。演示栈连续跑了 50 分钟后，缓存里的 admin JWT
      早已过期，`/sight/now` 的 calendar/todo/habits 三个源全部 401，tensions 归零、
      time 变空（演示第 1 拍直接空屏）。

      现在缓存 token 的同时记下失效时刻，**提前 5 分钟重签**；外部注入的
      SIGHT_SELF_TOKEN 视为长期有效（不猜别人的过期时间）。
    """
    global _token_cache, _token_deadline
    if _token_cache is not None and time.time() < _token_deadline:
        return _token_cache
    tok = (read_setting("SIGHT_SELF_TOKEN", "") or "").strip()
    if tok:
        _token_cache, _token_deadline = tok, float("inf")
        return _token_cache
    _token_cache = create_access_token("admin")
    try:
        minutes = int(read_setting("JWT_ACCESS_MINUTES", "30") or "30")
    except (TypeError, ValueError):
        minutes = 30
    # 提前 5 分钟续签，至少留 60 秒寿命（防止配成极小值导致每请求都重签）
    _token_deadline = time.time() + max(60, (minutes - 5) * 60)
    return _token_cache


def reset_credential_caches() -> None:
    """清缓存（测试用：改了 setting 之后需要重取）。"""
    global _base_cache, _token_cache, _token_deadline
    _base_cache = None
    _token_cache = None
    _token_deadline = 0.0


def self_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_self_token()}", "Accept": "application/json"}


def _get_shared_client() -> httpx.AsyncClient:
    """模块级共享 client（规则 3）。不关：它是进程级连接池。"""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(timeout=CALL_TIMEOUT_S, follow_redirects=False)
    return _shared_client


def set_fetch_override(fn: FetchFn | None) -> None:
    """测试注入。传 None 恢复真实 HTTP。"""
    global _fetch_override
    _fetch_override = fn


# ----------------------------- 时间工具 -----------------------------

def local_now() -> datetime:
    return datetime.now(ZoneInfo(get_settings().tz))


def local_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or local_now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _part_of_day(hour: int) -> str:
    if hour < 5:
        return "late_night"
    if hour < 8:
        return "dawn"
    if hour < 11:
        return "morning"
    if hour < 14:
        return "noon"
    if hour < 18:
        return "afternoon"
    if hour < 23:
        return "evening"
    return "late_night"


_WEEKDAY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# ----------------------------- 取数 -----------------------------

async def _fetch_json(path: str) -> dict[str, Any]:
    """取一个上游端点（带耗时日志）。永远不抛 —— 失败也要变成可记录的结果。

    ★ 每个源都记一行日志，不只记失败。
    理由：汐瑶报「稳定 timeout×3」时，响应体里看不到任何耗时（契约不允许带 _meta），
    于是只能猜。日志是唯一的定位入口——**看不见的慢，等于没慢**。
    """
    res = await _fetch_one(path)
    logger.info(
        "sight.fetch path=%s ok=%s reason=%s elapsed=%sms",
        path.split("?")[0], res["ok"], res.get("reason") or "-", res.get("elapsed_ms"),
    )
    return res


async def _fetch_one(path: str) -> dict[str, Any]:
    """真正的取数。永远不抛。"""
    started = time.perf_counter()
    try:
        if _fetch_override is not None:
            data = await asyncio.wait_for(_fetch_override(path), timeout=CALL_TIMEOUT_S)
            return {"ok": True, "data": data, "elapsed_ms": int((time.perf_counter() - started) * 1000)}

        url = f"{get_self_base()}{path}"
        r = await asyncio.wait_for(
            _get_shared_client().get(url, headers=self_headers()), timeout=CALL_TIMEOUT_S
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        if r.status_code >= 400:
            return {"ok": False, "reason": "error", "detail": f"HTTP {r.status_code}", "elapsed_ms": elapsed}
        return {"ok": True, "data": r.json(), "elapsed_ms": elapsed}

    except (asyncio.TimeoutError, httpx.TimeoutException):
        return {"ok": False, "reason": "timeout", "detail": f"超过 {CALL_TIMEOUT_MS}ms",
                "elapsed_ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001 —— 任何上游故障都降级，不 500
        return {"ok": False, "reason": "error", "detail": type(exc).__name__,
                "elapsed_ms": int((time.perf_counter() - started) * 1000)}


def _items(payload: Any) -> list[dict[str, Any]]:
    """宽容解析：上游可能返回 list，也可能返回 {items:[...]} / {data:[...]}。

    ★ 宽容不等于瞎猜：认得出就取，认不出就返回空 —— 空会让 degraded 说话，
      而不是让整包 500。
    """
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("items", "data", "rows", "list"):
            v = payload.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


# ----------------------------- 组装 -----------------------------

def _build_head(now: datetime) -> dict[str, Any]:
    return {
        "now_local": now.isoformat(timespec="seconds"),
        "weekday": _WEEKDAY[now.weekday()],
        "part_of_day": _part_of_day(now.hour),
    }


def _build_time(events: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """从今天的事件算出：正在忙到几点 / 下一件事 / 剩余空档。"""
    def _p(v: Any) -> datetime | None:
        if not isinstance(v, str):
            return None
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=now.tzinfo)

    spans: list[tuple[datetime, datetime]] = []
    for ev in events:
        s, e = _p(ev.get("start_at")), _p(ev.get("end_at"))
        if s is None:
            continue
        spans.append((s, e or s))
    if not spans:
        return None
    spans.sort(key=lambda t: t[0])

    busy_until = next((e for s, e in spans if s <= now < e), None)
    upcoming = [(s, e) for s, e in spans if s > now]
    next_event = None
    if upcoming:
        s, e = upcoming[0]
        # ★ next() 必须给 default：events 里若某条结构异常，生成器可能一条都匹配不上
        #   → StopIteration 从 async 冒穿 → /now 500。「永远 200」是契约级承诺，这里不能赌。
        matched = next((_t for _t in events if _p(_t.get("start_at")) == s), None)
        next_event = {
            "title": str((matched or {}).get("title") or "日程"),
            "start_at": s.isoformat(timespec="seconds"),
            "minutes_until": max(0, int((s - now).total_seconds() // 60)),
            # EventOut 有 location（calendar/schema.py L30）——透传，不硬编码 None
            "location": (matched or {}).get("location"),
        }

    # 空档：从「现在或最后一个已结束事件」到当天结束，只保留 ≥30 分钟
    _, day_end = local_day_bounds(now)
    cursor = max([now] + [e for _, e in spans if e <= now])
    free: list[dict[str, Any]] = []
    for s, e in spans:
        if s <= cursor:
            continue
        gap = int((s - cursor).total_seconds() // 60)
        if gap >= 30:
            free.append({"start": cursor.isoformat(timespec="seconds"),
                         "end": s.isoformat(timespec="seconds"), "minutes": gap})
        cursor = max(cursor, e)
    tail = int((day_end - cursor).total_seconds() // 60)
    if tail >= 30:
        free.append({"start": cursor.isoformat(timespec="seconds"),
                     "end": day_end.isoformat(timespec="seconds"), "minutes": tail})

    return {"busy_until": busy_until.isoformat(timespec="seconds") if busy_until else None,
            "next_event": next_event, "free_slots": free[:5]}


def _build_attention(
    todos: list[dict[str, Any]], habits: list[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    """挑「该看这几件」。最多 10 条，按紧迫度排。

    两个来源：
      · 待办 —— 已逾期(high) / 24h 内到期(medium)
      · 习惯 —— **只在晚上还没打卡才算 missed**（早上没打卡是常态，报出来只是噪音）

    「挂了很久」**故意不在这里报** —— 它归 `tensions.stale_item`。
    同一件事在两个板块各报一次，人会以为有两件事。
    """
    rank = {"high": 0, "medium": 1, "low": 2}
    out: list[dict[str, Any]] = []

    def _due(v: Any) -> datetime | None:
        if not isinstance(v, str):
            return None
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=now.tzinfo)

    for t in todos:
        if t.get("done"):
            continue
        due = _due(t.get("due_at"))
        created = _due(t.get("created_at"))
        text = str(t.get("text") or t.get("title") or "（未命名待办）")
        entry = {
            "kind": None,
            "title": text[:40],
            "ref": {"module": "todo", "id": str(t.get("id") or ""), "path": None},
            "age_days": (now - created).days if created else None,
            "due_at": due.isoformat(timespec="seconds") if due else None,
            "severity": "medium",
        }
        if due and due < now:
            entry["kind"] = "todo_overdue"
            entry["severity"] = "high"
        elif due and (due - now) <= timedelta(hours=24):
            entry["kind"] = "todo_due_soon"
        else:
            continue
        out.append(entry)

    # 习惯：只在晚上还没打卡才算 missed（清晨看「今天没打卡」是噪音，不是提醒）
    if _part_of_day(now.hour) in ("evening", "late_night"):
        for h in habits:
            # today_status 枚举: done | pending | rest（habits/schema.py L35）
            if str(h.get("today_status") or "") != "pending":
                continue
            streak = int(h.get("streak") or 0)
            out.append({
                "kind": "habit_missed",
                "title": f"「{h.get('name') or '习惯'}」今天还没打卡"[:40],
                "ref": {"module": "habits", "id": str(h.get("id") or ""), "path": None},
                "age_days": None,
                "due_at": None,
                # 有连续记录的人断了更疼 → medium；本来就没建起来的 → low
                "severity": "medium" if streak > 0 else "low",
            })

    out.sort(key=lambda e: (rank.get(e["severity"], 9), e["due_at"] or "9999"))
    return out[:10]


async def aggregate_now(now: datetime | None = None) -> dict[str, Any]:
    """`GET /sight/now` 的实现。**永远 200** —— 单源失败只让 degraded 变长。"""
    started = time.perf_counter()
    now = now or local_now()
    day_start, day_end = local_day_bounds(now)

    # ★ 必须 quote(..., safe='')：`+08:00` 里的 `+` 在 query 里会被服务端解码成空格，
    #   calendar 直接 4xx。dashboard L262-273 踩过同一个坑（当时实证 404）。
    #   后果链很狠：calendar 永远 degraded → time 块永缺 → schedule_conflict 永不触发
    #   → 90 秒验收第一拍废。所以这不是「建议修」，是**必修**。
    def _q(s: str) -> str:
        return quote(s, safe="")

    paths = {
        "calendar": (
            f"/api/v1/calendar/events?from={_q(day_start.isoformat())}"
            f"&to={_q(day_end.isoformat())}"
        ),
        "todo": "/api/v1/todo/items?status=todo&limit=100",
        "habits": "/api/v1/habits",
    }
    keys = list(paths)
    results = dict(zip(keys, await asyncio.gather(*[_fetch_json(paths[k]) for k in keys]), strict=True))

    degraded: list[dict[str, Any]] = []
    raw: dict[str, list[dict[str, Any]]] = {}
    for k in keys:
        r = results[k]
        if r["ok"]:
            raw[k] = _items(r["data"])
        else:
            raw[k] = []
            degraded.append({"source": k, "reason": r["reason"], "detail": r.get("detail")})
    for src_name, reason in NOT_WIRED:
        degraded.append({"source": src_name, "reason": reason, "detail": "v1 未接入"})

    # ★ v1 只取 100 条待办、不追 next_cursor。超过就静默截断——
    #   这不能忍「不说」，所以要留日志（响应体里放不下：契约 additionalProperties=false）。
    if len(raw["todo"]) >= 100:
        logger.warning(
            "sight: todo 达到上限 100 条，可能有截断（v1 不追 next_cursor）；"
            "attention/tensions 只基于前 100 条计算"
        )

    # ★ 契约里 `time` **不是 required**：没有日程时就不该有这一块。
    #   所以这里「没有就省略」，而不是塞一个 null（null 在契约里不合法）。
    #   这是第二次被契约抓住 —— 说明 additionalProperties:false 与 required 的
    #   组合，比任何口头约定都硬。
    out: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "head": _build_head(now),
        "attention": _build_attention(raw["todo"], raw["habits"], now),
        "tensions": compute_tensions(events=raw["calendar"], todos=raw["todo"], now=now),
        "degraded": degraded,
    }
    # ★ 三种语义必须分开（此前把「源挂了」和「没日程」混成了同一种「缺失」，是真 bug）：
    #   ① 源成功 + 有日程 → time 有内容
    #   ② 源成功 + 没日程 → time 是**空对象**（"知道了，确实没有"）
    #   ③ 源失败        → **不给 time**，degraded 里已记（"不知道"）
    # 为什么不能混：null/缺失表示「不知道」，空对象表示「知道且为空」。
    # 混在一起，前端就没法区分「今天真没安排」和「日程模块挂了」——
    # 而这两种情况，人应该得到完全不同的提示。
    t_block = _build_time(raw["calendar"], now)
    if results["calendar"]["ok"]:
        out["time"] = t_block or {"busy_until": None, "next_event": None, "free_slots": []}
    return out
