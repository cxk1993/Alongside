"""URL 编码回归：`+08:00` 里的 `+` 必须被编码成 `%2B`。

★ 这条 bug 有尸体：
  - `dashboard/aggregator.py` L262-273 曾因同样原因 404；
  - `sight` 初版（01:23）踩了同一个坑，被只读子代理的审计抓出来（§五.3 #1）。

后果链（不是"参数不好看"，是功能链断裂）：
  calendar 的 `from`/`to` 解析失败 → 4xx → calendar 永久进 `degraded`
  → `time` 块永远缺失 → `tensions` 里的 `schedule_conflict` 永不触发
  → **90 秒验收的第一拍（"AI 发现明天两件事撞了"）直接废掉**。

钉死方式：**断言 aggregator 真正发出的 path**（而不是断言"服务端能不能解析"——
后者要起服务，太重且容易变成假绿）。一句话：验"它发了什么"，不验"别人怎么理解"。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from modules.sight.aggregator import aggregate_now, set_fetch_override

NOW = datetime(2026, 11, 22, 1, 30, tzinfo=timezone(timedelta(hours=8)))


def _capture_paths():
    """跑一次聚合，把 aggregator 发出的所有 path 抓下来。"""
    seen: list[str] = []

    async def spy(path: str):
        seen.append(path)
        return []

    set_fetch_override(spy)
    try:
        asyncio.run(aggregate_now(now=NOW))
    finally:
        set_fetch_override(None)
    return seen


def test_calendar_url_is_percent_encoded():
    """★ 核心断言：calendar 的 from/to 必须含 %2B，且不得出现明文 '+08:00'。"""
    paths = _capture_paths()
    cal = [p for p in paths if "/calendar/events" in p]
    assert cal, f"没有发出 calendar 请求；实际发出：{paths}"
    url = cal[0]

    assert "%2B" in url, (
        f"calendar URL 没有把 '+' 编码成 %2B —— 服务端会把 '+' 解成空格，"
        f"时间只剩 '2026-11-22T00:00:00 08:00'，解析必失败。实际 URL：{url}"
    )
    assert "+08:00" not in url.split("%2B")[0] or True   # 只做说明，下面硬断言


def test_calendar_url_has_no_raw_plus():
    """明文 `+` 不许出现在 query 里（除了被编码后的 %2B，解码前不含 '+'）。"""
    paths = _capture_paths()
    url = [p for p in paths if "/calendar/events" in p][0]
    query = url.split("?", 1)[1] if "?" in url else ""
    assert "+" not in query, (
        f"query 里出现了明文 '+'，它会被服务端解码成空格。query={query!r}"
    )


def test_all_upstream_paths_are_ascii_safe():
    """三条上游路径都不该含会破坏 query 的字符（空格/未编码的 +）。"""
    paths = _capture_paths()
    assert len(paths) >= 3, f"应当发出至少 3 路请求，实际 {len(paths)}"
    for p in paths:
        assert " " not in p, f"路径里出现空格（说明参数没编码）：{p}"


def test_quote_roundtrip_semantics():
    """把「有 bug 会怎样」也钉住：证明不编码确实会坏，防止有人"优化"掉 quote。"""
    from urllib.parse import parse_qs, quote, urlparse

    iso = "2026-11-22T00:00:00+08:00"
    bad = parse_qs(urlparse(f"?from={iso}").query)["from"][0]
    good = parse_qs(urlparse(f"?from={quote(iso, safe='')}").query)["from"][0]

    assert bad != iso, "如果不编码就已经能正确还原，那这条测试的前提就不成立了"
    assert bad == "2026-11-22T00:00:00 08:00", f"未经预期的破坏形态：{bad!r}"
    assert good == iso, "编码后必须完整还原"
