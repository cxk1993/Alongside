"""桥客户端：云侧签名请求本机桥（services/bridge）。

★ 签名算法必须与 services/bridge/protocol.py **逐字节一致**：
      f"{METHOD}|{path}|{ts}|{nonce}|{body}"
★ notes 插件内部使用；`notify()` 为 T24/T25 通用桥能力（桌面通知），
  其他插件**不要 import 本模块**——T25 日程侧待 ISSUE-005 适配器落地后再接。
★ 超时要短（桥在本机，frp 隧道也应在秒级）；失败抛 BridgeError，由 service 转成 AppError。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable, Iterable
from typing import Any

import httpx

from core.config import read_setting


# 与 bridge.protocol 同一 canonical 格式
def _canonical(method: str, path: str, ts: int, nonce: str, body: bytes) -> str:
    return f"{method.upper()}|{path}|{ts}|{nonce}|{body.decode('utf-8', 'replace')}"


def _sign(psk: str, method: str, path: str, ts: int, nonce: str, body: bytes = b"") -> str:
    msg = _canonical(method, path, ts, nonce, body).encode("utf-8")
    return hmac.new(psk.encode("utf-8"), msg, hashlib.sha256).hexdigest()


class BridgeError(Exception):
    """桥不可达 / 鉴权失败 / 返回异常。"""

    def __init__(self, detail: str, status: int = 502) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


class BridgeClient:
    """对本机桥的签名 HTTP 客户端。path 必须带查询串（与桥侧 _canonical_path 一致）。"""

    def __init__(
        self,
        base_url: str | None = None,
        psk: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        # ★ 统一走 core.config.read_setting（os.environ 优先，缺失回退项目根 .env）。
        #   背景：Settings 未声明 bridge_* 字段（extra="ignore" 吞值），getattr(settings,...)
        #   恒为空 → 生产报「未配置 BRIDGE_URL」。与 finance 模块 BeeCount 同步故障同源，
        #   修法同先例（见 beecount_mcp.py:49 注释）。构造参数显式注入仍最优先（测试用）。
        env_base = read_setting("BRIDGE_URL", "") or ""
        env_psk = read_setting("BRIDGE_PSK", "") or ""
        self.base_url = (base_url or env_base).rstrip("/")
        self.psk = psk or env_psk
        self.timeout = timeout

    def _headers(self, method: str, path: str, body: bytes = b"") -> dict[str, str]:
        if not self.psk:
            raise BridgeError("未配置 BRIDGE_PSK，无法访问本机桥", status=503)
        ts = int(time.time())
        nonce = uuid.uuid4().hex
        return {
            "X-Bridge-PSK": self.psk,
            "X-Bridge-Ts": str(ts),
            "X-Bridge-Nonce": nonce,
            "X-Bridge-Sign": _sign(self.psk, method, path, ts, nonce, body),
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        if not self.base_url:
            raise BridgeError("未配置 BRIDGE_URL", status=503)
        # 签名覆盖 path+query：先把 query 拼进 path 再签
        from urllib.parse import urlencode

        qs = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        sign_path = path + (f"?{qs}" if qs else "")
        body = b""
        headers = self._headers(method, sign_path, body)
        url = self.base_url + sign_path
        try:
            resp = httpx.request(
                method, url, headers=headers, content=body or None, timeout=self.timeout
            )
        except httpx.HTTPError as exc:
            raise BridgeError(f"桥不可达：{exc}") from exc
        if resp.status_code >= 400:
            raise BridgeError(
                f"桥返回 {resp.status_code}：{resp.text[:200]}",
                status=502 if resp.status_code >= 500 else resp.status_code,
            )
        return resp.json()

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/bridge/healthz")

    def libs(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/bridge/libs")
        return data if isinstance(data, list) else data.get("libs", [])

    def scan(self, lib: str, limit: int = 5000) -> list[dict[str, Any]]:
        """全量索引。桥侧是 ndjson 流；这里聚成 list（v0.1 库规模可控）。"""
        if not self.base_url:
            raise BridgeError("未配置 BRIDGE_URL", status=503)
        qs = f"lib={lib}&limit={limit}"
        sign_path = f"/bridge/scan?{qs}"
        headers = self._headers("GET", sign_path)
        try:
            resp = httpx.get(self.base_url + sign_path, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise BridgeError(f"桥不可达：{exc}") from exc
        if resp.status_code >= 400:
            raise BridgeError(f"桥返回 {resp.status_code}：{resp.text[:200]}")
        items: list[dict[str, Any]] = []
        for line in resp.text.splitlines():
            line = line.strip()
            if line:
                items.append(__import__("json").loads(line))
        return items

    def read(self, lib: str, path: str) -> dict[str, Any]:
        return self._request("GET", "/bridge/read", params={"lib": lib, "path": path})

    def attachment(self, lib: str, path: str) -> tuple[bytes, str]:
        """★ 读二进制附件（图片等）· astrbot 下场 · 主人⑤「ob 附件、图片插入要能正常展示」。

        返回 (bytes, content_type)。签名与 /bridge/read 同款（GET + query 参与签名）。
        与 _request 的区别：**不做 .json()**，要原始字节。
        """
        if not self.base_url:
            raise BridgeError("未配置 BRIDGE_URL", status=503)
        from urllib.parse import urlencode

        qs = urlencode({"lib": lib, "path": path})
        sign_path = f"/bridge/attachment?{qs}"
        headers = self._headers("GET", sign_path)
        try:
            resp = httpx.get(self.base_url + sign_path, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise BridgeError(f"桥不可达：{exc}") from exc
        if resp.status_code >= 400:
            raise BridgeError(
                f"桥返回 {resp.status_code}：{resp.text[:200]}",
                status=502 if resp.status_code >= 500 else resp.status_code,
            )
        return resp.content, resp.headers.get("content-type", "application/octet-stream")

    def notify(
        self,
        title: str,
        body: str,
        *,
        app_id: str = "Alongside",
        channel: str = "auto",
    ) -> dict[str, Any]:
        """T24/T25：经本机桥弹 Windows 桌面通知。

        body JSON 参与 HMAC 签名（与桥侧 protocol 一致）。
        channel: auto|winotify|powershell|log。
        """
        if not self.base_url:
            raise BridgeError("未配置 BRIDGE_URL", status=503)
        payload = {
            "title": title,
            "body": body,
            "app_id": app_id,
            "channel": channel,
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        path = "/bridge/notify"
        headers = self._headers("POST", path, raw)
        headers["Content-Type"] = "application/json; charset=utf-8"
        try:
            resp = httpx.post(
                self.base_url + path,
                headers=headers,
                content=raw,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise BridgeError(f"桥不可达：{exc}") from exc
        if resp.status_code >= 400:
            raise BridgeError(
                f"桥返回 {resp.status_code}：{resp.text[:200]}",
                status=502 if resp.status_code >= 500 else resp.status_code,
            )
        data = resp.json()
        return data if isinstance(data, dict) else {"ok": False, "detail": "bad response"}


def dispatch_due_notifies(
    items: Iterable[dict[str, Any]],
    client: BridgeClient,
    *,
    title_prefix: str = "Alongside 提醒",
    channel: str = "auto",
    on_error: Callable[[dict[str, Any], Exception], None] | None = None,
) -> list[dict[str, Any]]:
    """T25 调度骨架：把「到期提醒项」推到本机桥。

    items 每项：{"id": ..., "title": str, "body": str?}
    ★ 不 import calendar/todo —— 调用方自己查库或打 API 后注入列表（ADR-0002）。
    ★ 单条失败不中断整批；默认吞错记在结果里。
    """
    results: list[dict[str, Any]] = []
    for item in items:
        title = str(item.get("title") or "提醒").strip()[:120]
        body = str(item.get("body") or title).strip()[:2000]
        row: dict[str, Any] = {"id": item.get("id"), "title": title}
        try:
            out = client.notify(
                f"{title_prefix}: {title}" if title_prefix else title,
                body,
                channel=channel,
            )
            row.update({"ok": bool(out.get("ok")), "bridge": out})
        except BridgeError as exc:
            row.update({"ok": False, "error": exc.detail})
            if on_error is not None:
                on_error(item, exc)
        results.append(row)
    return results
