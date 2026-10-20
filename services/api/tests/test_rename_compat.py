"""改名兼容回退（旧名 lifeos/lifos → 新名 alongside）的回归测试。

## 为什么需要这个文件

品牌改名把一批**运行时标识符**也换了名字（数据库文件名、refresh cookie、
MCP 来路请求头、备份文件名）。凡是换名，就有一个绕不开的问题：
**已经跑起来的实例手里还捏着旧名字**。

处理口径（主人 2026-10-20 拍板）：
> 全改，并在代码里加**旧名兜底**（新名优先、读不到回退旧名），现有实例不受影响。

于是每处换名都多了一条「旧名仍认」的分支 —— 分支没有测试就是**没有**：
哪天有人顺手把它删了，现象是「升级后所有人被踢下线 / AI 来路的护栏静默失效 /
备份脚本找不到库」，而测试照样全绿。

## 写法纪律（本文件刻意遵守的两条）

1. **每条兼容断言都配正向对照**：光证明「旧名能用」不够 —— 若实现退化成
   「新旧一律放行」，那也是绿的。所以新的必须认、旧的必须认、**别家的必须不认**。
2. **用真实现，不 mock 被测算子**：直接调 `is_mcp_call` / `_resolve_db_path` /
   `_BACKUP_NAME_RE`，只有外部依赖（Settings）用 monkeypatch 替换。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from core import security as security_mod
from core.errors import AppError, handle_app_error
from core.mcp_writes import HEADER, LEGACY_HEADERS, is_mcp_call
from db.engine import LEGACY_DB_NAME, _resolve_db_path
from modules.auth import router as router_mod
from modules.auth import service as auth_service_mod
from modules.auth.router import LEGACY_REFRESH_COOKIE, REFRESH_COOKIE, router
from scripts.backup import _BACKUP_NAME_RE

# ═══════════════ ① MCP 来路标记头（AI 写待办的护栏判据）═══════════════


class _FakeRequest:
    """只带 headers 的最小请求替身（is_mcp_call 只读这一个属性）。"""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_new_header_marks_mcp_call() -> None:
    """正向对照：新头上的 mcp 标记必须仍然生效。"""
    assert is_mcp_call(_FakeRequest({HEADER: "mcp"})) is True


def test_legacy_header_still_marks_mcp_call() -> None:
    """★ 兼容：升级前打出的旧头仍要被认成 MCP 来路。

    不认的后果不是报错，而是**静默放行** —— 待办的「AI 创建必须带标签」
    护栏悄悄失效，谁也发现不了。
    """
    assert LEGACY_HEADERS == ("X-LifeOS-Client",)
    assert is_mcp_call(_FakeRequest({"X-LifeOS-Client": "mcp"})) is True


@pytest.mark.parametrize("value", ["web", "", "mcp-ish", "other"])
def test_non_mcp_header_values_are_not_mcp_calls(value: str) -> None:
    """负向对照：别家来路 / 空值 / 相似值一律不算 MCP（新头旧头都一样）。"""
    assert is_mcp_call(_FakeRequest({HEADER: value})) is False
    assert is_mcp_call(_FakeRequest({"X-LifeOS-Client": value})) is False


def test_missing_headers_is_not_mcp_call() -> None:
    assert is_mcp_call(_FakeRequest({})) is False
    assert is_mcp_call(object()) is False


# ═══════════════ ② 数据库文件名（读错库 = 数据「消失」）═══════════════


def test_db_path_falls_back_to_legacy_file(tmp_path: Path) -> None:
    """★ 兼容：新名库还不存在、旧名库还在 → 用旧库，别空建一个新库。"""
    legacy = tmp_path / LEGACY_DB_NAME
    legacy.write_bytes(b"")
    assert _resolve_db_path(str(tmp_path / "alongside.db")) == legacy


def test_db_path_prefers_new_file_when_it_exists(tmp_path: Path) -> None:
    """正向对照：新名库已在（迁移过）→ 用新库，别再回头找旧的。"""
    (tmp_path / "alongside.db").write_bytes(b"")
    (tmp_path / LEGACY_DB_NAME).write_bytes(b"")
    assert _resolve_db_path(str(tmp_path / "alongside.db")).name == "alongside.db"


def test_db_path_creates_new_name_when_neither_exists(tmp_path: Path) -> None:
    """全新机器：两个都没有 → 建新名。"""
    resolved = _resolve_db_path(str(tmp_path / "alongside.db"))
    assert resolved.name == "alongside.db"
    assert resolved.parent == tmp_path  # 目录自动建好


def test_explicit_legacy_db_path_is_left_alone(tmp_path: Path) -> None:
    """显式把 DB_PATH 指到旧名的部署，不能被「兼容」顺手改写。"""
    (tmp_path / "alongside.db").write_bytes(b"")
    assert _resolve_db_path(str(tmp_path / LEGACY_DB_NAME)).name == LEGACY_DB_NAME


# ═══════════════ ③ 备份文件名（清理与「取最新」的判据）═══════════════


@pytest.mark.parametrize(
    "name", ["alongside-20261020-1200.db", "lifos-20261020-1200.db"]
)
def test_backup_name_regex_accepts_both_brands(name: str) -> None:
    """★ 兼容：旧品牌期留下的 lifos-*.db 备份必须仍然被认。

    不认的话，它们永远不会被保留期清理（越积越多），`--verify latest`
    也会跳过它们去找一个不存在的「最新」。
    """
    assert _BACKUP_NAME_RE.match(name) is not None


@pytest.mark.parametrize(
    "name",
    ["alongside.db", "lifos.db", "backup-alongside-20261020-1200.db", "alongside-2026.db"],
)
def test_backup_name_regex_rejects_others(name: str) -> None:
    assert _BACKUP_NAME_RE.match(name) is None


# ═══════════════ ④ refresh cookie 名（读错 = 当场踢下线）═══════════════

_PASSWORD = "rename-compat-test"


class _FakeSettings:
    """真 argon2 哈希 + 真 JWT 密钥，只是把凭据换成测试值（同 test_auth_cookie_secure）。"""

    def __init__(self) -> None:
        self.admin_password_hash = PasswordHasher().hash(_PASSWORD)
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
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    app = FastAPI()
    # 单独挂 router 的 app 不带全局异常处理器 —— 手动接上，否则 AppError（如 401）
    # 会被 TestClient 当成未捕获异常抛出来，验不到真实 HTTP 状态。
    app.add_exception_handler(AppError, handle_app_error)
    app.include_router(router)
    return TestClient(app)


def _refresh_value(resp: Response, cookie_name: str) -> str | None:
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{cookie_name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


def test_legacy_refresh_cookie_still_exchanges_for_access_token(
    client: TestClient,
) -> None:
    """★ 兼容：升级瞬间浏览器手里是旧 cookie 名 —— 带着它来换 access token 必须成功。

    不认的后果：所有人 access 过期（默认 30 分钟）后被踢回登录页，
    用户看到的是「怎么又要我登录」，而不是任何可排查的错误。
    """
    login = client.post("/login", json={"password": _PASSWORD, "totp": ""})
    assert login.status_code == 200, login.text
    value = _refresh_value(login, REFRESH_COOKIE)
    assert value, "登录必须下发新名 refresh cookie"

    replay = client.post("/refresh", headers={"Cookie": f"{LEGACY_REFRESH_COOKIE}={value}"})
    assert replay.status_code == 200, replay.text
    assert replay.json().get("access_token")
    # 换出来的新 cookie 一律用新名（旧名只读不写，过渡期结束自然消失）
    assert _refresh_value(replay, REFRESH_COOKIE)


def test_new_refresh_cookie_still_works(client: TestClient) -> None:
    """正向对照：新名路线不受兼容分支影响。"""
    login = client.post("/login", json={"password": _PASSWORD, "totp": ""})
    value = _refresh_value(login, REFRESH_COOKIE)
    assert value
    replay = client.post("/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={value}"})
    assert replay.status_code == 200, replay.text


def test_absent_refresh_cookie_is_still_rejected(client: TestClient) -> None:
    """负向对照：两个名字都没有时，仍然是 401（兼容不等于放行）。"""
    assert client.post("/refresh").status_code == 401


# ═══════════════ ⑤ pi 沙箱镜像名（换名后「镜像找不到」= 沙箱起不来）═══════════════

_PI_API_DIR = Path(__file__).resolve().parents[3] / "plugins" / "pi-agent" / "api"


@pytest.fixture(scope="module")
def pi_rpc():
    """按文件路径加载 pi-agent 的 rpc.py（插件不走包导入，用独立模块名避免撞名）。"""
    import importlib.util
    import sys

    path = _PI_API_DIR / "rpc.py"
    spec = importlib.util.spec_from_file_location("pi_agent_rpc_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclass 解析注解时要回头找 sys.modules[cls.__module__]，先登记再执行。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sandbox_image_falls_back_to_legacy_when_new_absent(pi_rpc, monkeypatch) -> None:
    """★ 兼容：只 build 过旧镜像的机器，不能因为默认标签换了就跑不起来。"""
    legacy = pi_rpc.LEGACY_SANDBOX_IMAGE
    monkeypatch.setattr(pi_rpc, "_docker_image_present", lambda image: image == legacy)
    assert pi_rpc.resolve_sandbox_image(pi_rpc.DEFAULT_SANDBOX_IMAGE) == legacy


def test_sandbox_image_keeps_new_name_when_built(pi_rpc, monkeypatch) -> None:
    """正向对照：新镜像已经 build 好 → 用新名，不回退。"""
    monkeypatch.setattr(pi_rpc, "_docker_image_present", lambda image: True)
    assert (
        pi_rpc.resolve_sandbox_image(pi_rpc.DEFAULT_SANDBOX_IMAGE)
        == pi_rpc.DEFAULT_SANDBOX_IMAGE
    )


def test_explicit_custom_sandbox_image_is_untouched(pi_rpc, monkeypatch) -> None:
    """用户显式配了别的镜像 → 不自作聪明改写。"""
    legacy = pi_rpc.LEGACY_SANDBOX_IMAGE
    monkeypatch.setattr(pi_rpc, "_docker_image_present", lambda image: image == legacy)
    assert pi_rpc.resolve_sandbox_image("my-custom-image:1.0") == "my-custom-image:1.0"
