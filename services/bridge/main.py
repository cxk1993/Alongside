"""本机笔记桥（FastAPI，只监听 127.0.0.1）。

★ 唯一入口形态：独立 uvicorn 进程，由 frpc 隧道暴露到云服务器。
★ 暴露端点（内网隧道，仅服务器可访问）：
    GET  /bridge/healthz            存活 + 版本 + 库列表
    GET  /bridge/libs               库配置（md_count）
    GET  /bridge/scan?lib=&limit=    全量索引（流式 ndjson，大库不 OOM）
    GET  /bridge/read?lib=&path=     读全文（content + mtime + hash）
    GET  /bridge/changes?since=      增量变化（供增量索引）
    POST /bridge/write               写回（v0.1 默认 403，mode=rw 才放行）
    POST /bridge/notify              桌面通知（T24；通用本机代理能力）
    GET  /bridge/notifications       通知投递历史（环形，T24）

★ 所有 /bridge/* 请求必须带：X-Bridge-PSK / X-Bridge-Ts / X-Bridge-Nonce / X-Bridge-Sign
★ 绝不监听 0.0.0.0（见 __main__ 与 run.ps1，均写死 127.0.0.1）。
"""
from __future__ import annotations

import json
import httpx
import logging
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse

from .config import BridgeConfig, load_config
from .notify import send_windows_notification
from .protocol import NONCE_TTL_S, NonceStore, check_timestamp, verify_sig
from .reader import read_binary, read_note
from .scanner import count_md, scan_lib

log = logging.getLogger("bridge")
logging.basicConfig(level=logging.INFO)

BRIDGE_VERSION = "0.2.0"


def _canonical_path(request: Request) -> str:
    """签名用的路径：path + 规范化查询串（覆盖 GET 查询参数防篡改）。"""
    q = request.url.query
    return request.url.path + (f"?{q}" if q else "")


class BridgeState:
    """桥运行期状态：配置 + nonce 防重放 + 增量变化日志。"""

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.nonces = NonceStore(ttl=NONCE_TTL_S)
        self._changes: list[dict] = []
        self._seq = 0
        self._notifications: list[dict] = []
        self._notify_seq = 0

    def push_change(self, entry: dict) -> None:
        self._seq += 1
        self._changes.append({"seq": self._seq, **entry})
        if len(self._changes) > 2000:  # 只留最近 2000 条，防内存无限增长
            self._changes = self._changes[-2000:]

    def changes_since(self, since: int) -> list[dict]:
        return [c for c in self._changes if c["seq"] > since]

    def push_notification(self, entry: dict) -> dict:
        self._notify_seq += 1
        row = {"id": self._notify_seq, **entry}
        self._notifications.append(row)
        if len(self._notifications) > 200:
            self._notifications = self._notifications[-200:]
        return row

    def notifications_since(self, since: int = 0) -> list[dict]:
        return [n for n in self._notifications if n["id"] > since]


class BridgeError(Exception):
    status = 400
    title = "Error"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class Unauthorized(BridgeError):
    status = 401
    title = "Unauthorized"


class NotFound(BridgeError):
    status = 404
    title = "Not Found"


class BadRequest(BridgeError):
    status = 400
    title = "Bad Request"


class Forbidden(BridgeError):
    status = 403
    title = "Forbidden"


def _auth(
    request: Request,
    x_bridge_psk: str | None = Header(default=None, alias="X-Bridge-PSK"),
    x_bridge_ts: str | None = Header(default=None, alias="X-Bridge-Ts"),
    x_bridge_nonce: str | None = Header(default=None, alias="X-Bridge-Nonce"),
    x_bridge_sign: str | bytes | None = Header(default=None, alias="X-Bridge-Sign"),
) -> None:
    cfg = request.app.state.bridge.config
    nonces: NonceStore = request.app.state.bridge.nonces
    # ① PSK
    if not x_bridge_psk or x_bridge_psk != cfg.psk:
        log.warning("桥请求被拒：PSK 缺失或错误", extra={"nonce": (x_bridge_nonce or "")[:8]})
        raise Unauthorized("PSK 缺失或错误")
    # ② 时间戳
    try:
        ts = check_timestamp(x_bridge_ts)
    except ValueError as exc:
        raise Unauthorized(str(exc)) from exc
    # ③ nonce 重放
    if not x_bridge_nonce:
        raise Unauthorized("缺少 nonce")
    if nonces.seen(x_bridge_nonce):
        raise Unauthorized("nonce 已使用（疑似重放）")
    # ④ 签名 —— body 从 middleware 缓存读；★ 必须把 x_bridge_sign 传给 verify_sig
    body = request.scope.get("_bridge_body") or b""
    if not x_bridge_sign or not verify_sig(
        cfg.psk,
        request.method,
        _canonical_path(request),
        ts,
        x_bridge_nonce,
        x_bridge_sign,
        body,
    ):
        log.warning("桥请求被拒：签名错误", extra={"nonce": str(x_bridge_nonce)[:8]})
        raise Unauthorized("签名校验失败")
    nonces.add(x_bridge_nonce)


