"""WE Learn 只读客户端（已用真实账号校准，2026-11-15）。

════════════════════════════════════════════════════════════════════
★ 与雨课堂不同，WE Learn 是 ASP.NET 服务端渲染：
  · 课程列表是 JSON：`/ajax/authCourse.aspx?action=gmc`
  · 单元与进度在 `/student/course_info.aspx?cid=` 的 HTML 里
  → 所以这个客户端只做两件事：带 Cookie 发请求、把响应原样交出去；
    解析 HTML 在 router.py（平台改版只改解析）。
★ 鉴权只靠两个 Cookie（`.AspNet.Cookies` + `ASP.NET_SessionId`），
  没有签名、没有风控参数——这是五家里第二个能服务端直连的。
════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0 Safari/537.36 Alongside-welearn/0.1"
)
MAX_BYTES = 4_000_000
TIMEOUT_SECONDS = 15.0
MIN_INTERVAL = float(os.environ.get("WELEARN_MIN_INTERVAL", "0.35"))

COURSES_PATH = "/ajax/authCourse.aspx"
COURSE_PAGE_PATH = "/student/course_info.aspx"
RECENT_PATH = "/ajax/authCourse.aspx"

_ALLOWED_HOST_SUFFIXES = (".sflep.com",)

_rate_lock = threading.Lock()
_last_call = 0.0


class WelearnError(Exception):
    def __init__(self, message: str, *, kind: str = "upstream") -> None:
        super().__init__(message)
        self.kind = kind  # auth | network | upstream | shape


def _throttle() -> None:
    global _last_call
    with _rate_lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _check_base(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise WelearnError("base_url 必须是 http(s) 地址", kind="shape")
    host = (parsed.hostname or "").lower()
    if not host or not any(host.endswith(s) for s in _ALLOWED_HOST_SUFFIXES):
        raise WelearnError(f"base_url 的域名 {host!r} 不在允许列表（*.sflep.com）", kind="shape")
    return f"{parsed.scheme}://{parsed.netloc}"


class WelearnClient:
    """一个会话 = 一条完整 Cookie 串。"""

    def __init__(self, cookie: str, base_url: str = "https://welearn.sflep.com") -> None:
        cookie = (cookie or "").strip()
        if ".AspNet.Cookies" not in cookie and "ASP.NET_SessionId" not in cookie:
            raise WelearnError("Cookie 里缺少 .AspNet.Cookies / ASP.NET_SessionId", kind="auth")
        self._cookie = cookie
        self._base = _check_base(base_url)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        _throttle()
        headers = {
            "Cookie": self._cookie,
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html, */*",
            "Referer": self._base + "/",
        }
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as c:
                r = c.get(self._base + path, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise WelearnError(f"连不上 WE Learn：{exc}", kind="network") from exc
        if len(r.content) > MAX_BYTES:
            raise WelearnError("响应过大，已拒绝解析", kind="upstream")
        if "/login" in str(r.url) or "prelogin" in str(r.url):
            raise WelearnError("被重定向到登录页，Cookie 已失效", kind="auth")
        if r.status_code >= 400:
            raise WelearnError(f"WE Learn 返回 HTTP {r.status_code}", kind="upstream")
        return r

    def courses(self) -> Any:
        r = self._get(COURSES_PATH, params={"action": "gmc"})
        try:
            data = r.json()
        except ValueError as exc:
            raise WelearnError("课程列表不是 JSON（可能被踢回登录页）", kind="auth") from exc
        if str(data.get("ret")) != "0":
            raise WelearnError(f"课程列表返回 ret={data.get('ret')}", kind="auth")
        return data

    def recent(self) -> Any:
        return self._get(RECENT_PATH, params={"action": "grecentc"}).json()

    def course_page(self, cid: str) -> str:
        return self._get(COURSE_PAGE_PATH, params={"cid": cid}).text
