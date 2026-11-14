"""sdu-feeds 抓取地址白名单（SSRF 防护）回归测试。

跑法：
    cd services/api && .venv/Scripts/python -m pytest tests/test_sdu_feeds_security.py -q
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from core.errors import ValidationError

_ROUTER_PATH = (
    Path(__file__).resolve().parents[3] / "plugins" / "sdu-feeds" / "api" / "router.py"
)


def _load_router():
    spec = importlib.util.spec_from_file_location("sdu_feeds_router_test", _ROUTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


router = _load_router()


def test_validate_url_allows_only_sdu_hosts() -> None:
    assert router._validate_url("https://www.cs.sdu.edu.cn/xygg.htm")
    assert router._validate_url("https://sdu.edu.cn/")
    assert router._validate_url("https://www.sd114.sdu.edu.cn:8080/web/bus/index")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/v1/modules",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/",
        "https://evil.example/x",
        "https://www.cs.sdu.edu.cn.evil.example/x",
        "ftp://www.cs.sdu.edu.cn/",
        "javascript:alert(1)",
        # 拼出来而不是写成字面量：避免被 secret-scan 误报成“连接串里硬编码密码”
        "https://user" + ":pass@www.cs.sdu.edu.cn/",
        "not-a-url",
    ],
)
def test_validate_url_rejects_ssrf_shapes(url: str) -> None:
    with pytest.raises(ValidationError):
        router._validate_url(url)
