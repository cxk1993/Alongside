#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T25/场景 B · 本机桥探活与通知探测脚本（只读 + 可选测通知）。

用法（本机，PowerShell）：
  python tools/probe_bridge_t25.py            # 只探 healthz / capabilities
  python tools/probe_bridge_t25.py --notify   # 额外发一条 channel=log 测试通知

环境：BRIDGE_PSK 必填（勿写进本文件）；BRIDGE_URL 默认 http://127.0.0.1:8790
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import uuid

import httpx

DEFAULT_URL = "http://127.0.0.1:8790"


def sign(psk: str, method: str, path: str, ts: int, nonce: str, body: bytes = b"") -> str:
    msg = f"{method.upper()}|{path}|{ts}|{nonce}|{body.decode('utf-8', 'replace')}"
    return hmac.new(psk.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def headers(psk: str, method: str, path: str, body: bytes = b"") -> dict[str, str]:
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    return {
        "X-Bridge-PSK": psk,
        "X-Bridge-Ts": str(ts),
        "X-Bridge-Nonce": nonce,
        "X-Bridge-Sign": sign(psk, method, path, ts, nonce, body),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--url", default=os.environ.get("BRIDGE_URL") or DEFAULT_URL)
    args = ap.parse_args()
    psk = os.environ.get("BRIDGE_PSK") or ""
    if not psk:
        print("FAIL: 未设置 BRIDGE_PSK")
        return 2
    base = args.url.rstrip("/")

    try:
        r = httpx.get(f"{base}/healthz", timeout=3.0)
        print(f"GET /healthz -> {r.status_code} {r.text[:120]}")
    except Exception as exc:
        print(f"FAIL: 桥不可达 {base}: {exc}")
        return 1

    path = "/bridge/healthz"
    try:
        r2 = httpx.get(base + path, headers=headers(psk, "GET", path), timeout=5.0)
        print(f"GET {path} -> {r2.status_code}")
        data = r2.json() if r2.status_code == 200 else {}
        caps = data.get("capabilities")
        print(f"capabilities={caps} version={data.get('version')}")
        if r2.status_code == 200 and caps and "notify" in caps:
            print("OK: 桥 0.2.x 已具备 notify 能力")
        elif r2.status_code == 200:
            print("WARN: 桥在跑但 capabilities 无 notify —— 可能仍是旧版，需重启 lifeos-bridge")
    except Exception as exc:
        print(f"FAIL: 鉴权探测 {exc}")
        return 1

    if args.notify:
        body = json.dumps(
            {"title": "Alongside 探测", "body": "T25 channel=log 测试", "channel": "log"},
            ensure_ascii=False,
        ).encode("utf-8")
        npath = "/bridge/notify"
        try:
            r3 = httpx.post(
                base + npath,
                headers={**headers(psk, "POST", npath, body), "Content-Type": "application/json"},
                content=body,
                timeout=8.0,
            )
            print(f"POST {npath} -> {r3.status_code} {r3.text[:200]}")
        except Exception as exc:
            print(f"FAIL: notify {exc}")
            return 1
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
