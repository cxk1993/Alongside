"""T19 网页工作台后端测试（只跑这一个文件，不影响别的卡）。

★ 状态码约定（跟项目走，不另起炉灶）：校验类错误 = **422**（core.errors.ValidationError），
  slug 冲突 = 409，找不到 = 404，未鉴权 = 401。
数据库隔离：用临时库 ./data/tmp_t19.db，绝不碰主库 alongside.db。
表只在当前进程内由模型直接建（create_app 不自动跑迁移，避免跨插件迁移碰撞）。
"""

from __future__ import annotations

import os

# ★ 必须在 import 任何内核/模块之前设置临时库，init_engine 只认一次。
os.environ["DB_PATH"] = "./data/tmp_t19.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, text  # noqa: E402

from core.app import create_app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from modules.web.models import WebEntry  # noqa: E402
from modules.web.schema import CapabilityEntry  # noqa: E402

BASE = "/api/v1/web"


@pytest.fixture(scope="module")
def client():
    init_engine()  # 用 DB_PATH 指向的临时库
    engine = get_engine()
    WebEntry.__table__.create(bind=engine, checkfirst=True)
    app = create_app()
    c = TestClient(app)
    yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean():
    """每条测试前清空本插件表，测试之间不得相互依赖。"""
    engine = get_engine()
    with Session(engine) as s:
        s.exec(text("DELETE FROM web_entry"))
        s.commit()
    yield


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def _payload(**kw):
    base = {"slug": "demo", "title": "演示", "url": "https://example.invalid/app"}
    base.update(kw)
    return base


