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
from pathlib import Path

# ★ 2026-11-24（汐瑶）：模块清单不再硬编码 —— 从 /readyz 动态取。
#
#   原来的 `MODULES` 是手写的 26 项，写于 countdown 还是内置模块的年代。
#   后来 countdown 变成第三方插件（plugins/countdown，未装进 modules/），
#   而真正上线的 12 个模块（appearance / assets / collab / growth / isdu /
#   knowledge / packs / proposals / schedule / sight / study-hub / system）
#   一个都不在清单里 —— 于是这道门禁既冤枉了插件，又漏报了大半壁江山，
#   覆盖率分母永远只有 26 个模块，数字看着漂亮但不可信。
#
#   「模块有哪些」的唯一真相是运行时（/readyz 的 activated），不是硬编码。
def _discover_modules(base: str) -> list[str] | None:
    """从 /readyz 取当前激活的模块清单。取不到返回 None（调用方判红）。"""
    d = _get(f"{base.rstrip('/')}/readyz")
    if not d:
        return None
    mods = d.get("activated") or d.get("modules")
    return sorted(mods) if isinstance(mods, list) else None


def _modules_with_api() -> set[str]:
    """哪些模块**声明了后端 API**（manifest 里有 `api` 字段）。

    ★ 2026-11-24（汐瑶）：不是每个模块都有后端。
      growth / knowledge / schedule / system 是纯前端模块（只有 manifest + entry，
      没有 `api` 字段，后端 0 行代码）—— 它们本来就不该有 openapi.json，
      把「读不到 openapi」算成失败是**误报**，而且会逼着人给空模块造假路由。

      判据直接读 `services/api/modules/*/manifest.json` 的 `api` 字段
      （走文件而不是走 /api/plugins —— 后者要鉴权，门禁不该依赖 token）。
    """
    root = Path(__file__).resolve().parent.parent / "services" / "api" / "modules"
    out: set[str] = set()
    for mf in root.glob("*/manifest.json"):
        try:
            d = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("api"):
            out.add(d.get("id") or mf.parent.name)
    return out


