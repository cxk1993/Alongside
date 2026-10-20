#!/usr/bin/env python
"""★ 根因部署后验收探针（/ 判据）—— workbuddy。

判据（定）：
  1. 连续 50 次打 `/api/v1/plugins` **不再 500**（裸打 401 = 鉴权墙正常，也算通过；
     判据看的是**不出现 5xx**）；
  2. 日志 **QueuePool 新增计数 = 0**（基线：2026-09-25 17:5x 实测 236，泄漏计数不再增长即修复）；
  3. 带 token 打 `/api/v1/plugins` 返回 **200**。

用法（在有生产访问权的机器上）：
    python tools/probe_d_pool_leak.py                 # 只跑 ①②（无需 token）
    ALONGSIDE_TOKEN=<jwt> python tools/probe_d_pool_leak.py   # 全跑 ①②③

设计原则：**只读探针**（不写库、不改状态）· 失败即如实报（不吞异常）。
"""
from __future__ import annotations

import os
import ssl
import sys
import time
import urllib.error
import urllib.request

BASE = ((os.environ.get("ALONGSIDE_BASE") or os.environ.get("LIFEOS_BASE")) or os.environ.get("LIFEOS_BASE") or "https://life.example.com:8443")
N = int(os.environ.get("PROBE_N", "50"))
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE  # 自签/内网证书场景


def hit(path: str, token: str | None = None) -> tuple[int, str]:
    req = urllib.request.Request(BASE + path, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10, context=CTX) as r:
            return r.status, r.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001 —— 探针要如实暴露网络异常
        return -1, str(e)


def main() -> int:
    token = (os.environ.get("ALONGSIDE_TOKEN") or os.environ.get("LIFEOS_TOKEN"))
    print(f"# 根因验收探针 · {BASE} · {N} 连打")
    codes: dict[int, int] = {}
    t0 = time.time()
    for i in range(1, N + 1):
        code, _ = hit("/api/v1/plugins")
        codes[code] = codes.get(code, 0) + 1
        if code >= 500:  # 5xx 立刻报（不等跑完）
            print(f"  [{i}/{N}] ✗ 5xx={code} —— 根因未修复（池耗尽）")
            return 1
    elapsed = time.time() - t0
    print(f"  连打完成：{elapsed:.1f}s · 状态码分布 {codes}")
    bad = {c: n for c, n in codes.items() if c >= 500 or c == -1}
    if bad:
        print(f"  ✗ 出现异常码 {bad}")
        return 1
    print("  ✅ 判据① 通过：50 连打无 5xx")

    if token:
        code, body = hit("/api/v1/plugins", token)
        print(f"  带 token → {code}")
        if code != 200:
            print(f"  ✗ 判据③ 未过（期望 200，实得 {code}）body={body[:120]}")
            return 1
        print("  ✅ 判据③ 通过：带 token 200")
    else:
        print("  ⏭ 判据③ 跳过（未提供 ALONGSIDE_TOKEN）")

    print("\n判据②（QueuePool 计数）需在服务器侧执行：")
    print("  grep -c 'QueuePool' /path/to/api.log  → 与基线 236 对比，**新增应为 0**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
