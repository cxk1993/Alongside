"""13 条复测探针包（D）· 只读 · 部署后 5 分钟填实测列。

用法：
  python tools/accept_probe/retest_13.py --base-url https://life.example.com:8443
  python tools/accept_probe/retest_13.py --base-url http://127.0.0.1:8000 --token $TOKEN

输出 JSON 表，直接贴/ 喂豆包对照表「实测」列。
★ 只 GET；不写生产。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def fetch(url: str, token: str = "") -> tuple[int, str]:
    req = Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urlopen(req, timeout=12) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except HTTPError as e:
        return int(e.code), e.read().decode("utf-8", "replace")
    except URLError as e:
        return 0, str(e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="https://life.example.com:8443")
    ap.add_argument("--token", default="")
    a = ap.parse_args()
    base = a.base_url.rstrip("/")
    tok = a.token
    rows: list[dict] = []

    def rec(item: str, check: str, ok: bool, detail: str) -> None:
        rows.append({"item": item, "check": check, "ok": ok, "detail": detail})

    # ② 四源 / BFF
    for p in (
        "/api/v1/todo/today-summary",
        "/api/v1/diary/today-summary",
        "/api/v1/review/today-summary",
        "/api/v1/calendar/today-summary",
        "/api/v1/summary/today",
    ):
        st, body = fetch(base + p, tok)
        # 200=有数据路径 · 401=端点在待登录 · 404=未部署
        ok = st in (200, 401)
        rec("②", p, ok, f"HTTP {st}")

    # ⑤ notes/libs（2026-09-25 Qoder 勘误：实际路由名=libs，/notes/tree 不存在）
    st, _ = fetch(base + "/api/v1/notes/libs", tok)
    rec("⑤", "notes/libs", st in (200, 401), f"HTTP {st}")
    st, _ = fetch(base + "/api/v1/query/presets", tok)
    rec("⑬", "query/presets", st in (200, 401), f"HTTP {st}")
    st, _ = fetch(base + "/api/v1/export/profiles", tok)
    rec("⑬", "export/profiles", st in (200, 401), f"HTTP {st}")

    # ⑧ habits
    st, _ = fetch(base + "/api/v1/habits/health", tok)
    rec("⑧", "habits/health", st in (200, 401), f"HTTP {st}")

    # ⑦ todo
    st, _ = fetch(base + "/api/v1/todo/items?limit=200", tok)
    rec("⑦", "todo/items limit=200", st in (200, 401), f"HTTP {st}")
    # ⑧ habits 列表（1ce4691 后 = GET /api/v1/habits）
    st, _ = fetch(base + "/api/v1/habits", tok)
    rec("⑧", "habits list", st in (200, 401), f"HTTP {st}")

    # 前端 entry 特征（①④⑤⑬ 壳层/渲染）
    st, html = fetch(base + "/", "")
    m = re.search(r"index-[A-Za-z0-9_-]+\.js", html or "")
    entry = m.group(0) if m else ""
    rec("壳", "entry", bool(entry), entry or "not found")
    if entry:
        stj, js = fetch(base + f"/assets/{entry}", "")
        for key, tag in (
            ("desktop--top-collapsed", "①收放"),
            ("today-sum-", "②子态"),
        ):
            rec(tag, f"js has {key}", key in js, "yes" if key in js else "no")
        # ⑤KaTeX/⑧自定义：懒加载独立 chunk（主 js 不含=正常）。
        # ★ 双前缀修复（P0-1）：Vite 引用形如 assets/foo.js | ./foo.js | foo.js，
        #   一律归一 basename 再打 /assets/<basename>，杜绝 /assets/assets/ 与漏抓。
        chunk_hits = {
            "katex": False,
            "alongside.plugin.habits.prefs": False,
            "alongside.plugin.sidebar.prefs": False,
        }
        names: set[str] = set()
        for raw in re.findall(r"([A-Za-z0-9_-]+\.js)", js):
            if raw == entry:
                continue
            names.add(raw)
        for c in sorted(names):
            try:
                _, cj = fetch(base + f"/assets/{c}", "")
                for k in chunk_hits:
                    if k in cj:
                        chunk_hits[k] = True
            except Exception:
                pass
        pref_ok = chunk_hits["alongside.plugin.habits.prefs"] or chunk_hits["alongside.plugin.sidebar.prefs"]
        rec("⑤KaTeX", "chunk has katex", chunk_hits["katex"], "yes" if chunk_hits["katex"] else "no")
        rec("⑧自定义", "chunk has prefs", pref_ok, "yes" if pref_ok else "no")

    # grid via css 若可
    stc, css_html = fetch(base + "/", "")
    mcss = re.search(r"index-[A-Za-z0-9_-]+\.css", css_html or "")
    if mcss:
        _, css = fetch(base + f"/assets/{mcss.group(0)}", "")
        rec("③", "background-size:32px", "background-size:32px" in css.replace(" ", "") or "background-size: 32px" in css, "css")

    out = {"base": base, "entry": entry, "rows": rows, "fail_count": sum(1 for r in rows if not r["ok"])}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["fail_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
