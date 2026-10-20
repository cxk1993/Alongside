#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ISSUE-006 Route A probe using stdlib urllib only.

用法：管理员口令经环境变量传入，勿硬编码（口令不落代码/历史）。
    ALONGSIDE_ADMIN_PASSWORD=<口令> python3 tools/probe_issue006_routeA.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://192.0.2.10:18080"


def req(method: str, path: str, token: str = "", body: dict | None = None):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=12) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, raw, dict(e.headers)
    except Exception as exc:
        return 0, f"EXC {exc}", {}


def main() -> int:
    fails: list[str] = []
    st, body, _ = req("GET", "/healthz")
    print(f"{'OK' if st==200 else 'FAIL':4s}  healthz  {st} {body[:80]}")
    if st != 200:
        fails.append("healthz")

    st, body, hdrs = req(
        "POST",
        "/api/v1/auth/login",
        body={"username": "admin", "password": ((os.environ.get("ALONGSIDE_ADMIN_PASSWORD") or os.environ.get("LIFEOS_ADMIN_PASSWORD")) or os.environ.get("LIFEOS_ADMIN_PASSWORD") or "")},
    )
    print(f"{'OK' if st==200 else 'FAIL':4s}  login  {st}")
    set_cookie = ""
    for k, v in hdrs.items():
        if k.lower() == "set-cookie":
            set_cookie = v
    secure = "secure" in set_cookie.lower()
    print(f"      Set-Cookie secure={secure} cookie={set_cookie[:140]}")
    if st != 200:
        fails.append("login")
        token = ""
        print("      body", body[:200])
    else:
        try:
            j = json.loads(body)
        except Exception:
            j = {}
        token = j.get("access_token") or j.get("token") or ""
        print(f"      token_len={len(token)} keys={list(j.keys())}")
    if secure:
        fails.append("cookie_secure_present")

    if token:
        st, body, _ = req("GET", "/api/v1/auth/refresh", token=token)
        print(f"{'OK' if st==200 else 'FAIL':4s}  refresh  {st}")
        if st != 200:
            fails.append("refresh")
        st, body, _ = req("GET", "/api/v1/auth/me", token=token)
        print(f"{'OK' if st==200 else 'FAIL':4s}  me  {st} {body[:80]}")
        if st != 200:
            fails.append("me")

    st, body, _ = req("GET", "/api/v1/modules", token=token)
    if st == 200:
        d = json.loads(body)
        ids = sorted(m.get("id") for m in d.get("modules", []))
        ok = d.get("count") == 17
        print(f"{'OK' if ok else 'FAIL':4s}  modules  count={d.get('count')}")
        print(f"      ids={ids}")
        if not ok:
            fails.append("modules_count")
    else:
        print(f"FAIL  modules  {st}")
        fails.append("modules")

    checks = [
        ("dashboard health", "/api/v1/dashboard/health"),
        ("review health", "/api/v1/review/health"),
        ("calendar health", "/api/v1/calendar/health"),
        ("reminder scheduler", "/api/v1/calendar/reminders/scheduler"),
        ("todo health", "/api/v1/todo/health"),
        ("habits health", "/api/v1/habits/health"),
        ("finance health", "/api/v1/finance/health"),
        ("finance scheduler", "/api/v1/finance/snapshots/scheduler"),
        ("notes health", "/api/v1/notes/health"),
        ("agents health", "/api/v1/agents/health"),
        ("web health", "/api/v1/web/health"),
        ("web entries", "/api/v1/web/entries"),
        ("docs health", "/api/v1/docs/health"),
        ("docs nodes", "/api/v1/docs/nodes"),
        ("persona health", "/api/v1/persona/health"),
        ("diary health", "/api/v1/diary/health"),
        ("health T21", "/api/v1/health/health"),
        ("health records", "/api/v1/health/records"),
        ("catalog", "/api/v1/catalog"),
        ("mcp health", "/api/v1/mcp/health"),
    ]
    for name, path in checks:
        st, body, _ = req("GET", path, token=token)
        ok = 0 < st < 400
        if not ok:
            fails.append(name)
        print(f"{'OK' if ok else 'FAIL':4s}  {name:20s} {st} {body[:70]}")

    print("\n=== PROBE SUMMARY ===")
    if fails:
        print("FAILS:", fails)
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
