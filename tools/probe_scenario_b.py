#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T25 场景 B · 生产验收探针（登录后机器侧）。

覆盖：调度状态 / 手动 tick / 提醒日志 / 可选 bridge notify。
用法：
  ALONGSIDE_TOKEN=xxx python tools/probe_scenario_b.py --base http://192.0.2.10:18080
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=(os.environ.get("ALONGSIDE_BASE") or os.environ.get("LIFEOS_BASE")) or "http://192.0.2.10:18080")
    ap.add_argument("--token", default=(os.environ.get("ALONGSIDE_TOKEN") or os.environ.get("LIFEOS_TOKEN")) or "")
    ap.add_argument("--tick", action="store_true", help="POST reminders/tick")
    args = ap.parse_args()
    if not args.token:
        print("FAIL: 需要 ALONGSIDE_TOKEN")
        return 2
    base = args.base.rstrip("/")
    h = {"Authorization": f"Bearer {args.token}"}
    fails = []

    def check(name: str, method: str, path: str, **kw) -> None:
        try:
            r = httpx.request(method, base + path, headers=h, timeout=10.0, **kw)
            ok = r.status_code < 400
            print(f"{'OK' if ok else 'FAIL'}  {name:28s} {r.status_code} {r.text[:100]}")
        except Exception as exc:
            ok = False
            print(f"FAIL  {name:28s} EXC {exc}")
        if not ok:
            fails.append(name)

    check("reminder scheduler", "GET", "/api/v1/calendar/reminders/scheduler")
    check("reminder logs", "GET", "/api/v1/calendar/reminders/logs?limit=10")
    check("calendar health", "GET", "/api/v1/calendar/health")
    if args.tick:
        check("reminder tick", "POST", "/api/v1/calendar/reminders/tick")
    print("---")
    if fails:
        print("FAILED:", ", ".join(fails))
        return 1
    print("场景 B 机器侧 OK（桌面 Toast 仍需本机桥 0.2.0 + ENABLED/手动 tick）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
