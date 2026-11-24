#!/usr/bin/env python
"""生产健康自检（补 probe.py 未覆盖的运行时 / 安全 / PWA 判据）。

为什么单独一个脚本（而不并进 tools/accept_probe/probe.py）：
- probe.py 是 L2 验收正本（Qoder 领地）+ 其 expected.json 正被 hermes 的 E 卡改动中
  → 新脚本**零侵入、零撞车**，只读生产、不改任何契约文件。
- 覆盖的是 **2026-09-23 新增的三批能力**，probe 的既有 suite 覆盖不到：
    · T29 Web Push（push 插件 /health + VAPID 配置态）
    · F2 SSE 入场券（事件流不再裸奔：无票 401 / 伪票 401）
    · F3 订阅源头收窄（垃圾 endpoint 被白名单拒）
    · PWA 三件套（manifest mime / SW / 图标）+ 断网壳前提
    · 线上包内容核验（文案是否真进 JS 包）+ 首屏 gzip（X01 契约 < 250KB）
    · 旧 IP:18080 停用（主人「IP 直连设为失效」）

用法：
    python tools/prod_health_check.py                     # 默认查生产
    python tools/prod_health_check.py --base-url http://127.0.0.1:18000
    python tools/prod_health_check.py --json out.json     # 结果落盘
退出码：0 = 全部 PASS；1 = 有 FAIL（CI/部署后可直接用）。
零第三方依赖（urllib + gzip + json）。
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://life.example.com:8443"
LEGACY_IP = "http://192.0.2.10:18080"
# 五页签文案（分页件是否真进线上包）——文案写在包里是「渲染出来」的必要条件
TAB_MARKERS = ("插件能力", "内核基础", "网页入口", "手动导入", "AI 工具")
ENTRY_RE = re.compile(r"assets/(index-[\w-]+\.js)")
CHUNK_RE = re.compile(r"assets/([\w-]+\.js)")
GZIP_BUDGET_KB = 250.0  # X01 契约：首屏 gzip < 250KB


def fetch(url: str, *, method: str = "GET", body: bytes | None = None,
          timeout: int = 20) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, method=method, data=body)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:  # 4xx/5xx 也是有效观测
        return e.code, e.read(), dict(e.headers or {})
    except Exception as e:  # noqa: BLE001 — 连不上（如旧 IP 已停用）本身就是要观测的结果
        return 0, str(e).encode(), {}



def selfcheck() -> int:
    """反向自证：起本地假生产，验「坏的一定红」——特别是**状态码对、内容不对**。

    ★ 2026-11-24（汐瑶）：这道门禁全是网络检查，没法喂纯数据，所以直接起
      一个 `http.server` 假生产，用 subprocess 调自己。钉两条：

      ① 全 500 ⇒ 必须 exit 1（瞎了的最直白形态）
      ② **全 200 但内容是垃圾 ⇒ 也必须 exit 1**
         —— 这条更重要：今天已经吃过「状态码当结论」的亏
            （`/sight/now` 数据源失败也回 200，只是把失败写进 degraded）。
            门禁如果只看 `st == 200`，就会在生产已经坏掉时欢快地报绿。

    局限（如实写明）：**不验「真绿场景」**——那需要完整模拟
    PWA / push / SSE / 包内容，成本高于收益。真绿靠真生产跑一次。
    """
    import http.server
    import subprocess
    import threading

    def run_against(handler_cls: type) -> subprocess.CompletedProcess:
        srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
        port = srv.server_address[1]
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            return subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--base-url", f"http://127.0.0.1:{port}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        finally:
            srv.shutdown()

    # ── ① 全 500 ────────────────────────────────────────────
    class All500(http.server.BaseHTTPRequestHandler):
        def _bad(self) -> None:
            self.send_error(500)

        do_GET = do_POST = _bad  # type: ignore[assignment]

        def log_message(self, *a: object) -> None:  # noqa: D102
            pass

    r = run_against(All500)
    checks = [
        ("① 全 500 ⇒ 必须 exit 1", r.returncode == 1),
        ("① 输出里必须出现 FAIL", "FAIL" in r.stdout),
    ]

    # ── ② 全 200 但内容是垃圾（状态码对、内容不对）─────────
    class All200Garbage(http.server.BaseHTTPRequestHandler):
        def _fake(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html>totally fine, trust me</html>")

        do_GET = do_POST = _fake  # type: ignore[assignment]

        def log_message(self, *a: object) -> None:  # noqa: D102
            pass

    r2 = run_against(All200Garbage)
    checks.append(
        ("② 全 200 但内容不对 ⇒ 也必须 exit 1（不许只看状态码）", r2.returncode == 1)
    )
    checks.append(
        (
                    '② healthz 的判据必须真的是「含 "ok":true」而不是「HTTP 200」',
                    "healthz 200" in r2.stdout and "FAIL" in r2.stdout,
                )
    )

    print("prod-health 门禁自检（反向验证）")
    ok = True
    for name, passed in checks:
        print(f"  {'✓' if passed else '✗'} {name}")
        ok = ok and passed
    print()
    print("结论：" + ("✓ 自检通过 —— 坏的一定红（含「200 但内容是垃圾」）" if ok else "✗ 自检失败 —— 门禁本身坏了"))
    print("局限：真绿场景需真生产，本自检不覆盖。")
    return 0 if ok else 1


def main() -> int:
    if "--selfcheck" in sys.argv[1:]:
        return selfcheck()
    ap = argparse.ArgumentParser(description="生产健康自检（新增判据集）")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    checks: list[tuple[str, bool, str]] = []

    def ok(name: str, passed: bool, detail: str) -> None:
        checks.append((name, passed, detail))

    # ── ① 基线 ────────────────────────────────────────────────
    st, body, _ = fetch(f"{base}/healthz")
    ok("healthz 200", st == 200 and b'"ok":true' in body, f"HTTP {st}")

    st, html, _ = fetch(f"{base}/")
    m = ENTRY_RE.search(html.decode("utf-8", "ignore"))
    entry = m.group(1) if m else ""
    ok("首页可达 + 入口可解析", st == 200 and bool(entry), f"HTTP {st} entry={entry or 'NOT_FOUND'}")

    # ── ② PWA 三件套 ─────────────────────────────────────────
    st, mani, hdr = fetch(f"{base}/manifest.webmanifest")
    ct = hdr.get("Content-Type", "")
    ok("PWA manifest + mime", st == 200 and "manifest+json" in ct, f"HTTP {st} ct={ct}")

    st, sw, _ = fetch(f"{base}/sw.js")
    sw_txt = sw.decode("utf-8", "ignore")
    ok("SW 可访问", st == 200, f"HTTP {st} len={len(sw)}")
    ok("SW 含 fetch（离线壳前提）", 'addEventListener("fetch"' in sw_txt, "ok" if 'addEventListener("fetch"' in sw_txt else "缺 fetch 监听")

    st, icon, _ = fetch(f"{base}/icons/icon-192.png")
    ok("PWA 图标 192", st == 200, f"HTTP {st}")

    # ── ③ T29 push 插件 ──────────────────────────────────────
    st, ph, _ = fetch(f"{base}/api/v1/push/health")
    try:
        pj = json.loads(ph)
    except Exception:  # noqa: BLE001
        pj = {}
    ok("push /health 200", st == 200, f"HTTP {st}")
    ok("push vapid_ready", bool(pj.get("vapid_ready")), f"vapid_ready={pj.get('vapid_ready')}")
    ok("push pywebpush 已装", bool(pj.get("pywebpush_installed")), f"installed={pj.get('pywebpush_installed')}")

    st, _, _ = fetch(f"{base}/api/v1/push/vapid-public-key")
    ok("push 公钥可下发", st == 200, f"HTTP {st}")

    # ── ④ F2 SSE 入场券（事件流不再裸奔）─────────────────────
    st, _, _ = fetch(f"{base}/api/v1/events/subscribe")
    ok("F2 无票订阅被拒 401", st == 401, f"HTTP {st}")
    st, _, _ = fetch(f"{base}/api/v1/events/subscribe?ticket=garbage")
    ok("F2 伪票被拒 401", st == 401, f"HTTP {st}")

    # ── ⑤ F3 订阅源头收窄（垃圾 endpoint 白名单拒）────────────
    payload = json.dumps({
        "endpoint": "https://evil.example.com/probe",
        "keys": {"p256dh": "BKprodhealthcheck123", "auth": "authtoken123"},
    }).encode()
    st, rb, _ = fetch(f"{base}/api/v1/push/subscribe", method="POST", body=payload)
    ok("F3 垃圾 endpoint 被拒", st in (400, 422), f"HTTP {st}（期望 400/422）")

    # ── ⑥ 线上包内容核验 + 首屏 gzip（X01）──────────────────
    if entry:
        st, js, _ = fetch(f"{base}/assets/{entry}")
        js_txt = js.decode("utf-8", "ignore")
        missing = [t for t in TAB_MARKERS if t not in js_txt]
        # 分页件可能在懒加载 chunk 里 → 抓入口引用的 chunk 再查
        if missing:
            refs = {c for c in CHUNK_RE.findall(js_txt)} - {entry}
            for name in sorted(refs)[:40]:
                s2, c2, _ = fetch(f"{base}/assets/{name}")
                if s2 == 200:
                    js_txt += c2.decode("utf-8", "ignore")
            missing = [t for t in TAB_MARKERS if t not in js_txt]
        ok("五页签文案在线上包", not missing, f"缺={missing}" if missing else "全部命中（入口+chunk 全覆盖）")
        gz_kb = len(gzip.compress(js)) / 1024
        ok(f"首屏 gzip < {GZIP_BUDGET_KB}KB（X01）", gz_kb < GZIP_BUDGET_KB, f"{gz_kb:.1f} KB")

    # ── ⑦ 旧 IP 直连应已停用（主人）───────────────────────
    # ★ 实测校准（2026-09-23）：18080 的现状是 **HTTP 502**（nginx 仍在监听、upstream 已摘），
    #   并非"连接失败"—— 两者都表示"直连不可用"，故判据取「连接失败 或 5xx」。
    st, _, _ = fetch(LEGACY_IP, timeout=8)
    ok("旧 IP:18080 不可用", st == 0 or st >= 500,
       f"HTTP {st}（0=连接失败 / 5xx=已摘 upstream）")

    # ── 输出 ────────────────────────────────────────────────
    width = max(len(c[0]) for c in checks) + 2
    passed = sum(1 for _, p, _ in checks if p)
    for name, p, detail in checks:
        print(f"{'✅' if p else '❌'} {name.ljust(width)} {detail}")
    print(f"\n{'ALL_GREEN ✅' if passed == len(checks) else 'HAS_FAIL ❌'}  "
          f"{passed}/{len(checks)} 项通过   base={base}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(
                {"base_url": base, "passed": passed, "total": len(checks),
                 "checks": [{"name": n, "pass": p, "detail": d} for n, p, d in checks]},
                f, ensure_ascii=False, indent=2,
            )
        print(f"结果已落盘：{args.json}")

    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
