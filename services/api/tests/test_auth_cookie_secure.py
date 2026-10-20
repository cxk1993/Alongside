"""BUG-T30-1 回归测试：refresh cookie 的 Secure 属性可配置化（AUTH_COOKIE_SECURE）。

## 背景（Qoder 2026-09-20 立案，Zcode 裁决方案 a）

旧实现 ``_cookie_secure() = app_env == "production"``——生产是明文 http 直连，
Set-Cookie 却带 Secure，浏览器在 http 源**拒存 Secure cookie**，refresh 链路
永远拿不到凭证，access 过期（默认 30 分钟）即被踢回登录页。

## 修复语义（本文件锁死）

- ``AUTH_COOKIE_SECURE`` 缺省 ``auto``：https（含反代头 X-Forwarded-Proto: https）
  → Secure；明文 http → 无 Secure（★ 核心回归：http 下 refresh cookie 可存可发）；
- 显式 ``false``：无 Secure（生产 .env 当前语义，直连 http 场景）；
- 显式 ``true``：始终下发 Secure（http 下浏览器不存——显式配置者自己的选择，
  语义按实现：服务器侧保证下发，浏览器侧行为由 RFC 6265bis 决定）。

不 mock 加密逻辑：真 argon2 哈希 + 真 JWT 签发/验签；monkeypatch 只替换
get_settings 绑定（test_auth_totp_switch.py 同款做法），并全程显式 setenv
AUTH_COOKIE_SECURE（read_setting 的 os.environ 优先级高于 .env 回退，测试不受
真实 .env 干扰）。
"""
from __future__ import annotations

import os

os.environ["DB_PATH"] = "./data/tmp_cookie_secure.db"

import pytest  # noqa: E402
from argon2 import PasswordHasher  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import security as security_mod  # noqa: E402
from modules.auth import router as router_mod  # noqa: E402
from modules.auth import service as auth_service_mod  # noqa: E402
from modules.auth.router import REFRESH_COOKIE, router  # noqa: E402

PASSWORD = "cookie-secure-test"


class _FakeSettings:
    """真实凭据形状（真 argon2 哈希 / 真 JWT 密钥），totp 关闭以便仅密码登录。"""

    def __init__(self) -> None:
        self.admin_password_hash = PasswordHasher().hash(PASSWORD)
        self.totp_secret = "JBSWY3DPEHPK3PXP"
        self.totp_required = False
        self.jwt_access_minutes = 30
        self.jwt_refresh_days = 14
        self.secret_key = "test-only-signing-key-not-a-credential"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    fake = _FakeSettings()
    monkeypatch.setattr(auth_service_mod, "get_settings", lambda: fake)
    monkeypatch.setattr(security_mod, "get_settings", lambda: fake)
    monkeypatch.setattr(router_mod, "get_settings", lambda: fake)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _refresh_cookie_of(resp: TestClient) -> str | None:
    """从响应里取 alongside_refresh 的 Set-Cookie 原始行（可能多条，取匹配的那条）。"""
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{REFRESH_COOKIE}="):
            return raw
    return None


def _has_secure_flag(set_cookie_line: str) -> bool:
    """按分号段精确判定 Secure 标志（避免 JWT base64url 值里偶然出现子串误判）。"""
    return any(
        part.strip().lower() == "secure" for part in set_cookie_line.split(";")
    )


def _login(client: TestClient) -> TestClient:
    resp = client.post(
        "/login",
        json={"password": PASSWORD, "totp": ""},
    )
    assert resp.status_code == 200, resp.text
    return resp


# ── ★ 核心回归：auto + 明文 http → 无 Secure → 浏览器可存 ──────────────────


def test_login_refresh_cookie_no_secure_on_http_when_auto(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "auto")
    resp = _login(client)
    line = _refresh_cookie_of(resp)
    assert line is not None
    assert not _has_secure_flag(line), (
        "auto 模式下明文 http 的 refresh cookie 不得带 Secure（BUG-T30-1 核心回归）"
    )


def test_refresh_flow_stores_and_replays_on_http_when_auto(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端：http + auto 下 refresh cookie「可存可发」——login 发出 →
    手动带上它调 /refresh 应 200 并换到新 access。"""
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "auto")
    resp = _login(client)
    line = _refresh_cookie_of(resp)
    assert line is not None
    value = line.split(";", 1)[0].split("=", 1)[1]
    replay = client.post(
        "/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={value}"}
    )
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert body.get("access_token")
    assert body.get("expires_in") == 30 * 60
    replay_line = _refresh_cookie_of(replay)
    assert replay_line is not None and not _has_secure_flag(replay_line)


# ── 显式 false：生产 .env 当前语义 ────────────────────────────────────────


def test_login_refresh_cookie_no_secure_when_explicit_false(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    resp = _login(client)
    line = _refresh_cookie_of(resp)
    assert line is not None and not _has_secure_flag(line)


# ── 显式 true：http 下服务器仍下发 Secure（语义按实现）──────────────────


def test_login_refresh_cookie_has_secure_when_explicit_true_on_http(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    resp = _login(client)
    line = _refresh_cookie_of(resp)
    assert line is not None
    assert _has_secure_flag(line), (
        "显式 true 必须下发 Secure（http 下浏览器不存是配置者自己的选择，"
        "服务器侧语义以实现为准）"
    )


# ── auto + https（两种途径都该 Secure）─────────────────────────────────


def test_login_refresh_cookie_secure_on_https_when_auto(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "auto")
    resp = client.post(
        "https://testserver/login", json={"password": PASSWORD, "totp": ""}
    )
    assert resp.status_code == 200, resp.text
    line = _refresh_cookie_of(resp)
    assert line is not None and _has_secure_flag(line)


def test_login_refresh_cookie_secure_via_forwarded_proto_https(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反代终止 TLS 场景：uvicorn 看到的是 http，但 X-Forwarded-Proto: https
    应被 auto 识别为 https。"""
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "auto")
    resp = client.post(
        "/login",
        json={"password": PASSWORD, "totp": ""},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert resp.status_code == 200, resp.text
    line = _refresh_cookie_of(resp)
    assert line is not None and _has_secure_flag(line)


def test_explicit_true_wins_over_forwarded_proto(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式配置优先于 scheme/反代头判定（auto 才动态判定）。"""
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    resp = client.post(
        "/login",
        json={"password": PASSWORD, "totp": ""},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert resp.status_code == 200, resp.text
    line = _refresh_cookie_of(resp)
    assert line is not None and _has_secure_flag(line)


def test_backward_compat_bool_aliases(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """true/1/yes/on 与 false/0/no/off 都是合法取值（大小写不敏感）。"""
    for raw, expect_secure in (("TRUE", True), ("1", True), ("OFF", False), ("0", False)):
        monkeypatch.setenv("AUTH_COOKIE_SECURE", raw)
        resp = _login(client)
        line = _refresh_cookie_of(resp)
        assert line is not None
        assert _has_secure_flag(line) is expect_secure, f"值 {raw!r} 判定错误"
