"""内核行为测试（T03）。

覆盖卡片验收要求的六类行为。**每条都是真实行为，不用 mock 冒充**：

1. `/healthz` 与 `/readyz` 正常，且 `/readyz` 不外泄敏感配置
2. 模块发现 → 挂载 → 路由可调（用临时造出的真实模块走全链路）
3. 坏 manifest → **启动即失败**，且报错明确指出是哪个模块的哪个字段
4. 登录失败 → RFC7807 `application/problem+json` + `trace_id` + `X-Trace-Id` 响应头
5. 幂等 → 同一 `Idempotency-Key` 重复请求，除 trace_id 外响应完全一致
6. 敏感信息脱敏 → 日志里的密码 / token 被打码

关于第 2 条的实现方式：`load_router` 是按模块 id 从 `modules` 包里 import 的，
所以临时模块必须真的落在 `services/api/modules/` 下才能被 import。
本文件用一个**带强制清理的 fixture** 造出 `modules/t03probe/`，测试结束后删除，
不留残留（这点是上一个 agent 踩过的坑：它把测试用模块 `demo/` 忘在仓库里了）。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from core.app import create_app
from core.config import Settings
from core.errors import ManifestError
from core.logging import redact_obj, redact_text
from core.manifest import discover_modules

MODULES_DIR = Path(__file__).resolve().parents[1] / "modules"
PROBE_ID = "t03probe"

GOOD_ROUTER = '''
from fastapi import APIRouter

router = APIRouter()


@router.get("/ping")
def ping() -> dict[str, str]:
    return {"pong": "probe"}
'''


def _manifest(**over: object) -> str:
    base = {
        "id": PROBE_ID,
        "name": "探针模块",
        "version": "0.1.0",
        "kind": "builtin",
        "minKernel": "0.1.0",
        "kernelApi": "^1",
        "icon": "probe",
        "description": "测试用临时模块",
        "author": "test",
        "window": {"w": 480, "h": 320},
        "entry": "@apps/probe",
        "api": {
            "base": f"/api/v1/{PROBE_ID}",
            "openapi": f"/api/v1/{PROBE_ID}/openapi.json",
            "health": f"/api/v1/{PROBE_ID}/health",
        },
        "provides": [],
        "requires": [],
        "slots": [],
        "emits": [],
        "consumes": [],
        "permissions": [],
        "migrations": None,
        "settingsSchema": None,
    }
    base.update(over)
    return json.dumps(base, ensure_ascii=False)


@pytest.fixture
def probe_module() -> Iterator[Path]:
    """在真实 modules 目录下造一个临时模块，测试后**必定删除**。"""
    d = MODULES_DIR / PROBE_ID
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / "__init__.py").write_text("", encoding="utf-8")
    (d / "manifest.json").write_text(_manifest(), encoding="utf-8")
    (d / "router.py").write_text(GOOD_ROUTER, encoding="utf-8")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ─────────────────────── 1. 探针 ───────────────────────
def test_healthz_and_readyz() -> None:
    client = TestClient(create_app())

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"ok": True}

    ready = client.get("/readyz")
    assert ready.status_code == 200
    body = ready.json()
    assert body["ok"] is True
    assert "auth" in body["modules"]


def test_readyz_does_not_leak_secrets() -> None:
    """就绪探针不能把密钥吐出去（总纲 §1.6）。"""
    body = json.dumps(TestClient(create_app()).get("/readyz").json(), ensure_ascii=False)

    for forbidden in ("secret_key", "admin_password_hash", "totp_secret", "password"):
        assert forbidden not in body, f"/readyz 疑似泄露了 {forbidden}"


# ─────────────────── 2. 模块发现 → 挂载 ───────────────────
def test_module_is_discovered_and_mounted(probe_module: Path) -> None:
    client = TestClient(create_app())

    # 被列表接口列出
    listed = client.get("/api/v1/modules").json()
    ids = [m["id"] for m in listed["modules"]]
    assert PROBE_ID in ids

    # 真实路由可调
    r = client.get(f"/api/v1/{PROBE_ID}/ping")
    assert r.status_code == 200
    assert r.json() == {"pong": "probe"}

    # 出现在聚合 OpenAPI 里
    paths = client.get("/api/docs").json()["paths"]
    assert f"/api/v1/{PROBE_ID}/ping" in paths


# ─────────── 2.5 ISSUE-011 案 C：/api/v1/modules 公开面折中 ───────────
def test_modules_public_shape_whitelist() -> None:
    """裸调 /api/v1/modules 只回白名单字段；带有效 token 才回全量（案 C）。"""
    from core.security import create_access_token

    client = TestClient(create_app())

    # T1 裸调 → 200 public
    body = client.get("/api/v1/modules").json()
    assert body["visibility"] == "public"
    assert body["count"] == len(body["modules"])

    # T2 每个 module 的 key 集合 ⊆ 白名单
    allowed = {"id", "name", "icon", "version"}
    for m in body["modules"]:
        assert set(m) <= allowed, f"裸调泄露了字段：{sorted(set(m) - allowed)}"

    # T3 裸调 body 不含敏感字段键名（T2 已保证键集合 ⊆ 白名单，此条防未来字段膨胀；
    #   故意不含 "api"——有模块的 icon 值恰为 "api"，键名检查会误伤值）
    raw = json.dumps(body, ensure_ascii=False)
    for forbidden in ('"provides"', '"permissions"', '"requires"', '"emits"'):
        assert forbidden not in raw, f"裸调泄露了 {forbidden}"

    # T4 有效 token → 200 full，含 provides
    tk = create_access_token("admin")
    full = client.get(
        "/api/v1/modules", headers={"Authorization": f"Bearer {tk}"}
    ).json()
    assert full["visibility"] == "full"
    assert full["count"] == len(full["modules"])
    for m in full["modules"]:
        assert "provides" in m, "full 面缺失 provides"

    # T6 两态 count 一致
    assert full["count"] == body["count"]


def test_modules_fake_token_is_401() -> None:
    """伪造/过期 token → 401（与 /api/v1/plugins 口径对齐，假 token 不给信息）。"""
    client = TestClient(create_app())
    r = client.get("/api/v1/modules", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401, f"假 token 应 401，实测 {r.status_code}"


# ─────────────── 3. 坏 manifest → 启动失败并指明字段 ───────────────
def test_broken_manifest_fails_loudly_with_module_and_field(tmp_path: Path) -> None:
    bad = tmp_path / "brokenmod"
    bad.mkdir()
    # 故意漏掉必填字段 id，并把 version 写成非法值
    (bad / "manifest.json").write_text(
        json.dumps({"name": "坏的", "version": 123}, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ManifestError) as ei:
        discover_modules(tmp_path)

    msg = str(ei.value)
    # 必须指出"哪个模块"，并提到缺失/非法的字段名
    assert "brokenmod" in msg
    assert "id" in msg

    # 而且 create_app 不许静默跳过：扫描失败要向上抛
    with pytest.raises(ManifestError):
        create_app(modules_dir=tmp_path)


# ─────────────────── 4. 登录失败 → RFC7807 ───────────────────
def test_login_wrong_password_returns_rfc7807() -> None:
    client = TestClient(create_app())

    r = client.post("/api/v1/auth/login", json={"password": "definitely-wrong", "totp": "000000"})

    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")

    body = r.json()
    for key in ("type", "title", "status", "detail", "trace_id"):
        assert key in body, f"RFC7807 缺字段 {key}"
    assert body["status"] == 401
    assert body["trace_id"]

    # 总纲 §1.4：每个响应都要带 X-Trace-Id，且与 body 里的 trace_id 一致
    assert r.headers.get("x-trace-id") == body["trace_id"]


# ────────── 4.5 SSE 订阅端点（防回归：曾经这里返回 422） ──────────
def test_events_subscribe_needs_no_required_query_params() -> None:
    """卡片要求的命令是 `curl -N /api/v1/events/subscribe`（**不带参数**）。

    这里钉住一个真实踩过的坑：端点上 `request` 参数曾被标注为 `Any`，
    FastAPI 认不出它是请求对象，就把它当成了**必填查询参数** ——
    于是不带 `?request=...` 一律 422，卡片那条命令直接不可用。

    断言方式刻意用 OpenAPI 生成结果，而不是真去读一个永不结束的 SSE 流：
    `TestClient.stream()` 碰上无限流会在退出上下文时死等，把整个 pytest 挂住
    （这个坑也真踩过一次）。真实的事件推送验证放在
    `_tools/t03_sse_e2e.py`（起真服务 + 真读流）。
    """
    client = TestClient(create_app())

    op = client.get("/api/docs").json()["paths"]["/api/v1/events/subscribe"]["get"]
    params = {p["name"]: p for p in op.get("parameters", [])}

    assert "request" not in params, "request 被错当成查询参数了（会要求 ?request=...）"
    assert set(params) <= {"topics", "ticket"}, f"出现了未预期的参数：{set(params)}"
    assert not any(p.get("required") for p in params.values()), "订阅端点不该有必填查询参数"
    # ★ F2 加固（2026-09-23）：「ticket 有意设为可选」—— 鉴权失败走函数体里的
    #   401，而不是 FastAPI 的 422（必填参数缺失）。这条与该测试原意图（防 422）
    #   同源，故一并钉住。
    assert params["ticket"].get("required") is not True, (
        "ticket 必须可选，否则裸连会变成 422 而不是 401"
    )


# ────────── 4.6 SSE 入场券（F2 加固 · 2026-09-23）──────────
def test_events_subscribe_without_ticket_is_401() -> None:
    """无入场券不得订阅：401（不是 422、更不是放行）。

    ★ 为什么是 401 而不是 422：ticket 是可选查询参数，缺失时 FastAPI 不会拦，
      由函数体里 `decode_sse_ticket("")` 抛 UnauthorizedError。
    ★ 本测试不读流：401 在进入 StreamingResponse 之前就抛出，不会挂住 pytest。
    """
    client = TestClient(create_app())
    r = client.get("/api/v1/events/subscribe")
    assert r.status_code == 401, f"无 ticket 应 401，实测 {r.status_code}"


def test_events_subscribe_with_garbage_ticket_is_401() -> None:
    """伪造/过期/类型不符的票据一律 401（票据不可与 access/refresh 互换）。"""
    client = TestClient(create_app())
    r = client.get("/api/v1/events/subscribe?ticket=not-a-real-ticket")
    assert r.status_code == 401, f"伪票应 401，实测 {r.status_code}"


def test_events_ticket_endpoint_requires_auth_then_issues() -> None:
    """换票端点：无 Bearer → 401；有 Bearer → 200 且票据 type=sse、60 秒有效。"""
    from core.security import create_access_token, decode_sse_ticket

    client = TestClient(create_app())

    assert client.post("/api/v1/events/ticket").status_code == 401

    tk = create_access_token("admin")
    r = client.post("/api/v1/events/ticket", headers={"Authorization": f"Bearer {tk}"})
    assert r.status_code == 200
    body = r.json()
    assert body["expires_in"] == 60
    payload = decode_sse_ticket(body["ticket"])  # 必须能按 sse 类型解出
    assert payload["sub"] == "admin"

    # 类型隔离：access token 不能当票据用
    try:
        decode_sse_ticket(tk)
        raise AssertionError("access token 不该能当 SSE 票据")
    except Exception as exc:  # noqa: BLE001
        assert "类型不匹配" in str(exc) or "无效" in str(exc)


# ─────────────────── 5. 幂等 ───────────────────
def test_idempotency_key_makes_repeat_request_identical() -> None:
    client = TestClient(create_app())
    payload = {"password": "wrong", "totp": "000000"}
    key = {"Idempotency-Key": "t03-test-idem-1"}

    first = client.post("/api/v1/auth/login", json=payload, headers=key)
    second = client.post("/api/v1/auth/login", json=payload, headers=key)

    assert first.status_code == second.status_code

    def strip_trace(text: str) -> str:
        data = json.loads(text)
        data.pop("trace_id", None)
        return json.dumps(data, sort_keys=True, ensure_ascii=False)

    assert strip_trace(first.text) == strip_trace(second.text)


# ─────────────────── 6. 敏感信息脱敏 ───────────────────
def test_log_redaction_masks_secrets() -> None:
    raw = "login failed password=hunter2 token=abcdef123456 Authorization: Bearer xyz.abc.def"
    cleaned = redact_text(raw)

    assert "hunter2" not in cleaned
    assert "abcdef123456" not in cleaned

    obj = redact_obj({"password": "hunter2", "token": "abcdef123456", "keep": "ok"})
    assert obj["password"] != "hunter2"
    assert obj["token"] != "abcdef123456"
    assert obj["keep"] == "ok"


# ─────────────────── 7. CORS 合法性（第 0 批清障）───────────────────
def test_cors_wildcard_forces_no_credentials() -> None:
    """CORS 规范：通配符 `*` 不得与凭据并用。默认 `*` 时必须强制关闭凭据。"""
    settings = Settings(
        secret_key="t", admin_password_hash="t", totp_secret="t", cors_allow_origins="*"
    )

    assert settings.cors_allow_credentials is False
    assert settings.cors_origins_list == ["*"]


def test_cors_explicit_origins_allow_credentials() -> None:
    """显式列出来源时才允许凭据型 CORS，且按逗号切分、去空白。"""
    settings = Settings(
        secret_key="t",
        admin_password_hash="t",
        totp_secret="t",
        cors_allow_origins="https://a.example, https://b.example",
    )

    assert settings.cors_allow_credentials is True
    assert settings.cors_origins_list == ["https://a.example", "https://b.example"]


def test_cors_middleware_wiring_disables_credentials_by_default() -> None:
    """内核装配层：默认配置（`*`）下 CORSMiddleware 不允许凭据，防任意站凭据放行。"""
    app = create_app()
    cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]

    assert len(cors) == 1
    assert cors[0].kwargs["allow_credentials"] is False
