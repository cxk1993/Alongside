"""iSDU 插件的安全回归：路径白名单 / 票据不入库 / 本机判断 / 上游异常映射。

跑法：
    cd services/api && .venv/Scripts/python -m pytest tests/test_isdu_security.py -q
"""

from __future__ import annotations

import pytest

from core.errors import ServiceUnavailableError, ValidationError
from modules.isdu.router import _RAW_PATH_RE
from modules.isdu.service import (
    _DEVICE_RE,
    _calendar_from_raw,
    _unwrap,
    request_is_local,
)

# ── /raw 路径白名单 ──────────────────────────────────────────────────
ALLOWED_RAW = [
    "/v2/api/edu/course_schedule",
    "/v2/api/edu/grades",
    "/v2/api/edu/studyrooms/data",
]
BLOCKED_RAW = [
    "/v2/api/edu/../core/card/bill",  # httpx 会归一化成 /v2/api/core/...
    "/v2/api/edu/%2e%2e/core/card/bill",
    "/v2/api/edu/",
    "/v2/api/core/news",
    "//evil.example/x",
    "https://i.sdu.edu.cn/isduapi/v2/api/edu/grades",
    "/v2/api/edu/grades?x=1",
    "/v2/api/edu/./grades",
]


def test_raw_path_whitelist_allows_only_simple_edu_paths() -> None:
    for path in ALLOWED_RAW:
        assert _RAW_PATH_RE.fullmatch(path), path
    for path in BLOCKED_RAW:
        assert not _RAW_PATH_RE.fullmatch(path), path


# ── device 头注入防护 ───────────────────────────────────────────────
def test_device_cookie_value_is_restricted() -> None:
    for value in ("abc123", "F5BEA4F1-BBF6", "a.b_c~d%3De-f", "x" * 128):
        assert _DEVICE_RE.fullmatch(value), value
    for value in ("abc;def", "a\r\nb", "a b", "x" * 129, "中文", ""):
        assert not _DEVICE_RE.fullmatch(value), value


# ── 上游返回映射：40101 提示重登，其他错误 503，无外壳原样返回 ──────────
def test_unwrap_maps_upstream_errors() -> None:
    assert _unwrap("/x", {"code": 0, "data": [1, 2]}) == [1, 2]
    assert _unwrap("/x", {"id": "raw-resource", "title": "t"})["id"] == "raw-resource"

    with pytest.raises(ValidationError, match="重新登录"):
        _unwrap("/x", {"code": 40101, "message": "登录状态已失效"})
    with pytest.raises(ServiceUnavailableError):
        _unwrap("/x", {"code": 50000, "message": "boom"})


# ── 本机判断：任何 forwarded 头指向非回环都拒绝 ──────────────────────
def test_request_is_local_rejects_forwarded_headers() -> None:
    assert request_is_local("127.0.0.1", None) is True
    assert request_is_local("::1", None) is True
    assert request_is_local("10.0.0.2", None) is False
    # 反向代理只带 X-Real-IP（XFF 为空）时也要拒绝
    assert request_is_local("127.0.0.1", None, {"x-real-ip": "203.0.113.9"}) is False
    assert request_is_local("127.0.0.1", None, {"cf-connecting-ip": "203.0.113.9"}) is False
    forwarded = {"forwarded": "for=203.0.113.9;proto=https"}
    assert request_is_local("127.0.0.1", None, forwarded) is False
    local_headers = {"x-real-ip": "127.0.0.1"}
    assert request_is_local("127.0.0.1", "127.0.0.1", local_headers) is True


# ── 校历解析：脏数据不炸、月份/周次过滤 ──────────────────────────────
def test_calendar_from_raw_tolerates_bad_upstream() -> None:
    cal = _calendar_from_raw(
        {
            "8": [[28, "教工上班", 0, 0], [31, "学生上课", 1, 0]],
            "9": [[10, "教师节", 2, 1]],
            "13": [[1, "不存在的月份", 1, 0]],
            "comments": ["本学期共 20 周"],
            "start": [2026, 8, 31],
            "weeks": "bad",
        }
    )
    assert cal["start_date"] == "2026-08-31"
    assert cal["weeks"] == 0
    assert [m["month"] for m in cal["months"]] == [8, 9]
    assert cal["months"][0]["year"] == 2026

    # start 畸形：不能抛异常
    cal2 = _calendar_from_raw({"start": ["oops", None, 3], "weeks": 10, "1": [[1, "元旦", 18, 1]]})
    assert cal2["start_date"] is None
    assert cal2["months"][0]["year"] is None
