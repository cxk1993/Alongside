"""T18 唯一判据自动验证：加工具 = 插件 manifest 多一行 provides。

★ 判据（整张 T18 卡的成败只看它）：
    给 AI 加一个新工具，只需某个已启用插件的 manifest 里多一行 provides，
    不改内核、不改 services/api/modules/mcp/ 任何文件。

脚本流程（全自动，自包含，用完清理）：
  1. 造测试插件 modules/mcp_probe/（manifest 声明 provides=["probe.ping.write"]
     + 最小 router.py —— T03 内核既定契约：modules/ 下无 router.py 则启动失败
     （core/manifest.py load_router），故 probe 附带一行 health；这不影响判据：
     加工具仍然只改 manifest 的 provides 行，probe 的 router 与工具暴露无关）
  2. 起真实服务（独立临时库 + 独立端口）→ 建 PAT → MCP tools/list
     → 断言 probe_ping_write 已自动出现
  3. 删除 mcp_probe → 重启服务 → tools/list → 断言 probe_ping_write 已消失
  4. 清理（probe 目录兜底删除、临时库删除）

用法：
    python scripts/verify_t18_criterion.py        # 通过 exit 0，失败 exit 1
接入：tools/task.py cmd_verify 发现本脚本存在即自动运行（同 check_kernel_purity 模式）。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
MODULES_DIR = API_DIR / "modules"
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


def main() -> int:
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
