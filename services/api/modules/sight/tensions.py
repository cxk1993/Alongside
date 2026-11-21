"""张力判断（纯函数，零 IO、零依赖）。

★ 这是 sight 的核心：**不是把数据吐出来，而是先做完判断再交出。**
空列表是合法且重要的答案——此刻没有冲突，不要为了「有内容」而硬造（I5 不装深度）。

v1 只实现三条规则，每条都对应契约里已有的 kind：
  1. schedule_conflict —— 两个日程时间重叠
  2. overload          —— 今天排得太满
  3. stale_item        —— 待办挂了太久

其余 kind（overload 之外的 goal_drift / resource_short / missing_info）**故意不实现**：
没有可靠数据源就硬造，就是装深度。契约里留着位置，等真有判据再填。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

MAX_TENSIONS = 5

# 阈值集中在这里，便于以后用一个 settings 覆盖（现在写死，因为还没有配置项）
CONFLICT_MIN_OVERLAP_MIN = 1      # 重叠 ≥1 分钟才算冲突（连续会议不算）
OVERLOAD_EVENTS = 6               # 一天 ≥6 个日程 = 排太满
OVERLOAD_TODOS_DUE = 12           # 到期/过期待办 ≥12 条 = 排太满
STALE_DAYS = 7                    # 待办挂 ≥7 天算 stale


# ----------------------------- 工具 -----------------------------

def _dt(value: Any) -> datetime | None:
    """尽力把上游给的时间转成带时区的 datetime；转不动就返回 None（不猜）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        d = value
    elif isinstance(value, str):
        s = value.strip().replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            return None
    else:
        return None
    # naive 一律按 UTC 处理前先标记 —— 与项目「库内 UTC、对外带时区」的约定一致
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _ref(module: str, id_: str | None, label: str | None = None) -> dict[str, Any]:
    return {"module": module, "id": id_, "label": label}


def _tension(
    tid: str,
    kind: str,
    summary: str,
    severity: str,
    refs: list[dict[str, Any]] | None = None,
    suggested_action: str | None = None,
) -> dict[str, Any]:
    return {
        "id": tid,
        "kind": kind,
        "summary": summary[:120],
        "severity": severity,
        "refs": refs or [],
        "suggested_action": suggested_action,
    }


# ----------------------------- 规则 1：日程冲突 -----------------------------

def find_conflicts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """两个日程时间重叠。

    输入事件需含 start_at / end_at（任一不可解析就跳过这一条，不猜）。
    只报「不同 id 且真实重叠 ≥ CONFLICT_MIN_OVERLAP_MIN 分钟」的配对，
    同一对只报一次（按 id 字典序去重），severity 依据重叠时长：
        重叠 ≥30 分钟 → high；≥10 分钟 → medium；其余 low。
    """
    parsed: list[tuple[dict[str, Any], datetime, datetime]] = []
    for ev in events or []:
        s, e = _dt(ev.get("start_at")), _dt(ev.get("end_at"))
        if s is None or (e is not None and e <= s):
            # 没有 end_at 的，按 0 时长处理；时间不可解析的直接跳过
            if s is None:
                continue
            e = s
        parsed.append((ev, s, e))

    parsed.sort(key=lambda t: t[1])
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            a, a_s, a_e = parsed[i]
            b, b_s, b_e = parsed[j]
            if b_s >= a_e:
                break  # 已按开始时间排序，后面不可能再重叠
            overlap = (min(a_e, b_e) - max(a_s, b_s)).total_seconds() / 60
            if overlap < CONFLICT_MIN_OVERLAP_MIN:
                continue
            ida, idb = str(a.get("id") or ""), str(b.get("id") or "")
            if not ida or not idb or ida == idb:
                continue
            key = tuple(sorted((ida, idb)))
            if key in seen:
                continue
            seen.add(key)
            sev = "high" if overlap >= 30 else "medium" if overlap >= 10 else "low"
            title_a = str(a.get("title") or "（未命名日程）")
            title_b = str(b.get("title") or "（未命名日程）")
            out.append(
                _tension(
                    tid=f"tn_conflict_{key[0]}_{key[1]}",
                    kind="schedule_conflict",
                    summary=f"「{title_a}」与「{title_b}」重叠约 {int(overlap)} 分钟",
                    severity=sev,
                    refs=[_ref("calendar", ida, title_a), _ref("calendar", idb, title_b)],
                    suggested_action="把其中一件挪开，或确认是否可以不参加",
                )
            )
    return out


# ----------------------------- 规则 2：排太满 -----------------------------

def find_overload(
    events: list[dict[str, Any]], todos: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """今天排得太满（日程多 或 待办积压）。两条都超才报 high。"""
    n_ev = len(events or [])
    due = [
        t
        for t in (todos or [])
        if not t.get("done") and (t.get("due_at") or t.get("status") in ("overdue", "due"))
    ]
    n_due = len(due)
    ev_over = n_ev >= OVERLOAD_EVENTS
    td_over = n_due >= OVERLOAD_TODOS_DUE
    if not ev_over and not td_over:
        return []

    parts = []
    if ev_over:
        parts.append(f"{n_ev} 个日程")
    if td_over:
        parts.append(f"{n_due} 条到期待办")
    sev = "high" if (ev_over and td_over) else "medium"
    return [
        _tension(
            tid="tn_overload_today",
            kind="overload",
            summary="今天排得偏满：" + "、".join(parts),
            severity=sev,
            refs=[_ref("calendar", None, "今天"), _ref("todo", None, "待办")],
            suggested_action="挑一件推迟或交出去",
        )
    ]


# ----------------------------- 规则 3：挂太久的待办 -----------------------------

def find_stale(
    todos: list[dict[str, Any]], now: datetime | None = None
) -> list[dict[str, Any]]:
    """挂 ≥STALE_DAYS 天且未完成的待办，取最老的三条。

    用 created_at 计算年龄；没有 created_at 的跳过（不猜）。
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    stale: list[tuple[int, dict[str, Any]]] = []
    for t in todos or []:
        if t.get("done"):
            continue
        c = _dt(t.get("created_at"))
        if c is None:
            continue
        age = (now - c).days
        if age >= STALE_DAYS:
            stale.append((age, t))

    if not stale:
        return []
    stale.sort(key=lambda p: p[0], reverse=True)
    return [
        _tension(
            tid=f"tn_stale_{t.get('id')}",
            kind="stale_item",
            summary=f"「{t.get('text') or t.get('title') or '一条待办'}」挂了 {age} 天没动",
            severity="high" if age >= STALE_DAYS * 2 else "medium",
            refs=[_ref("todo", str(t.get("id") or ""), None)],
            suggested_action="要么今天做掉，要么明确放弃——挂着比放弃更耗人",
        )
        for age, t in stale[:3]
    ]


# ----------------------------- 总入口 -----------------------------

def compute(
    events: list[dict[str, Any]] | None = None,
    todos: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """把三条规则的结果合并，按 severity 降序，最多 MAX_TENSIONS 条。

    ★ 顺序稳定：severity(high>medium>low) → id 字典序。
    稳定顺序是给前端与测试用的（不许靠随机洗牌）。
    """
    items: list[dict[str, Any]] = []
    items += find_conflicts(events or [])
    items += find_overload(events or [], todos or [])
    items += find_stale(todos or [], now=now)

    rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: (rank.get(x["severity"], 9), x["id"]))
    return items[:MAX_TENSIONS]
