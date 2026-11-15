"""雨课堂只读客户端（已用真实会话校准，2026-11-15）。

════════════════════════════════════════════════════════════════════
★ 校准来源：用你自己的账号在 Chrome 里登录 changjiang.yuketang.cn，
  抓到的真实请求。下面每条路径都在真实会话下验证过 200。
★ 区域差异：雨课堂分标准版（www）与学校私有云（如 changjiang）。
  同一份 cookie 只在它自己的域下有效；`base_url` 填错会得到 401/302。
★ 这个文件只做三件事：带会话发 GET、限速、把失败翻译成可读错误；
  业务字段的解释在 router.py，平台改版时只需要改那边。
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
    "(KHTML, like Gecko) Chrome/156.0 Safari/537.36 Alongside-yuketang/0.1"
)

MAX_BYTES = 4_000_000
TIMEOUT_SECONDS = 12.0
MIN_INTERVAL = float(os.environ.get("YUKETANG_MIN_INTERVAL", "0.35"))
#: 实测（2026-11-15）：章节接口要求 Cookie 里带 `xtbz`，否则返回
#: `{"msg":"XTBZ IS REQUIRED","error_code":40000}`。它是平台标记，不是用户凭据。
XTBZ = os.environ.get("YUKETANG_XTBZ", "ykt")
#: 平台标记（长江雨课堂 = 3）。学校私有云若不同，可用环境变量覆盖。
PLATFORM_ID = os.environ.get("YUKETANG_PLATFORM_ID", "3")

# ════════════════════════ 已校准的端点 ════════════════════════
USER_PATH = "/v2/api/web/userinfo"
COURSES_PATH = "/v2/api/web/courses/list"          # ?identity=2 = 学生视角
CLASSROOM_PATH = "/v2/api/web/classrooms/{cid}"    # ?role=5
ACTIVITIES_PATH = "/v2/api/web/logs/learn/{cid}"   # ?actype=-1&page=0&offset=N&sort=-1
NOTICES_PATH = "/v/discussion/v2/announcements/"   # ?cid=&limit=&offset=0&type=9
CHAPTER_PATH = "/mooc-api/v1/lms/learn/course/chapter"  # ?cid=&sign=&term=latest&uv_id=

_ALLOWED_HOST_SUFFIXES = (".yuketang.cn", ".rainclassroom.com", ".xuetangx.com")

#: 仅本机调试才允许降级到明文 http / 非 443 端口。默认关闭：
#: 明文出网会把 sessionid 直接送上公网，非标准端口则是受限探针。
_ALLOW_INSECURE = os.environ.get("YUKETANG_ALLOW_INSECURE", "") == "1"

_rate_lock = threading.Lock()
_last_call = 0.0


class YuketangError(Exception):
    """所有失败的统一形态。`kind` 决定上层怎么回复用户。"""

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


def _check_host(base_url: str) -> str:
    """只允许雨课堂自己的域名——防止把会话发给任意地址。

    三层收口（默认全开，`YUKETANG_ALLOW_INSECURE=1` 才放开后两层）：
    ① 必须是 http(s)；② 域名必须命中后缀白名单；③ 必须 https 且端口是 443。
    """
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise YuketangError("base_url 必须是 http(s) 地址", kind="shape")
    host = (parsed.hostname or "").lower()
    if not host or not any(host.endswith(suffix) for suffix in _ALLOWED_HOST_SUFFIXES):
        raise YuketangError(
            f"base_url 的域名 {host!r} 不在允许列表（*.yuketang.cn / *.rainclassroom.com）",
            kind="shape",
        )
    if not _ALLOW_INSECURE:
        if parsed.scheme != "https":
            raise YuketangError(
                "base_url 必须用 https —— 明文 http 会让 sessionid 走上公网"
                "（确实需要时设 YUKETANG_ALLOW_INSECURE=1）",
                kind="shape",
            )
        if parsed.port not in (None, 443):
            raise YuketangError(
                f"base_url 只允许 443 端口，收到 {parsed.port}"
                "（确实需要时设 YUKETANG_ALLOW_INSECURE=1）",
                kind="shape",
            )
    return f"{parsed.scheme}://{parsed.netloc}"


class YuketangClient:
    """一个会话 = 一个 client。不要在请求之间共享可变状态。"""

    def __init__(self, session_id: str, base_url: str = "https://www.yuketang.cn") -> None:
        if not session_id:
            raise YuketangError("缺少 sessionid", kind="auth")
        self._base = _check_host(base_url)
        self._session_id = session_id

    def get_json(
        self, path: str, params: dict[str, Any] | None = None, extra_headers: dict[str, str] | None = None
    ) -> Any:
        _throttle()
        headers = {
            "Cookie": f"sessionid={self._session_id}; xtbz={XTBZ}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": self._base + "/",
            # ★ 实测（2026-11-15）：mooc-api 的章节接口不认 Cookie 里的 xtbz，
            #   必须带下面这几个**请求头**，否则 40000 "XTBZ IS REQUIRED"。
            "xtbz": XTBZ,
            "x-client": "web",
            "terminal-type": "web",
            "platform-id": PLATFORM_ID,
        }
        if extra_headers:
            headers.update(extra_headers)
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False) as c:
                r = c.get(self._base + path, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise YuketangError(f"连不上雨课堂：{exc}", kind="network") from exc

        if r.status_code in (401, 403):
            raise YuketangError("会话已失效，请重新复制 sessionid", kind="auth")
        if r.status_code in (301, 302, 303, 307, 308):
            raise YuketangError("被重定向到登录页：sessionid 无效，或 base_url 不是学校所在区域", kind="auth")
        if r.status_code >= 400:
            raise YuketangError(f"雨课堂返回 HTTP {r.status_code}", kind="upstream")
        if len(r.content) > MAX_BYTES:
            raise YuketangError("响应过大，已拒绝解析", kind="upstream")

        try:
            return r.json()
        except ValueError as exc:
            raise YuketangError("返回的不是 JSON（多半是被打回登录页）", kind="auth") from exc

    # ── 业务 ────────────────────────────────────────────────────

    def userinfo(self) -> Any:
        return self.get_json(USER_PATH)

    def courses(self) -> Any:
        return self.get_json(COURSES_PATH, params={"identity": 2})

    def classroom(self, classroom_id: str) -> Any:
        return self.get_json(CLASSROOM_PATH.format(cid=classroom_id), params={"role": 5})

    def activities(self, classroom_id: str, *, limit: int = 20) -> Any:
        return self.get_json(
            ACTIVITIES_PATH.format(cid=classroom_id),
            params={"actype": -1, "page": 0, "offset": limit, "sort": -1},
        )

    def notices(self, classroom_id: str, *, limit: int = 10) -> Any:
        return self.get_json(
            NOTICES_PATH,
            params={"content": "", "cid": classroom_id, "limit": limit, "offset": 0, "type": 9},
        )

    def chapter(self, classroom_id: str, sign: str, uv_id: str) -> Any:
        return self.get_json(
            CHAPTER_PATH,
            params={
                "cid": classroom_id,
                "sign": sign,
                "term": "latest",
                "uv_id": uv_id,
                "classroom_id": classroom_id,
            },
            extra_headers={"university-id": uv_id},
        )
