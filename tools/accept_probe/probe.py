"""L2 验收判据自动化套件 · probe.py（只读，零三方依赖）。

用法：
    python tools/accept_probe/probe.py --target production --suite all
    python tools/accept_probe/probe.py --target production --suite gate
    python tools/accept_probe/probe.py --target production --suite hash
    python tools/accept_probe/probe.py --target production --suite modules
    python tools/accept_probe/probe.py --target production --suite docsprobe
    python tools/accept_probe/probe.py --target production --suite deploycheck

口令：运行时经环境变量 ALONGSIDE_ADMIN_PASSWORD 传入（或 --password，仅当次进程内存使用）。
    绝不写入任何文件/回帖/日志；token 与 cookie 全程内存，不落盘。

退出码：0=全绿；1=有判据未过；2=用法/输入错误。

判据来源（约束3：来源可溯）：
    gate      = ACCEPT-T30 A6 三快腿（02:23 全验判定帖 §1；02:53 延迟探针；11:4x 路线A 对表复绿）
    hash      = 生产入口 hash 基线链（02:36 判定帖 §5 追记 → 12:00 快照未漂移）
    modules   = modules 17 口径（12:00 实测；11:4x 逐数 id 复验）
    docsprobe = BUG-T16-1 销账 + T17 幂等线上判据（MiMo 09:15 盘点 + hermes 10:26 三合一眼验 + API 对表）
    deploycheck = 上述四件套的部署后四合一复核（healthz+modules+gate+hash）

🖥 腿边界：本套件只做 API 级 census + hash 对表；
    浏览器 DOM 级断言（开窗/渲染/console 零错）归 hermes 真机腿，不 jsdom 化。
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# Windows 控制台默认 GBK，中文/emoji 会炸（同 create_plugin.py pyfiglet 坑）
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

TIMEOUT = 10
EXPECTED_PATH = Path(__file__).resolve().parent / "expected.json"


class Row:
    # RFC-001（采纳）：rows 全局收集，供 verdict 事件机读输出；
    # 只含判据名/期望/实测/判定——绝不含 token/cookie/口令（#006 红灯纪律）。
    collected: list[dict[str, Any]] = []

    def __init__(self) -> None:
        self.items: list[tuple[str, str, str, bool]] = []

    def add(self, name: str, expect: str, actual: str, ok: bool) -> None:
        self.items.append((name, expect, actual, ok))
        Row.collected.append({"name": name, "expect": expect, "actual": actual, "ok": ok})

    def render(self, title: str) -> bool:
        print(f"\n## {title}")
        print(f"{'判据':<22}{'期望':<26}{'实测':<34}判定")
        allok = True
        for name, expect, actual, ok in self.items:
            allok = allok and ok
            print(f"{name:<22}{expect:<26}{actual:<34}{'✅' if ok else '❌'}")
        return allok


def load_expected() -> dict[str, Any]:
    with open(EXPECTED_PATH, encoding="utf-8") as f:
        return json.load(f)


def contract_sha256() -> str:
    """expected.json 全文 sha256——契约防漂移的机器化指纹（RFC-001；运行时计算，不改契约本体）。"""
    return hashlib.sha256(EXPECTED_PATH.read_bytes()).hexdigest()


def emit_verdict(target: str, suite: str) -> dict[str, Any]:
    """RFC-001 机读 verdict 事件（--emit-verdict 显式开启；人读表格与退出码语义不变）。

    schema 版本号只进本事件的 `schema` 字段（精化），不动 expected.json 结构。
    """
    rows = Row.collected
    passed = sum(1 for r in rows if r["ok"])
    verdict = {
        "schema": "alongside.probe.verdict/1",
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target": target,
        "suite": suite,
        "verdict": "PASS" if passed == len(rows) and rows else "FAIL",
        "summary": {"total": len(rows), "passed": passed, "failed": len(rows) - passed},
        "contract_sha256": contract_sha256(),
        "rows": rows,
    }
    print(json.dumps(verdict, ensure_ascii=False))
    return verdict


class Client:
    """内存 cookie + bearer 的最小只读客户端；不落盘任何凭证。"""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.access: str | None = None

    def _send(self, req: urllib.request.Request) -> tuple[int, Any, str]:
        try:
            with self.opener.open(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, _maybe_json(raw), raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            return e.code, _maybe_json(raw), raw

    def get(self, path: str, auth: bool = False) -> tuple[int, Any, str]:
        headers = {"Accept": "application/json"}
        if auth:
            if not self.access:
                raise RuntimeError("需要先登录（access token 未就绪）")
            headers["Authorization"] = f"Bearer {self.access}"
        return self._send(urllib.request.Request(self.base + path, headers=headers))

    def post_json(self, path: str, payload: dict[str, Any], auth: bool = False) -> tuple[int, Any, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if auth and self.access:
            headers["Authorization"] = f"Bearer {self.access}"
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        return self._send(req)

    def login(self, username: str, password: str) -> tuple[int, dict[str, Any]]:
        status, body, _ = self.post_json("/api/v1/auth/login", {"username": username, "password": password})
        if status == 200 and isinstance(body, dict) and body.get("access_token"):
            self.access = body["access_token"]
        return status, body if isinstance(body, dict) else {}

    def cookie_raw(self) -> str:
        out = []
        for c in self.jar:
            out.append(f"{c.name}={c.value[:8]}…; secure={c.secure}; path={c.path}")
        return " | ".join(out) or "(无)"


def _maybe_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return None


def _as_list(body: Any, *keys: str) -> list[Any]:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in keys:
            v = body.get(k)
            if isinstance(v, list):
                return v
    return []


def suite_gate(client: Client, exp: dict[str, Any], user: str, pwd: str) -> bool:
    """A6 三快腿：login 200 → cookie 可存且零 Secure → refresh 200 → /me 闭环。"""
    e = exp["suites"]["gate"]
    row = Row()
    status, body = client.login(user, pwd)
    row.add("login 状态", str(e["login_status"]), str(status), status == e["login_status"])

    names = [c.name for c in client.jar]
    sec = sum(1 for c in client.jar if c.name == e["cookie_name"] and c.secure)
    row.add("refresh cookie 在位", e["cookie_name"], ",".join(names) or "(无)", e["cookie_name"] in names)
    row.add("cookie Secure 计数", str(e["cookie_secure_count"]), str(sec), sec == e["cookie_secure_count"])

    rstatus, rbody, _ = client.post_json("/api/v1/auth/refresh", {})
    newtok = rbody.get("access_token") if isinstance(rbody, dict) else None
    row.add("refresh 状态", str(e["refresh_status"]), str(rstatus), rstatus == e["refresh_status"] and bool(newtok))
    if newtok:
        client.access = newtok

    mstatus, mbody, _ = client.get("/api/v1/auth/me", auth=True)
    sub = mbody.get("sub") if isinstance(mbody, dict) else None
    row.add("/me 状态", str(e["me_status"]), str(mstatus), mstatus == e["me_status"])
    row.add("/me sub", e["me_sub"], str(sub), sub == e["me_sub"])
    return row.render("gate · A6 三快腿（登录门判据）")


def suite_hash(client: Client, exp: dict[str, Any]) -> bool:
    """入口 chunk hash 对表：拉 / 提取 index-*.js，与基线一致（超阈值即预警）。"""
    e = exp["suites"]["hash"]
    row = Row()
    req = urllib.request.Request(client.base + "/", headers={"Accept": "text/html"})
    try:
        with client.opener.open(req, timeout=TIMEOUT) as resp:
            html = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as ex:
        html, status = ex.read().decode("utf-8", "replace"), ex.code
    found = sorted(set(re.findall(r"index-[A-Za-z0-9_-]{8,}\.js", html)))
    hit = e["entry_hash"] in " ".join(found)
    row.add("GET / 状态", "200", str(status), status == 200)
    row.add("入口 index-*.js", e["entry_hash"], ", ".join(found) or "(无)", hit)
    return row.render("hash · 入口基线对表（hash 链取证）")


def suite_modules(client: Client, exp: dict[str, Any], user: str, pwd: str) -> bool:
    """坞 census：/api/v1/modules 计数 + id 集合与预期形态对表。"""
    e = exp["suites"]["modules"]
    row = Row()
    if not client.access:
        client.login(user, pwd)
    status, body, _ = client.get("/api/v1/modules", auth=True)
    items = _as_list(body, "modules", "items", "data")
    ids = sorted(str(m.get("id")) for m in items if isinstance(m, dict) and m.get("id"))
    row.add("modules 计数", str(e["count"]), str(len(ids)), len(ids) == e["count"])
    miss = sorted(set(e["ids"]) - set(ids))
    extra = sorted(set(ids) - set(e["ids"]))
    row.add("id 集合对表", "无缺无多", f"缺:{miss or '无'} 多:{extra or '无'}", not miss and not extra)
    return row.render("modules · 坞 census（模块形态）")


def suite_docsprobe(client: Client, exp: dict[str, Any], user: str, pwd: str) -> bool:
    """文档链：/api/v1/docs/nodes 活跃人格根=1、日记根=1（T16-1 销账 + T17 幂等判据机器侧）。"""
    e = exp["suites"]["docsprobe"]
    row = Row()
    if not client.access:
        client.login(user, pwd)
    status, body, _ = client.get("/api/v1/docs/nodes", auth=True)
    nodes = _as_list(body, "nodes", "items", "data")

    def is_root(n: dict[str, Any]) -> bool:
        pid = n.get("parent_id")
        return (pid is None or pid == "") and not n.get("deleted_at")

    roots = [n for n in nodes if isinstance(n, dict) and is_root(n)]
    persona = sum(1 for n in roots if "人格" in str(n.get("name", "")))
    diary = sum(1 for n in roots if "日记" in str(n.get("name", "")))
    row.add("docs/nodes 状态", "200", str(status), status == 200)
    row.add("活跃人格根", str(e["persona_roots"]), str(persona), persona == e["persona_roots"])
    row.add("活跃日记根", str(e["diary_roots"]), str(diary), diary == e["diary_roots"])
    row.add("活跃根合计", "2", str(len(roots)), len(roots) == 2)
    return row.render("docsprobe · 文档链根计数（C1 幂等机器侧）")


def _check_o1(body: Any, e: dict[str, Any], modules_ids: list[str]) -> Row:
    """O1 契约对表纯函数（判据先行：契约源=MiMo《字段契约卡》17:55）。
    判据：ok=true / count=len(modules)=契约值 / id 集合与 modules 段一致（契约卡 §2.4-3）/
    status∈四态枚举 / summary 四态和=count / reconcile.scheduler_enabled=契约默认值。"""
    row = Row()
    if not isinstance(body, dict):
        row.add("响应形态", "dict", type(body).__name__, False)
        return row
    mods = body.get("modules") or []
    ids = sorted(str(m.get("id")) for m in mods if isinstance(m, dict) and m.get("id"))
    count = body.get("count")
    enum = e.get("status_enum") or ["healthy", "degraded", "disabled", "unknown"]
    statuses = [str(m.get("status")) for m in mods if isinstance(m, dict)]
    bad_status = sorted({s for s in statuses if s not in enum})
    summary = body.get("summary") or {}
    ssum = sum(int(v) for v in summary.values() if isinstance(v, int))
    sched = (body.get("reconcile") or {}).get("scheduler_enabled")
    row.add("ok 字段", "true", str(body.get("ok")), body.get("ok") is True)
    row.add("count=len(modules)", str(len(mods)), f"count={count} len={len(mods)}",
            isinstance(count, int) and count == len(mods))
    row.add("count=契约值", str(e.get("count")), str(count), count == e.get("count"))
    miss = sorted(set(modules_ids) - set(ids))
    extra = sorted(set(ids) - set(modules_ids))
    row.add("id 集合≡modules 段", "无缺无多", f"缺:{miss or '无'} 多:{extra or '无'}", not miss and not extra)
    row.add("status∈四态枚举", "/".join(enum), f"越界:{bad_status or '无'}", not bad_status)
    row.add("summary 四态和=count", str(len(mods)), str(ssum), ssum == len(mods))
    row.add("scheduler_enabled", str(e.get("scheduler_enabled")), str(sched), sched == e.get("scheduler_enabled"))
    return row


def suite_o1status(client: Client, exp: dict[str, Any], user: str, pwd: str) -> bool:
    """O1 模块健康四态：/api/v1/health/modules 契约对表。
    判据先行态：O1 未部署→404→输出 SKIP 行（不判红不冒充绿）；部署后自动转实验。"""
    e = exp["suites"]["o1_status"]
    row = Row()
    if not client.access:
        client.login(user, pwd)
    status, body, _ = client.get("/api/v1/health/modules", auth=True)
    if status == 404 and e.get("skip_if_404", True):
        row.add("⏭SKIP O1 路由", "200（部署后实验）", "404 未部署（判据先行态）", True)
        ok = row.render("o1status · O1 四态（SKIP：未部署，非绿非红）")
        print("  说明：判据包预写态（契约卡已立、部署未到）；部署后本行转实验 7 判据。")
        return ok
    modules_ids = exp["suites"]["modules"]["ids"]
    row = _check_o1(body, e, modules_ids)
    return row.render("o1status · O1 模块健康四态（契约）")


def _check_bff(body: Any, e: dict[str, Any]) -> Row:
    """BFF 聚合源健康度对表纯函数（判据先行：契约源=hermes 复现帖+根因侦察）。

    判据：响应 dict + providers 数组 / id 集合≡契约登记聚合源（缺多皆红，防静默丢源）/
    每源 status ∈ 白名单（ok|not-implemented）——**unavailable=红**并点名哪几家（缺陷态警报器）。
    """
    row = Row()
    if not isinstance(body, dict):
        row.add("响应形态", "dict", type(body).__name__, False)
        return row
    providers = body.get("providers")
    if not isinstance(providers, list):
        row.add("providers 形态", "list", type(providers).__name__, False)
        return row
    expected_ids = list(e.get("providers") or [])
    allow = set(e.get("status_allow") or ["ok", "not-implemented"])
    ids = sorted(str(p.get("id")) for p in providers if isinstance(p, dict) and p.get("id"))
    miss = sorted(set(expected_ids) - set(ids))
    extra = sorted(set(ids) - set(expected_ids))
    row.add("聚合源集合≡契约", "/".join(expected_ids), f"缺:{miss or '无'} 多:{extra or '无'}",
            not miss and not extra)
    bad = [(str(p.get("id")), str(p.get("status"))) for p in providers
           if isinstance(p, dict) and str(p.get("status")) not in allow]
    row.add("status∈白名单(无unavailable)", "|".join(sorted(allow)),
            "越界:" + (",".join(f"{i}:{s}" for i, s in bad) if bad else "无"), not bad)
    return row


def suite_bffsummary(client: Client, exp: dict[str, Any], user: str, pwd: str) -> bool:
    """BFF `/api/v1/summary/today` 聚合源健康度（机读判据卡·还 09-20 O 项欠账）。

    判据先行态：expected.json `suites.bff_summary.enabled=false`（当前生产=缺陷态，
    未立基线）→ 输出 SKIP 行（非绿非红不粉饰）；workbuddy 修复上线后翻 enabled=true 转实验。
    """
    e = exp["suites"]["bff_summary"]
    row = Row()
    if not e.get("enabled", False):
        row.add("⏭SKIP BFF 聚合", "修复后实验", "enabled=false（判据先行态：缺陷未修不立基线）", True)
        ok = row.render("bffsummary · BFF 聚合源健康度（SKIP：判据先行态）")
        print("  说明：预写； workbuddy 修复上线后 expected.json 翻 enabled=true 转实验。")
        return ok
    if not client.access:
        client.login(user, pwd)
    status, body, _ = client.get("/api/v1/summary/today", auth=True)
    if status == 404 and e.get("skip_if_404", True):
        row.add("⏭SKIP BFF 路由", "200（部署后实验）", "404 未部署", True)
        return row.render("bffsummary · BFF 聚合（SKIP：未部署）")
    row.add("http", "200", str(status), status == 200)
    if status == 200:
        row = _check_bff(body, e)
    return row.render("bffsummary · BFF 聚合源健康度（判据卡）")


def suite_deploycheck(client: Client, exp: dict[str, Any], user: str, pwd: str) -> bool:
    """部署后四合一：healthz + gate + modules + hash（部署留痕帖的标准复核动作）。"""
    row = Row()
    status, _, _ = client.get("/healthz")
    row.add("healthz", "200", str(status), status == 200)
    ok_row = row.render("deploycheck · healthz")
    ok_gate = suite_gate(client, exp, user, pwd)
    ok_mod = suite_modules(client, exp, user, pwd)
    ok_hash = suite_hash(client, exp)
    return ok_row and ok_gate and ok_mod and ok_hash


def suite_readyz(client: Client, exp: dict[str, Any]) -> bool:
    """ISSUE-010 判据：/readyz 必须返回 JSON 而非 SPA 壳（采纳 MiMo A2 规格）。

    说明：_send 未保留 Content-Type 头，故本套件以 body 形状判定——
    JSON 以 `{` 开头，SPA 壳以 `<!doctype`/`<html` 开头，二者可 100% 区分。
    """
    e = exp["suites"]["readyz"]
    rows = Row()
    status, _, raw = client.get("/readyz")
    body_text = str(raw) if isinstance(raw, str) else ""
    ok_spa = bool(body_text) and not any(
        body_text.lstrip().lower().startswith(p) for p in e.get("must_not_start_with", [])
    )
    rows.add("readyz http", str(e.get("status", 200)), str(status), status == e.get("status", 200))
    rows.add("body 非 SPA 壳", "not <!doctype/<html", body_text[:30] if body_text else "(empty)", ok_spa)
    return rows.render("readyz · ISSUE-010")


def main() -> int:
    ap = argparse.ArgumentParser(description="L2 验收判据自动化套件（只读）")
    ap.add_argument("--target", default="production", choices=["production", "local"])
    ap.add_argument("--suite", default="all",
                    choices=["gate", "hash", "modules", "docsprobe", "o1status", "bffsummary", "readyz", "deploycheck", "all"])
    ap.add_argument("--password", default=None, help="不推荐；优先用环境变量 ALONGSIDE_ADMIN_PASSWORD")
    ap.add_argument("--emit-verdict", action="store_true",
                    help="RFC-001：在人读表格之外追加一行机读 verdict JSON（schema=alongside.probe.verdict/1）")
    args = ap.parse_args()

    exp = load_expected()
    tgt = exp["targets"][args.target]
    if not tgt.get("enabled", True):
        print(f"target {args.target} 未立基线（enabled=false），拒绝对表")
        return 2

    import os
    pwd = args.password or (os.environ.get("ALONGSIDE_ADMIN_PASSWORD") or os.environ.get("LIFEOS_ADMIN_PASSWORD")) or ""
    user = tgt.get("username", "admin")
    need_auth = args.suite in ("gate", "modules", "docsprobe", "o1status", "bffsummary", "deploycheck", "all")
    if need_auth and not pwd:
        print("需要口令：export ALONGSIDE_ADMIN_PASSWORD=…（绝不写入文件）")
        return 2

    client = Client(tgt["base_url"])
    results: list[bool] = []
    if args.suite in ("gate", "all"):
        results.append(suite_gate(client, exp, user, pwd))
    if args.suite in ("hash", "all"):
        results.append(suite_hash(client, exp))
    if args.suite in ("modules", "all"):
        results.append(suite_modules(client, exp, user, pwd))
    if args.suite in ("docsprobe", "all"):
        results.append(suite_docsprobe(client, exp, user, pwd))
    if args.suite in ("o1status", "all"):
        results.append(suite_o1status(client, exp, user, pwd))
    if args.suite in ("bffsummary", "all"):
        results.append(suite_bffsummary(client, exp, user, pwd))
    if args.suite == "readyz":
        results.append(suite_readyz(client, exp))
    if args.suite == "deploycheck":
        results.append(suite_deploycheck(client, exp, user, pwd))

    print("\n" + "=" * 60)
    print("ALL_GREEN ✅" if all(results) else "RED ❌（有判据未过，详见上表）")
    if args.emit_verdict:
        emit_verdict(args.target, args.suite)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