def _get(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _count_module(spec: dict) -> tuple[int, int, int, int, list[str]]:
    """数一个模块的 openapi：端点数 / 无解释 / 参数数 / 参数无解释 + 缺失明细。

    ★ 2026-11-24（汐瑶）：从 scan() 里抽出来的 —— 为的是让 selfcheck 能喂**假数据**
      验证「无描述真会被逮到」。门禁最危险的失效是一直绿。
    """
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
    return m_ops, m_nodoc, m_prm, m_nodesc, missing


def scan(base: str) -> dict:
    total_ops = ops_nodoc = 0
    total_prm = prm_nodesc = 0
    per_module: list[tuple] = []
    # ★ 2026-09-29（云昔 · 因 pi-agent 未挂载事件）：
    #   **读不到 openapi 的模块绝不能静默跳过** —— 否则"死掉的模块从分母里消失"，
    #   覆盖率反而更好看（pi-agent 全路由 404 时，本脚本曾报它 100% ✗）。
    #   读不到 = **失败**，单独列出并让退出码非 0。
    #
    # ★ 2026-11-24（汐瑶）：清单改从 /readyz 动态取。
    #   取不到清单 = 连服务都没起来，直接判红（不能退回硬编码，那正是本门禁失效的原因）。
    unreachable: list[str] = []
    modules = _discover_modules(base)
    with_api = _modules_with_api()
    if not modules:
        return {
            "unreachable": ["<readyz 取不到模块清单>"],
            "fatal": "无法从 /readyz 发现模块清单 —— 服务未起或 /readyz 被壳吞掉（见 ISSUE-010）",
            "total_ops": 0, "ops_nodoc": 0, "total_prm": 0, "prm_nodesc": 0,
            "per_module": [],
        }
    for mod in modules:
        # ★ 2026-11-24（汐瑶）：只有声明了 API 的模块才该有 openapi.json。
        #   纯前端模块（growth / knowledge / schedule / system）读不到是**正常**，不是失败。
        if mod not in with_api:
            continue
        spec = _get(f"{base.rstrip('/')}/api/{mod}/openapi.json")
        if not spec:
            unreachable.append(mod)          # ★ 不跳过：记下来当失败报
            continue
        m_ops, m_nodoc, m_prm, m_nodesc, missing = _count_module(spec)
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


def selfcheck() -> int:
    """反向自证 —— 门禁最危险的失效是「一直绿」。

    ★ 2026-11-24 的真实教训：模块清单曾是**硬编码 26 项**（写于 countdown 还是
      内置模块的年代），冤枉 1 个已转第三方的插件、漏掉 12 个真实上线模块，
      于是分母永远是 26 —— **覆盖率虚报 100%，两个多月没人发现**。

    所以这里钉四条，各自注入一次失效，都必须报红：
      ① readyz 取不到模块清单 ⇒ 必须 fatal（**不许退回硬编码**）
      ② 只有声明了 api 的模块才该有 openapi（纯前端模块不该被算失败）
      ③ 清单来源必须真能读到「有 api 字段」的模块
      ④ 计数逻辑必须逮到「无描述」的端点 / 参数 / body 字段

    ★ 全程不碰网络、不碰真实文件（②读真 manifest，验的是「这函数自己没坏」）。
    """
    checks: list[tuple[str, bool]] = []

    # ① readyz 取不到 ⇒ fatal（不许静默降级到硬编码 —— 那正是旧病的来源）
    real = globals().get("_discover_modules")
    try:
        globals()["_discover_modules"] = lambda _base: None
        r = scan("http://selfcheck.invalid")
        checks.append(("readyz 取不到模块清单时必须 fatal", bool(r.get("fatal"))))
    finally:
        if real is not None:
            globals()["_discover_modules"] = real

    # ③ 清单来源：读真 manifest
    with_api = _modules_with_api()
    checks.append(("_modules_with_api 必须读到非空集合", len(with_api) > 0))
    checks.append(("必须含已知带 api 的模块（todo / docs）", {"todo", "docs"} <= with_api))

    # ② 纯前端模块（只有 manifest + entry、无 api 字段）不该被算成「读不到」
    checks.append(
        (
            "纯前端模块不得混入带 api 集合（growth / schedule / system）",
            not ({"growth", "schedule", "system"} & with_api),
        )
    )

    # ④ 计数逻辑：喂假 spec，三处缺失都必须被数出来
    fake = {
        "paths": {
            "/a": {"get": {"parameters": [{"name": "q"}], "description": "有解释"}},  # 参数缺
            "/b": {"post": {}},  # 端点缺
            "/c": {
                "put": {
                    "description": "有",
                    "requestBody": {
                        "content": {"application/json": {"schema": {"properties": {"x": {}}}}}
                    },
                }
            },  # body 字段缺
        }
    }
    o, nd, p, npx, miss = _count_module(fake)
    checks.append(("假 spec：端点数应为 3", o == 3))
    checks.append(("假 spec：必须数出 1 个无解释端点", nd == 1))
    checks.append(("假 spec：必须数出 2 处无描述参数/字段", npx == 2))
    checks.append(("假 spec：缺失明细至少 3 条", len(miss) >= 3))

    print("api-docs 门禁自检（反向验证）")
    ok = True
    for name, passed in checks:
        print(f"  {'✓' if passed else '✗'} {name}")
        ok = ok and passed
    print()
    print("结论：" + ("✓ 自检通过 —— 四条判据各自都能报红" if ok else "✗ 自检失败 —— 门禁本身坏了"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://life.example.com:8443", help="alongside 基址")
    ap.add_argument("--fail-under", type=float, default=None, help="参数解释覆盖率下限（%%）")
    ap.add_argument("--show-missing", action="store_true", help="逐条列出缺解释的参数")
    ap.add_argument("--allow-unreachable", action="store_true",
                    help="允许有读不到的模块（默认：有则退出码 3）")
    ap.add_argument("--selfcheck", action="store_true",
                    help="反向自证：四条判据各注入一次失效，都必须报红（不碰网络）")
    args = ap.parse_args()

    if args.selfcheck:
        return selfcheck()

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
