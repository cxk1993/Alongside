#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiMo 授权部署 · 后端增量上传与验证（只读/可选执行，需显式 --apply）。

默认 dry-run：列出将上传的本地文件与远端路径，不改服务器。
--apply 时按runbook：备份远端 → scp →（不自动重启，打印重启命令）→ 可选 --verify。

用法（PowerShell）：
  $env:SSH_KEY = "$env:TEMP\\deploy-key.pem"
  python tools/deploy_backend_increment.py --dry-run
  python tools/deploy_backend_increment.py --apply --files modules/todo/health_link.py,modules/todo/router.py
  python tools/deploy_backend_increment.py --apply --verify

红线：只上传 --files 列出的相对 services/api 路径；不做 git restore/clean；不删生产数据。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "services" / "api"
HOST = (os.environ.get("ALONGSIDE_SSH_HOST") or os.environ.get("LIFEOS_SSH_HOST")) or "ubuntu@192.0.2.10"
REMOTE_API = "/home/ubuntu/alongside/services/api"
KEY_ENV = "SSH_KEY"


def _key() -> str:
    k = os.environ.get(KEY_ENV) or ""
    if not k or not Path(k).is_file():
        print(f"FAIL: 请设置 {KEY_ENV} 指向 SSH 私钥（ASCII 路径副本）")
        sys.exit(2)
    return k


def _ssh(key: str, cmd: str) -> int:
    full = ["ssh", "-i", key, "-o", "StrictHostKeyChecking=no", HOST, cmd]
    print("+", " ".join(full[:4]), "…")
    return subprocess.call(full)


def _scp(key: str, local: Path, remote_rel: str) -> int:
    remote = f"{HOST}:{REMOTE_API}/{remote_rel}"
    full = ["scp", "-i", key, "-o", "StrictHostKeyChecking=no", str(local), remote]
    print("+ scp", local.name, "→", remote_rel)
    return subprocess.call(full)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", default="", help="逗号分隔，相对 services/api 的路径")
    ap.add_argument("--apply", action="store_true", help="真正上传（默认 dry-run）")
    ap.add_argument("--verify", action="store_true", help="上传后 curl healthz/health")
    args = ap.parse_args()
    files = [f.strip() for f in args.files.split(",") if f.strip()]
    if not files:
        print("FAIL: 需要 --files services/api 相对路径列表")
        return 2
    plan = []
    for rel in files:
        local = API / rel
        if not local.is_file():
            print(f"FAIL: 本地不存在 {local}")
            return 1
        plan.append((local, rel))
    print("=== 部署计划 ===")
    for local, rel in plan:
        print(f"  {local}  →  {REMOTE_API}/{rel}")
    if not args.apply:
        print("DRY-RUN 结束（未 --apply，服务器未改动）")
        print("重启命令（apply 后自行或由脚本提示执行）:")
        print("  ssh … 'kill <pid>'; ssh … 'cd ... && setsid ... uvicorn ...'")
        return 0
    key = _key()
    ts = time.strftime("%Y%m%d-%H%M%S")
    for local, rel in plan:
        backup = f"{REMOTE_API}/{rel}.bak-{ts}"
        if _ssh(key, f"cp {REMOTE_API}/{rel} {backup}") != 0:
            print("WARN: 备份失败或远端尚无旧文件，继续上传", rel)
        if _scp(key, local, rel) != 0:
            print("FAIL scp", rel)
            return 1
    print("=== 上传完成 ===")
    print("请分两会话重启 Alongside API（kill / setsid），然后 --verify 或手工探活。")
    if args.verify:
        base = (os.environ.get("ALONGSIDE_BASE") or os.environ.get("LIFEOS_BASE")) or "http://192.0.2.10:18080"
        try:
            import httpx

            r = httpx.get(f"{base}/healthz", timeout=8.0)
            print("healthz", r.status_code, r.text[:80])
        except Exception as exc:  # noqa: BLE001
            print("verify FAIL", exc)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
