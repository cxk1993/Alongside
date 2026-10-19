#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Alongside 全模块 API 巡检（13 模块 REST 探针，配合 hermes 初验 / Qoder 深验）。

不替代真机浏览器验收；用于「登录后各模块 API 是否可达」的机器侧清单。

用法：
  python tools/probe_all_modules_api.py --base http://192.0.2.10:18080 --token <jwt>
  LIFEOS_TOKEN=xxx python tools/probe_all_modules_api.py
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

# (窗口名, method, path) — 优先 health/manifest/只读列表
CHECKS: list[tuple[str, str, str]] = [
    ("内核 healthz", "GET", "/healthz"),
    ("内核 modules", "GET", "/api/v1/modules"),
    ("成长罗盘", "GET", "/api/v1/dashboard/health"),
    ("复盘", "GET", "/api/v1/review/health"),
    ("复盘 source", "GET", "/api/v1/review/source"),
    ("日程表", "GET", "/api/v1/calendar/health"),
    ("日程提醒调度", "GET", "/api/v1/calendar/reminders/scheduler"),
    ("待办", "GET", "/api/v1/todo/health"),
    ("习惯", "GET", "/api/v1/habits/health"),
    ("理财", "GET", "/api/v1/finance/health"),
    ("理财调度", "GET", "/api/v1/finance/snapshots/scheduler"),
    ("笔记", "GET", "/api/v1/notes/health"),
    ("智能体", "GET", "/api/v1/agents/health"),
    ("网页工作台", "GET", "/api/v1/web/health"),
    ("网页入口列表", "GET", "/api/v1/web/entries"),
    ("文档", "GET", "/api/v1/docs/health"),
    ("文档树根", "GET", "/api/v1/docs/nodes"),
    ("人格", "GET", "/api/v1/persona/health"),
    ("日记", "GET", "/api/v1/diary/health"),
    ("健康 T21", "GET", "/api/v1/health/health"),
    ("健康记录", "GET", "/api/v1/health/records"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("LIFEOS_BASE") or "http://192.0.2.10:18080")
    ap.add_argument("--token", default=os.environ.get("LIFEOS_TOKEN") or "")
    args = ap.parse_args()
    token = args.token
    if not token:
        print("FAIL: 需要 --token 或 LIFEOS_TOKEN")
        return 2
    base = args.base.rstrip("/")
    h = {"Authorization": f"Bearer {token}"}
    fails: list[str] = []
    for name, method, path in CHECKS:
        url = base + path
        try:
            r = httpx.request(method, url, headers=h, timeout=8.0)
            ok = r.status_code < 400
            detail = f"{r.status_code} {r.text[:100]}"
        except Exception as exc:
            ok = False
            detail = f"EXC {exc}"
        mark = "OK" if ok else "FAIL"
        if not ok:
            fails.append(f"{name}({path})")
        print(f"{mark:4s}  {name:16s} {detail}")

    # modules 清单专项
    try:
        r = httpx.get(base + "/api/v1/modules", headers=h, timeout=8.0)
        ids = sorted(m.get("id", "?") for m in r.json().get("modules", []))
        print(f"\nmodules ({len(ids)}): {', '.join(ids)}")
        for need in ("health", "calendar", "finance"):
            if need not in ids:
                print(f"WARN: modules 缺 {need}")
    except Exception as exc:
        print(f"FAIL modules list: {exc}")

    print("---")
    if fails:
        print(f"FAILED {len(fails)}: {', '.join(fails)}")
        return 1
    print("ALL API CHECKS OK（浏览器真机项仍归 Qoder/hermes/主人）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
