"""T18 唯一判据自动验证：加工具 = 插件 manifest 多一行 provides。

★ 判据（整张 T18 卡的成败只看它）：
    给 AI 加一个新工具，只需某个已启用插件的 manifest 里多一行 provides，
    不改内核、不改 services/api/modules/mcp/ 任何文件。

★ 静态面（2026-11-24 · 比丢 · 工单 28）—— `--structural-only`
    起真服务验的是「加一行 provides → 工具真的出现」。
    但**工具该出现却没出现**是另一半判据：manifest 声明了 provides，
    工具面上却没有对应工具 —— 这条不需要起服务（摊平是纯函数），
    所以做成静态断言，并在 `--selfcheck` 里喂失效自证。
    依据：ISSUE-016（provides ↔ MCP 工具面关系隐式化）+ 判据 41（安全不能靠巧合）。
    ★ 判据不是「每条 provides 都必须有工具」，而是
      「**没有工具这件事必须有人明说过**」：人专属 / 能力声明 / 登记在案的缺口。
      新出现的静默丢失 ⇒ 红；登记项已消失 ⇒ 红（登记表不许说谎）。

脚本流程（全自动，自包含，用完清理）：
  1. 静态：扫全部 manifest 的 provides，逐条归档（工具 / 人专属 / 能力声明 / 显式登记）
  2. 造测试插件 modules/mcp-probe/（manifest 声明 provides=["probe.ping.write"]
     + 最小 router.py —— T03 内核既定契约：modules/ 下无 router.py 则启动失败
     （core/manifest.py load_router），故 probe 附带一行 health；这不影响判据：
     加工具仍然只改 manifest 的 provides 行，probe 的 router 与工具暴露无关）
  3. 起真实服务（独立临时库 + 独立端口）→ 建 PAT → MCP tools/list
     → 断言 probe_ping_write 已自动出现
  4. 删除 mcp_probe → 重启服务 → tools/list → 断言 probe_ping_write 已消失
  5. 清理（probe 目录兜底删除、临时库删除）

用法：
    python scripts/verify_t18_criterion.py                 # 静态 + 真服务，通过 exit 0
    python scripts/verify_t18_criterion.py --structural-only  # 只跑静态判据（不占端口）
    python scripts/verify_t18_criterion.py --selfcheck     # 反向自证：喂失效必须报红
接入：tools/task.py cmd_verify 发现本脚本存在即自动运行（同 check_kernel_purity 模式）。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
MODULES_DIR = API_DIR / "modules"
REPO_DIR = API_DIR.parents[1]
PLUGINS_DIR = REPO_DIR / "plugins"
PROBE_DIR = MODULES_DIR / "mcp-probe"
PORT = 18233
BASE = f"http://127.0.0.1:{PORT}"

sys.path.insert(0, str(API_DIR))

PROBE_MANIFEST = {
    # ★ id 必须等于目录名，且 T03 契约不允许下划线（^[a-z][a-z0-9-]*$）→ 用连字符。
    #   工具名来自 provides（probe.ping.write → probe_ping_write），与插件 id 无关。
    "id": "mcp-probe",
    "name": "MCP 判据探针",
    "version": "0.0.1",
    "kind": "builtin",
    "minKernel": "0.1.0",
    "kernelApi": "^1",
    "icon": "box",
    "description": "T18 判据验证用临时插件：仅声明 provides，验证完即删",
    "author": "verify",
    "window": {"w": 320, "h": 240},
    "entry": "",
    "api": {
        "base": "/api/v1/mcp-probe",
        "openapi": "/api/v1/mcp-probe/openapi.json",
        "health": "/api/v1/mcp-probe/health",
    },
    "provides": ["probe.ping.write"],
    "requires": [],
    "slots": [],
    "emits": [],
    "consumes": [],
    "permissions": ["db:own"],
    "migrations": None,
    "settingsSchema": None,
    # ★ 2026-11-24 比丢：这里原来写的是**旧形态对象**
    #   {"onInstall": None, "onEnable": None, ...}，而 core/manifest.py:105 的契约是
    #   `lifecycle: str | None = None`（字符串或 null）
    #   ⇒ 探针一旦落盘，create_app() 的 discover_modules() 直接抛
    #     `ManifestError: 模块「mcp-probe」manifest 校验失败：lifecycle: Input should be a valid string`
    #   ⇒ T18 门禁自己造自己崩，永远红。
    #   不声明 = None 是合法且最省事的（35 个内置模块也都不声明）。
    #   这正是「门禁自身失效」的第四例（主人 14:22 的 c80eb6f 修了前三道）。
}

PROBE_ROUTER = '''"""T18 判据探针路由：仅 health（工具暴露与 router 内容无关）。"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
'''


def _write_probe() -> None:
    if PROBE_DIR.exists():
        import shutil

        shutil.rmtree(PROBE_DIR, ignore_errors=True)
    (PROBE_DIR).mkdir(parents=True)
    (PROBE_DIR / "manifest.json").write_text(
        json.dumps(PROBE_MANIFEST, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (PROBE_DIR / "router.py").write_text(PROBE_ROUTER, encoding="utf-8")


def _remove_probe() -> None:
    import shutil

    try:
        shutil.rmtree(PROBE_DIR, ignore_errors=True)
    except OSError as exc:
        print(f"WARN probe 目录清理失败（可手动删）：{PROBE_DIR} → {exc}")


def _wait_port(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=1):
                return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError(f"服务 {PORT} 端口 {timeout}s 未就绪")


def _start_server() -> tuple[object, threading.Thread]:
    os.environ["DB_PATH"] = "./data/tmp_t18criterion.db"
    os.environ["INTERNAL_API_BASE"] = BASE
    import uvicorn
    from sqlmodel import SQLModel

    import db.engine as _db_engine
    from core.app import create_app
    from db.engine import init_engine

    _db_engine._engine = None  # 重启语义：第二次起服务要重建引擎（旧连接已随上次服务关闭）
    init_engine()
    app = create_app()
    SQLModel.metadata.create_all(_db_engine.get_engine())
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_port()
    return server, thread


def _stop_server(server: object | None, thread: threading.Thread | None) -> None:
    if server is not None:
        server.should_exit = True  # type: ignore[attr-defined]
    if thread is not None:
        thread.join(timeout=15)
    time.sleep(0.5)  # 端口释放缓冲
    try:
        # 释放 SQLite 句柄，否则后面的临时库清理会因文件被锁而失败
        import db.engine as _db_engine

        _db_engine.get_engine().dispose()
    except Exception:  # noqa: BLE001 —— 清理尽力而为
        pass


def _http(
    method: str,
    path: str,
    body: dict | None = None,
    token: str | None = None,
) -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data=data, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def _tools_list(pat: str) -> list[str]:
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    code, body = _http("POST", "/api/v1/mcp", rpc, token=pat)
    assert code == 200, f"tools/list HTTP {code}: {body}"
    return [t["name"] for t in body["result"]["tools"]]


def _clean_tmp_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        p = API_DIR / "data" / f"tmp_t18criterion.db{suffix}"
        try:
            p.unlink(missing_ok=True)
        except OSError as exc:
            # 清理失败不改变判据结果（PASS 已定），只提示残留位置
            print(f"WARN 临时库清理失败（可手动删）：{p} → {exc}")


# ═════════════════ 静态判据：provides ↔ MCP 工具面 ═════════════════
#
# ★ 病根（ISSUE-016 / 判据 41）
#   `derive_tool` 不认识的 provides 会**静默丢弃**：动词不在映射表、缺 api.base、
#   段数不足 —— 三种情况都没有一行日志。于是「这个能力 AI 用不用得上」从来
#   不是有人决定的，而是映射表恰好有没有决定的。
#   双向都出过事：该拦的（install 类）靠「恰好不认识」拦着；该给的（docs.search、
#   catalog.read、query.read）靠「恰好没映射」丢了，没人发现。
#
# ⇒ 本段把关系**显式化**：每条 provides 必须落到以下四格之一，否则红。
#     ① 摊成工具（with_tool）
#     ② 动词在 `_HUMAN_ONLY_VERBS`（人专属，来自实现，不抄第二份）
#     ③ 动词在 `_CAPABILITY_ONLY_VERBS`（能力声明，同上）
#     ④ 登记进 `_NO_TOOL_SURFACE`，并写明属于哪一类 + 为什么
#   登记表还会做反向核对：登记项若已不存在（或已能摊成工具），同样红 ——
#   登记表不许变成「永远不会被清理的借口清单」。


@dataclass(frozen=True)
class NoToolEntry:
    """一条「故意不进工具面」的 provides 的档案。"""

    kind: str
    """human=人专属 · capability-decl=能力声明 · gap=在案待补缺口 ·
    semi-write=半写（将来给 AI 应走提案）· undecided=语义未定。"""

    reason: str


_KIND_LABEL: dict[str, str] = {
    "human": "人专属",
    "capability-decl": "能力声明",
    "gap": "待补缺口",
    "semi-write": "半写（应走提案）",
    "undecided": "语义未定",
}

#: 显式豁免表：`<manifest id>::<provides 串>` → 档案。
#:
#: ★ 每一条都要能回答「为什么 AI 够不着」；写不出理由的，就不该豁免 —— 去补映射。
#: ★ 2026-11-24 首次落表时的 12 条均经逐条核对：三条 `gap` 对应的 REST 端点真实存在
#:   （catalog `GET /api/v1/catalog` · docs `GET /api/v1/docs/search` ·
#:   query `GET /api/v1/query/presets`），它们不是「不该给」，是「还没给」。
#:   这类**必须保持可见**：报告里单独计数并逐条打印，不许混进「已归档」里消失。
_NO_TOOL_SURFACE: dict[str, NoToolEntry] = {
    "auth::auth.session": NoToolEntry(
        "human", "登录会话与刷新令牌：AI 不得持有会话态（会话是人的凭据）"
    ),
    "catalog::catalog.read": NoToolEntry(
        "gap", "GET /api/v1/catalog 真实存在，但没映射成工具 —— AI 读不到能力目录（待补）"
    ),
    "docs::docs.search": NoToolEntry(
        "gap", "GET /api/v1/docs/search 真实存在，但没映射成工具 —— AI 搜不了文档（待补）"
    ),
    "health::health.care.request": NoToolEntry(
        "undecided", "「求助/关怀」是人的动作还是 AI 可发起的请求，尚未拍板（ISSUE-016 待定）"
    ),
    "packs::pack.list": NoToolEntry("human", "包清单 = 平台装配视图，人专属"),
    "packs::pack.dryrun": NoToolEntry("human", "组包预演会改平台装配，人专属"),
    "packs::packs.health": NoToolEntry("human", "包层健康巡检属运维面，人专属"),
    "plugins::plugin.list": NoToolEntry("human", "插件清单 = 平台装配视图，人专属"),
    "query::query.read": NoToolEntry(
        "gap", "GET /api/v1/query/presets 真实存在，但没映射成工具（待补）"
    ),
    "sdu-feeds::sdu.feed.sync": NoToolEntry(
        "semi-write", "拉取会回写（ISSUE-016）：将来给 AI 应走提案，不是裸工具"
    ),
    "welearn::welearn.sync": NoToolEntry(
        "semi-write", "拉取会回写（ISSUE-016）：将来给 AI 应走提案，不是裸工具"
    ),
    "yuketang::yuketang.sync": NoToolEntry(
        "semi-write", "拉取会回写（ISSUE-016）：将来给 AI 应走提案，不是裸工具"
    ),
}


@dataclass(frozen=True)
class SurfaceFinding:
    """一条 provides 没有被归档 —— 说明「它去哪了」没人决定。"""

    manifest: str
    plugin_id: str
    capability: str
    problem: str


@dataclass(frozen=True)
class SurfaceReport:
    manifests: int
    provides: int
    with_tool: int
    #: 由实现里的显式集合自动归档的条数（不需要逐条登记）。
    classified: dict[str, int]
    #: 由 `_NO_TOOL_SURFACE` 登记的条数（按档案类别）。
    registered: dict[str, int]
    gaps: tuple[SurfaceFinding, ...]
    stale: tuple[str, ...]
    roots_scanned: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.gaps and not self.stale


def _diagnose_no_tool(capability: str, api_base: str) -> str:
    """给「摊不出工具」一个能直接照着修的原因，而不是「返回了 None」。"""
    parts = capability.split(".")
    if not api_base:
        return "插件没有 api.base —— 工具无处转发（补 api.base，或登记为不进工具面）"
    if len(parts) < 3:
        return (
            f"能力串只有 {len(parts)} 段（契约要求 domain.resource.verb）—— 摊不出工具"
            "（改形态，或登记为不进工具面）"
        )
    return (
        f"动词 {parts[-1]!r} 不在 MCP 动词表（_METHOD_BY_VERB）—— 静默丢弃"
        "（补映射 —— 会立刻暴露给 AI，慎重；或登记为不进工具面）"
    )


def scan_provides_tool_surface(
    roots: list[tuple[str, Path]] | None = None,
    registry: dict[str, NoToolEntry] | None = None,
    *,
    classify_human: bool = True,
    classify_capability: bool = True,
) -> SurfaceReport:
    """静态扫 provides → 工具面，逐条归档。

    ★ 工具判定直接用实现里的 `derive_tool`（纯函数），不在这里复刻一套推导规则——
      复刻就是造第二个真相源：实现改了、门禁没改，门禁会继续绿着守旧规则。
    ★ `roots` / `registry` 可注入，供 `--selfcheck` 在临时目录里喂失效。
    """
    from modules.mcp.mcp_server import _CAPABILITY_ONLY_VERBS, _HUMAN_ONLY_VERBS
    from modules.mcp.registry_adapter import derive_tool

    scanned_roots = roots or [
        ("modules", MODULES_DIR),
        ("plugins", PLUGINS_DIR),
    ]
    table = _NO_TOOL_SURFACE if registry is None else registry

    manifests = 0
    provides_total = 0
    with_tool = 0
    classified: dict[str, int] = {"human": 0, "capability-decl": 0}
    registered: dict[str, int] = {}
    gaps: list[SurfaceFinding] = []
    used: set[str] = set()

    for label, root in scanned_roots:
        if not root.is_dir():
            continue
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            manifest_path = child / "manifest.json"
            if not manifest_path.is_file():
                continue
            manifests += 1
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                gaps.append(
                    SurfaceFinding(
                        f"{label}/{child.name}/manifest.json",
                        child.name,
                        "（整份 manifest）",
                        f"manifest 读不出来（{type(exc).__name__}）—— 它的 provides 无从核对",
                    )
                )
                continue
            plugin_id = str(manifest.get("id") or child.name)
            api = manifest.get("api") or {}
            api_base = str(api.get("base") or "")
            tool_routes = api.get("tools") or {}
            for capability in manifest.get("provides") or []:
                if not isinstance(capability, str):
                    continue
                provides_total += 1
                # ★ 必须自己也判 api_base：`derive_tool` 是纯函数，api_base="" 时它照样
                #   返回映射（path 变成 `/items`），而 `build_tool_map` 在调用前就把这种
                #   插件跳过了（`if not api_base: continue`）。只信 derive_tool 会假绿。
                if api_base and derive_tool(capability, api_base, plugin_id, tool_routes) is not None:
                    with_tool += 1
                    continue
                verb = capability.rsplit(".", 1)[-1]
                if classify_human and verb in _HUMAN_ONLY_VERBS:
                    classified["human"] += 1
                    continue
                if classify_capability and verb in _CAPABILITY_ONLY_VERBS:
                    classified["capability-decl"] += 1
                    continue
                key = f"{plugin_id}::{capability}"
                entry = table.get(key)
                if entry is not None:
                    registered[entry.kind] = registered.get(entry.kind, 0) + 1
                    used.add(key)
                    continue
                gaps.append(
                    SurfaceFinding(
                        f"{label}/{child.name}/manifest.json",
                        plugin_id,
                        capability,
                        _diagnose_no_tool(capability, api_base),
                    )
                )

    # 反向核对：登记项若已不存在（或已能摊成工具 / 已被显式集合覆盖），就是陈旧登记。
    stale = tuple(sorted(set(table) - used))

    return SurfaceReport(
        manifests=manifests,
        provides=provides_total,
        with_tool=with_tool,
        classified=classified,
        registered=registered,
        gaps=tuple(gaps),
        stale=stale,
        roots_scanned=tuple(f"{label}={root}" for label, root in scanned_roots),
    )


def render_surface(report: SurfaceReport) -> str:
    """人类可读报告。★ 待补缺口必须单独出现，不许混进「已归档」消失。"""
    lines = [
        "T18 · 静态判据：provides ↔ MCP 工具面",
        f"扫描根：{', '.join(report.roots_scanned)}",
        f"扫到 {report.manifests} 份 manifest / {report.provides} 条 provides；"
        f"其中 {report.with_tool} 条摊成工具。",
        f"自动归档：人专属 {report.classified.get('human', 0)} 条 · "
        f"能力声明 {report.classified.get('capability-decl', 0)} 条。",
    ]
    if report.registered:
        parts = " · ".join(
            f"{_KIND_LABEL.get(kind, kind)} {count}"
            for kind, count in sorted(report.registered.items())
        )
        lines.append(f"登记归档：{parts}。")
    for kind, count in sorted(report.registered.items()):
        if kind != "gap":
            continue
        lines.append(f"  ⚠ 其中 {count} 条是「待补缺口」——这些能力 AI 现在还够不着，别当成已解决：")
        for key in sorted(_NO_TOOL_SURFACE):
            if _NO_TOOL_SURFACE[key].kind == "gap":
                lines.append(f"    · {key}：{_NO_TOOL_SURFACE[key].reason}")
    if report.gaps:
        lines.append(f"✗ {len(report.gaps)} 条 provides 没有被归档（它们是静默丢失的候选）：")
        for gap in report.gaps:
            lines.append(f"  · [{gap.plugin_id}] {gap.capability} —— {gap.problem}")
        lines.append(
            "  修法：给 manifest 补正确的 api.base / 能力串形态 / 动词映射，"
            "或在 verify_t18_criterion.py 的 _NO_TOOL_SURFACE 里登记并写明理由。"
        )
    if report.stale:
        lines.append(f"✗ {len(report.stale)} 条登记项已不成立（登记表在说谎，请删掉或更正）：")
        for key in report.stale:
            lines.append(f"  · {key}（登记项已不存在：能力被删了 / 已能摊成工具 / 已被显式集合覆盖）")
    if report.provides == 0 or report.manifests == 0:
        lines.append(
            "✗ 反空转：一条 provides 都没扫到 —— 扫描根算错了，或者 manifest 全没了。"
            "「零违规」在这里没有意义。"
        )
    lines.append(
        "结论：" + ("✓ 每条 provides 都有去处（工具 / 人专属 / 能力声明 / 登记在案）" if report.ok and report.provides else "✗ 有静默丢失或陈旧登记")
    )
    return "\n".join(lines)


def structural_check(
    roots: list[tuple[str, Path]] | None = None,
    registry: dict[str, NoToolEntry] | None = None,
) -> int:
    report = scan_provides_tool_surface(roots, registry)
    print(render_surface(report))
    if report.provides == 0 or report.manifests == 0:
        return 1
    return 0 if report.ok else 1


def _selfcheck() -> int:
    """反向自证：临时目录里喂 5 类失效，各自必须报红；干净插件必须全绿。

    ★ 断言到**具体条数 + 具体文案**（团队纪律第 4 条），不是只看退出码。
    ★ 与真仓库无关：只扫临时目录，注册表由本函数注入 —— 真仓库的 provides
      以后怎么变，都不会让自检假红 / 假绿。
    """
    import tempfile

    failures: list[str] = []
    passed: list[str] = []
    expected_gaps = {
        "demo-unknown-verb::demo.item.frobnicate": "frobnicate",
        "demo-no-base::demo.item.read": "api.base",
        "demo-two-seg::demo.sync": "2 段",
    }

    with tempfile.TemporaryDirectory(prefix="t18-structural-") as raw:
        tmp = Path(raw)

        def make(dirname: str, payload: dict) -> None:
            d = tmp / dirname
            d.mkdir(parents=True, exist_ok=True)
            (d / "manifest.json").write_text(
                json.dumps({"id": dirname, **payload}, ensure_ascii=False), encoding="utf-8"
            )

        # ① 干净样本：能摊成工具
        make("demo-good", {"provides": ["demo.item.read"], "api": {"base": "/api/v1/demo"}})
        # ② 动词不在表
        make("demo-unknown-verb", {"provides": ["demo.item.frobnicate"], "api": {"base": "/api/v1/demo"}})
        # ③ 没有 api.base
        make("demo-no-base", {"provides": ["demo.item.read"], "api": {}})
        # ④ 两段形态（契约要 domain.resource.verb）
        make("demo-two-seg", {"provides": ["demo.sync"], "api": {"base": "/api/v1/demo"}})
        # ⑤ 人专属动词 → 自动归档，不该报红
        make("demo-human", {"provides": ["demo.item.install"], "api": {"base": "/api/v1/demo"}})
        # ⑥ 能力声明动词 → 自动归档，不该报红
        make("demo-capability", {"provides": ["demo.summary.today"], "api": {"base": "/api/v1/demo"}})
        # ⑦ 显式登记的豁免 → 不该报红
        make("demo-registered", {"provides": ["demo.thing.sync"], "api": {"base": "/api/v1/demo"}})

        registry = {
            "demo-registered::demo.thing.sync": NoToolEntry("undecided", "自检用登记项")
        }
        report = scan_provides_tool_surface([("tmp", tmp)], registry)

        if report.manifests != 7:
            failures.append(f"扫到 {report.manifests} 份 manifest，期望 7")
        else:
            passed.append("扫到 7 份 manifest")
        if report.provides != 7:
            failures.append(f"扫到 {report.provides} 条 provides，期望 7")
        else:
            passed.append("扫到 7 条 provides（反空转）")
        if report.with_tool != 1:
            failures.append(f"摊成工具 {report.with_tool} 条，期望 1（只有干净样本）")
        else:
            passed.append("干净样本摊成工具")
        if report.classified.get("human") != 1 or report.classified.get("capability-decl") != 1:
            failures.append(f"自动归档计数不对：{report.classified}")
        else:
            passed.append("人专属 / 能力声明各自动归档 1 条")
        if report.registered != {"undecided": 1}:
            failures.append(f"登记归档计数不对：{report.registered}")
        else:
            passed.append("登记豁免 1 条")

        got = {f"{g.plugin_id}::{g.capability}": g.problem for g in report.gaps}
        if set(got) != set(expected_gaps):
            failures.append(f"未归档集合不对：{sorted(got)}，期望 {sorted(expected_gaps)}")
        else:
            missing = [
                key for key, needle in expected_gaps.items() if needle not in got[key]
            ]
            if missing:
                failures.append(f"未归档文案缺具体原因：{missing} → {got}")
            else:
                passed.append("三类静默丢失各报红且文案含具体原因")
        if report.stale:
            failures.append(f"登记表命中却报陈旧：{report.stale}")
        else:
            passed.append("登记项命中时不报陈旧")

        # ⑧ 陈旧登记：登记项在仓库里不存在 ⇒ 必须红
        stale_report = scan_provides_tool_surface(
            [("tmp", tmp)], {"demo-good::demo.item.read": NoToolEntry("gap", "已能摊成工具")}
        )
        if "demo-good::demo.item.read" not in stale_report.stale:
            failures.append("陈旧登记没有被判红 —— 登记表会变成永久借口清单")
        else:
            passed.append("陈旧登记报红")
        if stale_report.ok:
            failures.append("存在陈旧登记时 ok 仍为 True")
        else:
            passed.append("陈旧登记使整体判红")

        # ⑨ 反空转：空根 ⇒ structural_check 必须 1
        rc = structural_check([("empty", tmp / "does-not-exist")], {})
        if rc != 1:
            failures.append(f"空扫描根返回 {rc}，期望 1（零 provides 不许当成通过）")
        else:
            passed.append("空扫描根判红")

    print(f"── T18 静态判据 · 反向自证（{len(passed) + len(failures)} 项）──")
    for line in passed:
        print(f"  ✓ {line}")
    for line in failures:
        print(f"  ✗ {line}")
    print(f"结论：{'✗ 自检未通过' if failures else '✓ 自检通过'}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="T18 唯一判据验证：manifest 多一行 provides ⇒ MCP 工具面多一个工具。"
    )
    parser.add_argument(
        "--selfcheck",
        action="store_true",
        help="反向自证：临时目录里喂失效，断言每类静默丢失都必须报红（不碰仓库、不占端口）。",
    )
    parser.add_argument(
        "--structural-only",
        action="store_true",
        help="只跑静态判据（provides ↔ 工具面），不起真服务 —— 不需要端口与临时库。",
    )
    args = parser.parse_args(argv)

    if args.selfcheck:
        return _selfcheck()

    # ★ 静态面先跑：不占端口、不留探针、红得更快，而且能直接指出是哪一条 provides。
    rc = structural_check()
    if rc != 0:
        print(
            "\n✗ 静态判据未通过 —— 真服务那一段没必要再跑："
            "provides 已经和工具面对不上，先把上面几条归档掉。"
        )
        return rc
    if args.structural_only:
        print("\nPASS（静态面）：每条 provides 都有去处；真服务段按 --structural-only 跳过。")
        return 0

    # 干净起点
    _remove_probe()
    _clean_tmp_db()

    server = None
    thread = None
    try:
        print("── 造 mcp_probe（manifest 声明 probe.ping.write）──")
        _write_probe()

        print("── 起服务（重启语义：全新进程读盘）──")
        server, thread = _start_server()

        from core.security import create_access_token

        code, body = _http(
            "POST", "/api/v1/mcp/pats",
            {"name": "判据探针PAT", "scopes": ["probe:write"]},
            token=create_access_token("admin"),
        )
        assert code == 201, body
        pat = body["token"]

        names = _tools_list(pat)
        assert "probe_ping_write" in names, (
            f"判据失败：tools/list 无 probe_ping_write（现有 {names}）"
        )
        print(
            f"✓ tools/list 自动出现 probe_ping_write"
            f"（共 {len(names)} 个工具，未改 mcp 模块任何文件）"
        )

        print("── 删除 mcp_probe → 重启 → 工具应消失 ──")
        _stop_server(server, thread)
        server = thread = None
        _remove_probe()

        server, thread = _start_server()
        code, body = _http(
            "POST", "/api/v1/mcp/pats",
            {"name": "判据探针PAT2", "scopes": ["probe:write"]},
            token=create_access_token("admin"),
        )
        assert code == 201, body
        pat2 = body["token"]

        names2 = _tools_list(pat2)
        assert "probe_ping_write" not in names2, (
            f"判据失败：删除后 probe_ping_write 仍在 {names2}"
        )
        print(f"✓ 删除插件后 probe_ping_write 已消失（剩 {len(names2)} 个工具）")

        print("PASS：唯一判据达成 —— 加工具 = manifest 多一行 provides，mcp 模块/内核零改动。")
        return 0
    finally:
        _stop_server(server, thread)
        _remove_probe()  # 兜底清理
        _clean_tmp_db()


if __name__ == "__main__":
    sys.exit(main())