def create_bridge_app(
    config: BridgeConfig | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    cfg = config or load_config(config_path or (Path(__file__).resolve().parent / "config.yaml"))
    state = BridgeState(cfg)

    app = FastAPI(title="Alongside Note Bridge", version=BRIDGE_VERSION)
    app.state.bridge = state

    @app.middleware("http")
    async def _cache_body(request: Request, call_next):
        # 鉴权前把 body 读出来缓存（仅 /bridge/*），供签名校验使用。
        # ★ 兼容 BaseHTTPMiddleware：用 receive 通道拼装，避免 body 被吃掉。
        if request.url.path.startswith("/bridge/"):
            raw = b""
            try:
                if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                    chunks: list[bytes] = []
                    receive = request.receive

                    async def _recv():
                        message = await receive()
                        if message["type"] == "http.request":
                            chunks.append(message.get("body", b""))
                        return message

                    request._receive = _recv  # type: ignore[attr-defined]
                    # 先走一遍 receive 拿 body（ASGI 消息可能分片）
                    more = True
                    while more:
                        message = await _recv()
                        if message["type"] != "http.request":
                            break
                        more = message.get("more_body", False)
                    raw = b"".join(chunks)
                    # 重置 receive，让下游仍能读 body
                    body_sent = False

                    async def _replay():
                        nonlocal body_sent
                        if not body_sent:
                            body_sent = True
                            return {
                                "type": "http.request",
                                "body": raw,
                                "more_body": False,
                            }
                        return {"type": "http.disconnect"}

                    request._receive = _replay  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                raw = b""
            request.scope["_bridge_body"] = raw
        return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "service": "bridge"}

    @app.get("/bridge/healthz")
    def bridge_healthz(_: None = Depends(_auth)) -> dict:
        return {
            "ok": True,
            "version": BRIDGE_VERSION,
            "capabilities": ["notes", "notify"],
            "libs": [
                {"id": lib.id, "name": lib.name, "enabled": lib.enabled, "mode": lib.mode}
                for lib in cfg.libs
            ],
        }

    @app.get("/bridge/libs")
    def bridge_libs(_: None = Depends(_auth)) -> dict:
        return {
            "libs": [
                {
                    "id": lib.id,
                    "name": lib.name,
                    "path": lib.path,
                    "mode": lib.mode,
                    "enabled": lib.enabled,
                    "md_count": count_md(lib),
                }
                for lib in cfg.libs
            ]
        }

    @app.get("/bridge/scan")
    def bridge_scan(
        lib: str = Query(...),
        limit: int = Query(5000, ge=1, le=5000),
        _: None = Depends(_auth),
    ) -> StreamingResponse:
        lib_cfg = cfg.lib(lib)
        if lib_cfg is None:
            raise NotFound(f"未知库：{lib}")
        if not lib_cfg.enabled:
            raise NotFound(f"库已禁用：{lib}")
        entries = scan_lib(lib_cfg, limit=limit)

        def _gen():
            for e in entries:
                yield json.dumps(e, ensure_ascii=False) + "\n"

        return StreamingResponse(
            _gen(),
            media_type="application/x-ndjson",
            headers={"X-Index-Count": str(len(entries))},
        )

    @app.get("/bridge/read")
    def bridge_read(
        lib: str = Query(...),
        path: str = Query(..., alias="path"),
        _: None = Depends(_auth),
    ) -> dict:
        lib_cfg = cfg.lib(lib)
        if lib_cfg is None:
            raise NotFound(f"未知库：{lib}")
        if not lib_cfg.enabled:
            raise NotFound(f"库已禁用：{lib}")
        try:
            note = read_note(lib_cfg, path)
        except ValueError as exc:  # 路径穿越 / 越界
            raise BadRequest(str(exc)) from exc
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        return note

    # ── ★ Work-Review 代理（astrbot 下场 · 主人「修数据链路」）───────────
    # 背景：Work-Review 跑在主人本机 127.0.0.1:49996，**服务器上的 127.0.0.1 不是它**
    #       → 复盘「手动同步」必然失败（`WORK_REVIEW_BRIDGE` 是半成品开关，只改错误映射不改 URL）。
    # 本代理：把服务器的请求经桥（frp 隧道）转发到主人本机 Work-Review。
    # 安全：PSK 四重鉴权 + **只许转发到配置的 work_review 上游**（防 SSRF）。
    @app.api_route(
        "/bridge/work-review/{wr_path:path}",
        methods=["GET", "POST"],
    )
    async def bridge_work_review(
        request: Request,
        wr_path: str,
        _: None = Depends(_auth),
    ) -> Response:
        upstream = cfg.work_review_base_url
        if not upstream:
            raise BadRequest("桥未配置 work_review_base_url（主人侧 config.yaml）")
        # 只许相对路径，禁止穿越 / 绝对 URL（防 SSRF）
        if ".." in wr_path or wr_path.startswith("//"):
            raise BadRequest("非法上游路径")
        target = f"{upstream.rstrip('/')}/{wr_path.lstrip('/')}"
        qs = request.url.query
        if qs:
            target = f"{target}?{qs}"
        body = request.scope.get("_bridge_body") or b""
        fwd_headers = {}
        auth = request.headers.get("authorization")
        if auth:
            fwd_headers["Authorization"] = auth
        ct = request.headers.get("content-type")
        if ct:
            fwd_headers["Content-Type"] = ct
        try:
            resp = httpx.request(
                request.method, target, headers=fwd_headers, content=body or None, timeout=15.0
            )
        except httpx.HTTPError as exc:
            raise BridgeError(f"Work-Review 不可达：{exc}") from exc
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )

    @app.get("/bridge/attachment")
    def bridge_attachment(
        lib: str = Query(...),
        path: str = Query(..., alias="path"),
        _: None = Depends(_auth),
    ) -> Response:
        """★ 读二进制附件（图片等）· astrbot 下场 · 主人⑤「ob 附件、图片插入要能正常展示」。

        安全：与 /bridge/read 同款 —— safe_resolve 双守卫（路径穿越 + 越界）。
        返回：原始字节 + 按扩展名推断的 Content-Type（未知 → application/octet-stream）。
        """
        lib_cfg = cfg.lib(lib)
        if lib_cfg is None:
            raise NotFound(f"未知库：{lib}")
        if not lib_cfg.enabled:
            raise NotFound(f"库已禁用：{lib}")
        try:
            data, mime = read_binary(lib_cfg, path)
        except ValueError as exc:  # 路径穿越 / 越界
            raise BadRequest(str(exc)) from exc
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        return Response(
            content=data,
            media_type=mime,
            headers={"Cache-Control": "private, max-age=300"},
        )

    @app.get("/bridge/changes")
    def bridge_changes(
        since: int = Query(0, ge=0),
        _: None = Depends(_auth),
    ) -> dict:
        return {"since": since, "changes": state.changes_since(since)}

    @app.post("/bridge/write")
    async def bridge_write(
        request: Request,
        lib: str = Query(...),
        path: str = Query(..., alias="path"),
        _: None = Depends(_auth),
    ) -> dict:
        lib_cfg = cfg.lib(lib)
        if lib_cfg is None:
            raise NotFound(f"未知库：{lib}")
        if lib_cfg.mode != "rw":
            raise Forbidden("该库为只读（mode=ro），写回已禁用")
        body = await request.body()
        target = lib_cfg.abs_path() / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        log.info("桥写回成功", extra={"lib": lib, "path": path})
        return {"ok": True, "rel_path": path}

    @app.post("/bridge/notify")
    async def bridge_notify(request: Request, _: None = Depends(_auth)) -> dict:
        """桌面通知（T24）。JSON: {title, body, app_id?, channel?}。"""
        raw = request.scope.get("_bridge_body") or b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise BadRequest("notify body 必须是 JSON") from exc
        if not isinstance(payload, dict):
            raise BadRequest("notify body 必须是 JSON 对象")
        title = str(payload.get("title") or "Alongside").strip()[:120]
        body = str(payload.get("body") or "").strip()[:2000]
        if not body:
            raise BadRequest("notify 需要非空 body")
        app_id = str(payload.get("app_id") or "Alongside").strip()[:80]
        channel = str(payload.get("channel") or "auto").strip()[:20]
        result = send_windows_notification(title, body, app_id=app_id, channel=channel)
        row = state.push_notification(
            {
                "ts": int(time.time()),
                "title": title,
                "body_len": len(body),
                "channel": result.channel,
                "ok": result.ok,
                "detail": result.detail[:200],
            }
        )
        if not result.ok:
            log.warning("桥通知失败 id=%s channel=%s", row["id"], result.channel)
        return {"ok": result.ok, "id": row["id"], **result.as_dict()}

    @app.get("/bridge/notifications")
    def bridge_notifications(
        since: int = Query(0, ge=0),
        _: None = Depends(_auth),
    ) -> dict:
        items = state.notifications_since(since)
        return {"since": since, "items": items}

    _install_error_handlers(app)
    return app


def _install_error_handlers(app: FastAPI) -> None:
    def _make(exc: BridgeError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={
                "type": "about:blank",
                "title": exc.title,
                "status": exc.status,
                "detail": exc.detail,
            },
            headers={"Content-Type": "application/problem+json"},
        )

    for cls in (Unauthorized, NotFound, BadRequest, Forbidden):
        app.add_exception_handler(cls, lambda req, exc, _c=cls: _make(exc))  # type: ignore


if __name__ == "__main__":
    # 只监听本机回环；端口由 run.ps1 / 部署决定（默认 8790，自测用 8791）。
    import os

    import uvicorn

    _app = create_bridge_app(config_path=os.environ.get("BRIDGE_CONFIG") or None)
    uvicorn.run(
        _app,
        host="127.0.0.1",
        port=int(os.environ.get("BRIDGE_PORT", "8790")),
        reload=False,
    )
