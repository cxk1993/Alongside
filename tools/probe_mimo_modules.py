#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Alongside 全模块「随心所欲」· MiMo 名下模块 API 巡检探针。

覆盖：健康 T21 / 日程提醒 T25 / 理财调度 A2 / 模块清单（确认 health 已登记）。
不替代 Qoder 浏览器深验；用于 T30 上线后快速确认「我的模块没半残」。

用法：
  set ALONGSIDE_TOKEN=eyJ...
  python tools/probe_mimo_modules.py --base http://192.0.2.10:18080
  python tools/probe_mimo_modules.py --base http://127.0.0.1:8000 --token $TOKEN
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import httpx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=(os.environ.get("ALONGSIDE_BASE") or os.environ.get("LIFEOS_BASE")) or "http://192.0.2.10:18080")
    ap.add_argument("--token", default=(os.environ.get("ALONGSIDE_TOKEN") or os.environ.get("LIFEOS_TOKEN")) or "")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    token = args.token
    if not token:
        print("FAIL: 需要 --token 或环境变量 ALONGSIDE_TOKEN")
        return 2
    h = {"Authorization": f"Bearer {token}"}
    results: list[tuple[str, bool, str]] = []

    def check(name: str, method: str, path: str, **kw: Any) -> None:
        url = base + path
        try:
            r = httpx.request(method, url, headers=h, timeout=8.0, **kw)
            ok = r.status_code < 400
            detail = f"{r.status_code} {r.text[:120]}"
        except Exception as exc:
            ok = False
            detail = f"EXC {exc}"
        results.append((name, ok, detail))
        print(f"{'OK' if ok else 'FAIL'}  {name:28s} {detail}")

    check("healthz", "GET", "/healthz")
    check("modules 列表含 health", "GET", "/api/v1/modules")
    check("T21 health 探针", "GET", "/api/v1/health/health")
    check("T21 manifest", "GET", "/api/v1/health/manifest")
    check("T21 records 列表", "GET", "/api/v1/health/records")
    check("T25 reminder scheduler", "GET", "/api/v1/calendar/reminders/scheduler")
    check("T25 reminder logs", "GET", "/api/v1/calendar/reminders/logs")
    check("A2 finance scheduler", "GET", "/api/v1/finance/snapshots/scheduler")
    check("calendar health", "GET", "/api/v1/calendar/health")
    check("finance health", "GET", "/api/v1/finance/health")

    # modules 列表专项：health 是否注册
    try:
        r = httpx.get(base + "/api/v1/modules", headers=h, timeout=8.0)
        ids = [m.get("id") for m in r.json().get("modules", [])]
        has = "health" in ids
        results.append(("modules 含 health.id", has, f"ids={ids}"))
        print(f"{'OK' if has else 'FAIL'}  modules 含 health.id                 {ids}")
    except Exception as exc:
        print(f"FAIL  modules 含 health.id                 {exc}")

    failed = [n for n, ok, _ in results if not ok]
    print("---")
    print(f"SUMMARY: {len(results) - len(failed)}/{len(results)} OK")
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    print("MiMo 名下模块 API 侧全部可访问（浏览器深验仍归 Qoder/hermes）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
