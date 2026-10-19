#!/usr/bin/env python3
"""API/MCP 注释覆盖率自检（★ 2026-09-28 · 云昔 · 主人）。

为什么有它：
    主人「确保所有的 MCP 工具、API 接口，都带上注释和解释，**以防 AI 调用的时候
    所有东西的参数全部都一样而抓瞎**」。
    但"有没有注释"不能靠感觉 —— 本脚本把它变成一个**可测量、可追踪**的指标：
      · 端点解释：route 函数的 docstring（FastAPI 暴露为 description；`summary` 单独
        不可信 —— 无 docstring 时 FastAPI 会用函数名自动生成英文标题如 "Update Item"）
        —— 它会被 MCP 桥接层用作**工具描述**（见 modules/mcp/mcp_server._tool_description）
      · 参数解释：Pydantic `Field(description=...)` / Query(description=...)
        —— 它会进 MCP 的 inputSchema，AI 靠它认出每个字段是什么意思

用法：
    python tools/check_api_docs.py                      # 扫公网生产
    python tools/check_api_docs.py --base http://127.0.0.1:18000
    python tools/check_api_docs.py --fail-under 90      # 覆盖率低于阈值则退出码非 0（给 CI 用）

退出码：0 = 通过（或未给 --fail-under）；1 = 低于阈值；2 = 拉不到 openapi
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

MODULES = [
    "agents", "ai-chat", "auth", "calendar", "catalog", "course", "dashboard", "diary",
    "docs", "export", "finance", "habits", "health", "mcp", "notes", "persona", "plugins",
    "push", "query", "review", "sidebar", "summary", "todo", "web", "countdown", "pi-agent",
]


def _get(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def scan(base: str) -> dict:
    total_ops = ops_nodoc = 0
    total_prm = prm_nodesc = 0
    per_module: list[tuple] = []
    # ★ 2026-09-29（云昔 · 因 pi-agent 未挂载事件）：
    #   **读不到 openapi 的模块绝不能静默跳过** —— 否则"死掉的模块从分母里消失"，
    #   覆盖率反而更好看（pi-agent 全路由 404 时，本脚本曾报它 100% ✗）。
    #   读不到 = **失败**，单独列出并让退出码非 0。
    unreachable: list[str] = []
    for mod in MODULES:
        spec = _get(f"{base.rstrip('/')}/api/{mod}/openapi.json")
        if not spec:
            unreachable.append(mod)          # ★ 不跳过：记下来当失败报
            continue
        schemas = (spec.get("components") or {}).get("schemas") or {}

        def deref(node):
            if isinstance(node, dict) and isinstance(node.get("$ref"), str):
                return schemas.get(node["$ref"].split("/")[-1], {})
            return node if isinstance(node, dict) else {}

        m_ops = m_nodoc = m_prm = m_nodesc = 0
        missing: list[str] = []
        for path, ops in (spec.get("paths") or {}).items():
            if not isinstance(ops, dict):
                continue
            for method, op in ops.items():
                if not isinstance(op, dict):
                    continue
                m_ops += 1
                # 只认 docstring 派生：`description`，或**含中文**的 summary
                #（FastAPI 无 docstring 时会用函数名生成英文标题，那不是解释）
                _doc = (op.get("description") or "").strip()
                if not _doc:
                    _s = (op.get("summary") or "").strip()
                    _doc = _s if any("\u4e00" <= c <= "\u9fff" for c in _s) else ""
                if not _doc:
                    m_nodoc += 1
                    missing.append(f"{method.upper()} {path}（无端点解释）")
                for prm in op.get("parameters") or []:
                    m_prm += 1
                    if not deref(prm).get("description"):
                        m_nodesc += 1
                        missing.append(f"{method.upper()} {path} · 参数 {deref(prm).get('name')}")
                body = ((op.get("requestBody") or {}).get("content") or {}).get("application/json")
                if isinstance(body, dict):
                    sch = deref(body.get("schema") or {})
                    for k, v in (sch.get("properties") or {}).items():
                        m_prm += 1
                        if not deref(v).get("description"):
                            m_nodesc += 1
                            missing.append(f"{method.upper()} {path} · 字段 {k}")
        total_ops += m_ops
        ops_nodoc += m_nodoc
        total_prm += m_prm
        prm_nodesc += m_nodesc
        per_module.append((mod, m_ops, m_nodoc, m_prm, m_nodesc, missing))

    return {
        "total_ops": total_ops, "ops_nodoc": ops_nodoc,
        "total_prm": total_prm, "prm_nodesc": prm_nodesc,
        "per_module": per_module, "unreachable": unreachable,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://life.example.com:8443", help="alongside 基址")
    ap.add_argument("--fail-under", type=float, default=None, help="参数解释覆盖率下限（%%）")
    ap.add_argument("--show-missing", action="store_true", help="逐条列出缺解释的参数")
    ap.add_argument("--allow-unreachable", action="store_true",
                    help="允许有读不到的模块（默认：有则退出码 3）")
    args = ap.parse_args()

    r = scan(args.base)
    if not r["total_ops"]:
        print(f"✗ 拉不到任何 openapi（base={args.base}）", file=sys.stderr)
        return 2

    if r["unreachable"]:
        print(f"⚠️  ⚠️  ⚠️  **{len(r['unreachable'])} 个模块读不到 openapi（插件可能未挂载）** ⚠️  ⚠️  ⚠️")
        print("    " + ", ".join(r["unreachable"]))
        print("    ⇒ 它们**不在下面的分母里**，所以数字会虚高 —— 请先修挂载再看覆盖率 ✗")
        print()
    ops_pct = 100 * (r["total_ops"] - r["ops_nodoc"]) / max(r["total_ops"], 1)
    prm_pct = 100 * (r["total_prm"] - r["prm_nodesc"]) / max(r["total_prm"], 1)
    print(f"端点解释覆盖率 : {ops_pct:5.1f}%（{r['total_ops'] - r['ops_nodoc']}/{r['total_ops']}）"
          "   ← 会作为 MCP 工具描述")
    print(f"参数解释覆盖率 : {prm_pct:5.1f}%（{r['total_prm'] - r['prm_nodesc']}/{r['total_prm']}）"
          "   ← 会进 inputSchema")
    print()
    print("各模块缺口（模块 | 端点数/无解释 | 参数数/无解释）:")
    for mod, o, nd, p, npx, missing in sorted(r["per_module"], key=lambda x: -x[4]):
        flag = "  " if (nd == 0 and npx == 0) else "★ "
        print(f"  {flag}{mod:12s} {o:3d}/{nd:<3d}   {p:4d}/{npx:<4d}")
        if args.show_missing and missing:
            for line in missing[:20]:
                print(f"        - {line}")

    if r["unreachable"] and not args.allow_unreachable:
        print(f"\n✗ 有 {len(r['unreachable'])} 个模块读不到 —— 覆盖率不可信，先修挂载")
        return 3
    if args.fail_under is not None and prm_pct < args.fail_under:
        print(f"\n✗ 参数解释覆盖率 {prm_pct:.1f}% < 阈值 {args.fail_under}%")
        return 1
    print("\n✓ 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