def _create(client, auth, **kw):
    r = client.post(f"{BASE}/entries", json=_payload(**kw), headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


# ───────────────────────── 基础 ─────────────────────────
def test_health(client):
    r = client.get(f"{BASE}/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_manifest_id_and_provides(client):
    r = client.get(f"{BASE}/manifest")
    assert r.status_code == 200
    m = r.json()
    assert m["id"] == "web"
    # ★ 恰好这两个 provide（不多不少）
    # ★ 2026-10-04：由「精确相等」改为「子集」（同上 test_diary 的理由）——
    #   模块新增能力不应被判成回归。防回退 = 原有的必须还在。
    assert set(m["provides"]) >= {"web.entry.read", "web.entry.write"}, m["provides"]
    # ★ 本插件**不需要出网权限**
    assert m["permissions"] == ["db:own"]


def test_requires_auth(client):
    assert client.get(f"{BASE}/entries").status_code == 401


# ───────────────────────── CRUD ─────────────────────────
def test_create_list_get(client, auth):
    created = _create(client, auth)
    assert created["slug"] == "demo"
    assert created["enabled"] is True

    lst = client.get(f"{BASE}/entries", headers=auth).json()
    assert lst["total"] == 1
    assert lst["items"][0]["id"] == created["id"]

    one = client.get(f"{BASE}/entries/{created['id']}", headers=auth)
    assert one.status_code == 200
    assert one.json()["url"] == "https://example.invalid/app"


def test_get_missing_404(client, auth):
    assert client.get(f"{BASE}/entries/nope", headers=auth).status_code == 404


def test_update_and_toggle(client, auth):
    e = _create(client, auth)
    r = client.patch(f"{BASE}/entries/{e['id']}", json={"enabled": False}, headers=auth)
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    # ★ 开关生效：enabled=true 的列表里不再出现
    assert client.get(f"{BASE}/entries?enabled=true", headers=auth).json()["total"] == 0
    assert client.get(f"{BASE}/entries", headers=auth).json()["total"] == 1


def test_touch(client, auth):
    e = _create(client, auth)
    r = client.post(f"{BASE}/entries/{e['id']}/touch", headers=auth)
    assert r.status_code == 200
    assert r.json()["id"] == e["id"]


def test_delete(client, auth):
    e = _create(client, auth)
    assert client.delete(f"{BASE}/entries/{e['id']}", headers=auth).status_code == 204
    assert client.get(f"{BASE}/entries/{e['id']}", headers=auth).status_code == 404


# ───────────────────── ★ frame-url：凭据注入链路（2026-10-10） ─────────────────────
def test_frame_url_without_auth_ref_returns_url_as_is(client, auth):
    """没配 auth_ref 的条目：原样回 url，parsed=False（老条目零变化）。"""
    e = _create(client, auth)
    r = client.get(f"{BASE}/entries/{e['id']}/frame-url", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parsed"] is False
    assert body["url"] == e["url"]
    assert "token=" not in body["url"]


def test_frame_url_injects_token_from_env(client, auth, monkeypatch):
    """★ token:env:VAR 形态：从环境变量取值，拼成 ?token=<值>（pi-web-ui 靠这个）。

    守着"凭据只存引用、真值运行时注入"这条纪律不退化。
    """
    monkeypatch.setenv("PI_WEB_TOKEN", "abc123deadbeef4567890123456789ab")
    e = client.post(
        f"{BASE}/entries",
        json=_payload(
            slug="pi-web-probe",
            url="https://demo.invalid/pi/",
            auth_ref="token:env:PI_WEB_TOKEN",
        ),
        headers=auth,
    ).json()
    r = client.get(f"{BASE}/entries/{e['id']}/frame-url", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parsed"] is True
    assert body["url"] == "https://demo.invalid/pi/?token=abc123deadbeef4567890123456789ab"
    # ★ 表里存的仍是引用，不是真值
    assert e["auth_ref"] == "token:env:PI_WEB_TOKEN"
    assert "abc123" not in str(e)


def test_frame_url_missing_env_var_is_error_not_silent(client, auth, monkeypatch):
    """`.env` 里缺变量 → 明确报错（422），绝不静默返回半个 URL。"""
    monkeypatch.delenv("PI_WEB_TOKEN", raising=False)
    e = client.post(
        f"{BASE}/entries",
        json=_payload(
            slug="pi-web-noenv",
            url="https://demo.invalid/pi/",
            auth_ref="token:env:PI_WEB_TOKEN",
        ),
        headers=auth,
    ).json()
    r = client.get(f"{BASE}/entries/{e['id']}/frame-url", headers=auth)
    assert r.status_code == 422, r.text
    assert "PI_WEB_TOKEN" in r.text


def test_frame_url_reads_dotenv_not_only_os_environ(client, auth, monkeypatch, tmp_path):
    """★ 守护：凭据必须能**从 .env 文件**读到，而不只是 os.environ。

    背景（本项目经典坑）：.env 只由 pydantic-settings 加载，**不注入 os.environ**。
    web 模块最初直接 `os.environ.get()` → 明明 .env 里配了也报「未设置」。
    finance 模块早先栽过同一个坑。这条测试钉死「必须走 read_setting」。
    """
    monkeypatch.delenv("PI_WEB_TOKEN", raising=False)  # 确保进程环境里没有
    import core.config as cfg

    # 假 .env：把 read_setting 的 dotenv 回退源换成只含这一项的字典
    monkeypatch.setattr(cfg, "_dotenv_values", lambda: {"PI_WEB_TOKEN": "fromdotenv9876543210"}, raising=True)
    e = client.post(
        f"{BASE}/entries",
        json=_payload(slug="pi-web-dotenv", url="https://d.invalid/pi/", auth_ref="token:env:PI_WEB_TOKEN"),
        headers=auth,
    ).json()
    r = client.get(f"{BASE}/entries/{e['id']}/frame-url", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://d.invalid/pi/?token=fromdotenv9876543210"


# ───────────────────────── ★ 三处校验（本卡重点） ─────────────────────────
@pytest.mark.parametrize(
    "bad_url",
    ["javascript:alert(1)", "data:text/html,<h1>x", "ftp://example.invalid", "https://"],
)
def test_reject_non_http_url(client, auth, bad_url):
    r = client.post(f"{BASE}/entries", json=_payload(url=bad_url), headers=auth)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "bad_ref",
    [
        "pat:AbCdEf0123456789AbCdEf0123456789",  # ★ 直接把明文 token 贴进来
        "AbCdEf0123456789AbCdEf0123456789",  # ★ 裸 token
        "pat:BEECOUNT_MCP_TOKEN",  # 忘了 env: 前缀
        "pat:env:lowercasebad",  # 变量名不是大写
    ],
)
def test_reject_plaintext_credentials(client, auth, bad_ref):
    r = client.post(f"{BASE}/entries", json=_payload(auth_ref=bad_ref), headers=auth)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "good_ref",
    [
        "none",
        "pat:env:EXAMPLE_TOKEN",
        "bearer:env:OTHER_KEY",
        # ★ 2026-10-10：token: 形态（给认 query 参数的自建服务，如 pi-web-ui 的 ?token=）
        "token:env:PI_WEB_TOKEN",
    ],
)
def test_accept_reference_style_auth(client, auth, good_ref):
    slug = "ok-" + good_ref.lower().replace(":", "-").replace("_", "-")
    r = client.post(
        f"{BASE}/entries",
        json=_payload(
            slug=slug, kind="web+rest", endpoint="https://api.invalid/v1", auth_ref=good_ref
        ),
        headers=auth,
    )
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("bad_kind", ["web+ftp", "rest", ""])
def test_reject_bad_kind(client, auth, bad_kind):
    r = client.post(f"{BASE}/entries", json=_payload(kind=bad_kind), headers=auth)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("kind", ["web+rest", "web+mcp"])
def test_kind_needs_endpoint(client, auth, kind):
    r = client.post(f"{BASE}/entries", json=_payload(kind=kind), headers=auth)
    assert r.status_code == 422, r.text
    r2 = client.post(
        f"{BASE}/entries",
        json=_payload(kind=kind, endpoint="https://api.invalid/v1"),
        headers=auth,
    )
    assert r2.status_code == 201, r2.text


@pytest.mark.parametrize("bad_slug", ["Bad Slug", "x", "-lead", "大写"])
def test_reject_bad_slug(client, auth, bad_slug):
    r = client.post(f"{BASE}/entries", json=_payload(slug=bad_slug), headers=auth)
    assert r.status_code == 422, r.text


def test_slug_conflict_409(client, auth):
    _create(client, auth)
    r = client.post(f"{BASE}/entries", json=_payload(title="另一个"), headers=auth)
    assert r.status_code == 409, r.text


def test_kind_validation_on_update(client, auth):
    """PATCH 改 kind 却忘了给 endpoint → 也要被拦住。"""
    e = _create(client, auth)
    r = client.patch(f"{BASE}/entries/{e['id']}", json={"kind": "web+mcp"}, headers=auth)
    assert r.status_code == 422, r.text


# ───────────────────────── ★ 与 T20 的接口形状 ─────────────────────────
def test_capability_shape_matches_t20(client, auth):
    e = _create(
        client,
        auth,
        slug="portal-x",
        title="门户 X",
        kind="web+mcp",
        endpoint="http://127.0.0.1:9/mcp",
        auth_ref="pat:env:EXAMPLE_TOKEN",
        capabilities=["thing.read", "thing.write"],
    )
    cap = e["capability"]
    # ★ 字段名必须与 T20 的 catalog entry 完全一致
    assert sorted(cap.keys()) == sorted(CapabilityEntry.model_fields.keys())
    assert cap["source"] == "web_entry"
    assert cap["id"] == "portal-x"
    assert cap["name"] == "门户 X"
    assert cap["enabled"] is True
    assert cap["capabilities"] == ["thing.read", "thing.write"]
    # ★ 只出现引用，不出现明文凭据
    assert cap["auth_ref"] == "pat:env:EXAMPLE_TOKEN"


def test_ordering_by_order_field(client, auth):
    _create(client, auth, slug="c-third", title="丙", order=30)
    _create(client, auth, slug="a-first", title="甲", order=10)
    _create(client, auth, slug="b-second", title="乙", order=20)
    items = client.get(f"{BASE}/entries", headers=auth).json()["items"]
    assert [i["slug"] for i in items] == ["a-first", "b-second", "c-third"]


def test_capabilities_roundtrip(client, auth):
    e = _create(client, auth, capabilities=["a", "b", "c"])
    assert e["capabilities"] == ["a", "b", "c"]
    got = client.get(f"{BASE}/entries/{e['id']}", headers=auth).json()
    assert got["capabilities"] == ["a", "b", "c"]
