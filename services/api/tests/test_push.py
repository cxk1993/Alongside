"""push 插件测试（Web Push：订阅/注销/刷新/广播兜错/410 清理/API 面）。

数据库隔离：临时库 ./data/tmp_push.db，绝不碰主库 alongside.db（test_notes 先例）。
外发 HTTP 全部 mock —— 没有真推送服务，不伪造「已推送」。
"""
from __future__ import annotations

import base64
import os

os.environ["DB_PATH"] = "./data/tmp_push.db"


import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from core.app import create_app  # noqa: E402
from core.errors import ValidationError  # noqa: E402
from core.security import create_access_token  # noqa: E402
from db.engine import get_engine, init_engine  # noqa: E402
from db.models.system import PluginState  # noqa: E402
from modules.push import push_link, sender  # noqa: E402
from modules.push.models import PushLog, PushSubscription  # noqa: E402

# ★ F3 加固后 endpoint 主机需在白名单内 → 测试用真实服务商域名形态
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123def456"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    """★ 建表必须与 client fixture 解耦（实测踩过）：本文件有只请求 db 的测试，
    它们同样会走 autouse 的 _clean；而 client 是 module fixture，只有请求它的
    测试才会触发 —— 建表若挂在 client 上，这些测试就在 _clean 里撞
    "no such table: push_subscription"（6 个 ERROR 的真实根因）。
    plugin_state 是 /api/v1/plugins 的内核表，一并建（本仓库 fixture 建表的既定做法）。"""
    init_engine()
    engine = get_engine()
    PushSubscription.__table__.create(bind=engine, checkfirst=True)
    PushLog.__table__.create(bind=engine, checkfirst=True)
    PluginState.__table__.create(bind=engine, checkfirst=True)
    yield
    engine.dispose()


@pytest.fixture(scope="module")
def client():
    yield TestClient(create_app())


@pytest.fixture(autouse=True)
def _clean():
    engine = get_engine()
    with Session(engine) as s:
        for row in s.exec(select(PushSubscription)).all():
            s.delete(row)
        for row in s.exec(select(PushLog)).all():
            s.delete(row)
        s.commit()
    yield


@pytest.fixture()
def db():
    with Session(get_engine()) as s:
        yield s


def _auth() -> dict[str, str]:
    tk = create_access_token('admin')
    return {"Authorization": "Bearer " + tk}


# ────────────────────────── service 层 ──────────────────────────

def test_subscribe_then_list(db: Session) -> None:
    row = sender.subscribe(
        db, {"endpoint": ENDPOINT, "keys": {"p256dh": "BK" + "x" * 80, "auth": "authtoken"}}
    )
    assert row.active is True
    assert sender.list_active(db)[0].endpoint == ENDPOINT


def test_resubscribe_refreshes_keys_and_revives(db: Session) -> None:
    sender.subscribe(db, {"endpoint": ENDPOINT, "keys": {"p256dh": "BK1xx", "auth": "a1"}})
    assert sender.unsubscribe(db, ENDPOINT) is True
    assert sender.list_active(db) == []
    row = sender.subscribe(db, {"endpoint": ENDPOINT, "keys": {"p256dh": "BK2xx", "auth": "a2"}})
    assert row.active is True and row.p256dh == "BK2xx"  # 复活+换key，不重复登记


def test_subscribe_missing_fields_rejected(db: Session) -> None:
    from core.errors import ValidationError
    with pytest.raises(ValidationError):
        sender.subscribe(db, {"endpoint": ENDPOINT, "keys": {"p256dh": "", "auth": ""}})


def test_send_without_vapid_reports_not_configured(db: Session, monkeypatch) -> None:
    monkeypatch.setattr(sender, "vapid_ready", lambda: False)
    out = sender.send_broadcast(db, title="测试")
    assert out["ok"] is False and "未配置" in (out["detail"] or "")
    row = db.exec(select(PushLog)).first()
    assert row is not None and row.ok is False  # 失败也留审计


def test_send_prunes_gone_subscriptions(db: Session, monkeypatch) -> None:
    sender.subscribe(db, {"endpoint": ENDPOINT, "keys": {"p256dh": "BK", "auth": "a"}})
    monkeypatch.setattr(sender, "vapid_ready", lambda: True)
    monkeypatch.setattr(sender, "pywebpush_available", lambda: True)
    monkeypatch.setattr(sender, "_send_one", lambda sub, t, b, u, tag: (False, 410, "Gone"))
    out = sender.send_broadcast(db, title="测试")
    assert out["pruned"] == 1 and out["ok"] is False
    assert sender.list_active(db) == []


def test_send_ok_counts(db: Session, monkeypatch) -> None:
    sender.subscribe(db, {"endpoint": ENDPOINT, "keys": {"p256dh": "BK", "auth": "a"}})
    monkeypatch.setattr(sender, "vapid_ready", lambda: True)
    monkeypatch.setattr(sender, "pywebpush_available", lambda: True)
    monkeypatch.setattr(sender, "_send_one", lambda sub, t, b, u, tag: (True, 201, ""))
    out = sender.send_broadcast(db, title="测试", topic="manual")
    assert out["ok"] is True and out["sent"] == 1
    row = db.exec(select(PushLog)).first()
    assert row is not None and row.ok is True and row.topic == "manual"


# ────────────────────────── API 面（create_app 全栈挂载） ──────────────────────────

def test_api_health(client: TestClient) -> None:
    h = client.get("/api/v1/push/health")
    assert h.status_code == 200
    body = h.json()
    assert body["ok"] is True
    assert set(body) >= {"enabled", "vapid_ready", "pywebpush_installed", "subscriptions_active"}


def test_api_subscribe_flow(client: TestClient) -> None:
    r = client.post("/api/v1/push/subscribe", json={
        "endpoint": ENDPOINT, "keys": {"p256dh": "BK" + "y" * 80, "auth": "authzz"}})
    assert r.status_code == 200 and r.json()["active"] is True

    lst = client.get("/api/v1/push/subscriptions", headers=_auth())
    assert lst.status_code == 200 and len(lst.json()) == 1
    # ★ 响应零泄密：完整 endpoint 不出现在列表里
    assert ENDPOINT not in lst.text

    unsub = client.request("DELETE", "/api/v1/push/subscribe", params={"endpoint": ENDPOINT})
    assert unsub.status_code == 200 and unsub.json()["ok"] is True
    lst2 = client.get("/api/v1/push/subscriptions", headers=_auth())
    assert lst2.json()[0]["active"] is False


def test_api_requires_login_for_admin_endpoints(client: TestClient) -> None:
    assert client.get("/api/v1/push/subscriptions").status_code == 401
    assert client.post("/api/v1/push/send", json={"title": "x"}).status_code == 401


def test_reminder_event_triggers_push(client: TestClient, db: Session, monkeypatch) -> None:
    """事件联动：publish calendar.reminder.fired → push 通道被调用（ISSUE-007 同款验证）。

    ★ monkeypatch 必须打在 push_link 的命名空间上：push_link 用
      `from .sender import send_broadcast` 把函数对象绑进了自己的模块属性，
      改 sender.send_broadcast 打不中它（实测 calls 恒空、事件日志却已打出）。"""
    from core.events import event_bus

    calls: list[dict] = []
    monkeypatch.setattr(
        push_link,
        "send_broadcast",
        lambda db_, **kw: (
            calls.append(kw),
            {"ok": True, "sent": 0, "pruned": 0, "detail": None},
        )[1],
    )
    event_bus.publish("calendar.reminder.fired",
                      {"event_id": "e1", "title": "体检", "start_at": "2026-09-23T09:00:00+08:00"},
                      source="calendar")
    assert calls and "日程提醒" in calls[0]["title"]


def test_manifest_registers_and_mcp_tool_visible(client: TestClient) -> None:
    """push 插件过内核三层校验成功挂载 + provides 机械映射进 MCP（18 号判据）。

    ★ /api/v1/plugins 的响应是 {"plugins": [...]} 包装（与前端 PluginContext 一致），
      不是裸数组 —— 原写法 plugins.json() 直接迭代会 TypeError。
    """
    plugins = client.get("/api/v1/plugins", headers=_auth())
    assert plugins.status_code == 200
    ids = {p["id"] for p in plugins.json()["plugins"]}
    assert "push" in ids

    # provides 机械映射（T18 派生规则）：push.subscription.read → push_subscription_read
    tools = client.get("/api/v1/mcp/tools", headers=_auth())
    assert tools.status_code == 200
    names = {t["name"] for t in tools.json()}
    assert "push_subscription_read" in names, (
        "provides 未映射进 MCP；当前 push_* 工具："
        f"{sorted(n for n in names if n.startswith('push'))}"
    )


# ────────────────────── F3 加固（2026-09-23）：源头收窄 ──────────────────────
# 依据：CodeArts 知默《无鉴权端点全扫描》F3。subscribe 不鉴权是 Web Push 惯例
# （SW 后台续订拿不到 token），故改为「主机白名单 + 活跃订阅上限」两条源头收窄。


def test_endpoint_host_not_in_allowlist_is_rejected(db: Session) -> None:
    """F3①：非白名单主机的 endpoint 一律拒绝（挡垃圾登记）。"""
    with pytest.raises(ValidationError):
        sender.subscribe(
            db,
            {"endpoint": "https://evil.example.com/x", "keys": {"p256dh": "BK", "auth": "a"}},
        )


def test_allowlist_can_be_configured_off(db: Session, monkeypatch) -> None:
    """F3①：白名单可配置 —— `*`（返回 None）表示不限制，供自建推送服务场景。"""
    monkeypatch.setattr(sender, "allowed_host_suffixes", lambda: None)
    row = sender.subscribe(
        db,
        {"endpoint": "https://self-hosted.push.internal/x", "keys": {"p256dh": "BK", "auth": "a"}},
    )
    assert row.active is True


def test_mainstream_hosts_pass_allowlist() -> None:
    """F3①：主流服务商域名形态均通过（含子域与逐字匹配两种写法）。"""
    for host in (
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "wns2-by3p.notify.windows.com",
        "web.push.apple.com",
    ):
        assert sender.endpoint_host_allowed(f"https://{host}/x"), host
    assert not sender.endpoint_host_allowed("https://tracker.example.com/x")
    assert not sender.endpoint_host_allowed("not-a-url")


def test_active_subscription_ceiling(db: Session, monkeypatch) -> None:
    """F3②：活跃订阅达上限后拒绝新增（已存在的刷新不受限）。"""
    monkeypatch.setattr(sender, "MAX_ACTIVE_SUBSCRIPTIONS", 2)
    for i in range(2):
        sender.subscribe(
            db,
            {
                "endpoint": f"https://fcm.googleapis.com/fcm/send/sub{i}",
                "keys": {"p256dh": "BK" + "x" * (i + 1), "auth": "authok"},
            },
        )
    with pytest.raises(ValidationError):
        sender.subscribe(
            db,
            {
                "endpoint": "https://fcm.googleapis.com/fcm/send/overflow",
                "keys": {"p256dh": "BKov", "auth": "authov"},
            },
        )
    # 刷新已有订阅（非新增）不受上限限制
    refreshed = sender.subscribe(
        db,
        {
            "endpoint": "https://fcm.googleapis.com/fcm/send/sub0",
            "keys": {"p256dh": "BKrefresh", "auth": "authrf"},
        },
    )
    assert refreshed.p256dh == "BKrefresh"


# ──────────── 静默失败防御（讨论第五轮 · 提案 G1）────────────
# vapid_ready 从「只查非空」升级为「查格式可解」。以下前三条**故意造坏输入**，
# 遵纪律「加闸门必须证明它对坏输入能变红」——旧实现在这三条上都会误报 ready:true。

_GOOD_PUB = base64.urlsafe_b64encode(b"\x04" + b"\x11" * 64).rstrip(b"=").decode()


def test_vapid_ready_rejects_malformed_public_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 反例①：公钥形状不对（非 65B/0x04）→ 必须 not ready。"""
    monkeypatch.setattr(sender, "vapid_keys", lambda: ("tooshort", "x" * 43, "mailto:a@b"))
    assert sender.vapid_ready() is False


def test_vapid_ready_rejects_undersized_public_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 反例②：公钥长度对但首字节不是 0x04（非未压缩点）→ not ready。"""
    bad = base64.urlsafe_b64encode(b"\x02" + b"\x11" * 64).rstrip(b"=").decode()
    monkeypatch.setattr(sender, "vapid_keys", lambda: (bad, "x" * 43, "mailto:a@b"))
    assert sender.vapid_ready() is False


def test_vapid_ready_rejects_unparseable_private_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 反例③：公钥形状对、但私钥解不开 → 仍须 not ready（旧实现会误报 ready）。

    ★ G1 惰性容错：`vapid_ready()` 仅在 `pywebpush_available()` 时才校验私钥
      （未装 pywebpush 时跳过，health 另有 `pywebpush_installed` 上报）。
      本地 venv 通常不装 pywebpush（生产才有）→ 此测试必须 monkeypatch
      `pywebpush_available` 为 True，才能进入私钥校验分支，否则会误判 ready。
    """
    monkeypatch.setattr(sender, "pywebpush_available", lambda: True)
    monkeypatch.setattr(
        sender, "vapid_keys", lambda: (_GOOD_PUB, "not-a-valid-private-key", "mailto:a@b")
    )
    assert sender.vapid_ready() is False


def test_vapid_ready_skips_private_key_check_when_pywebpush_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 惰性容错分支：未装 pywebpush → 跳过私钥校验，公钥形状对即 ready。

    （G1 设计：不因未装 pywebpush 而让插件加载失败；health 另行上报依赖状态。）
    """
    monkeypatch.setattr(sender, "pywebpush_available", lambda: False)
    monkeypatch.setattr(
        sender, "vapid_keys", lambda: (_GOOD_PUB, "not-a-valid-private-key", "mailto:a@b")
    )
    # 未装 pywebpush → 私钥未校验 → 公钥形状对即 ready（不因坏私钥误杀）
    assert sender.vapid_ready() is True


def test_vapid_ready_accepts_a_real_generated_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """正例：真实生成器产出的密钥对 → ready（确保新校验不误杀好密钥）。"""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid

    v = Vapid()
    v.generate_keys()
    pub = (
        base64.urlsafe_b64encode(
            v.public_key.public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        )
        .rstrip(b"=")
        .decode()
    )
    priv = (
        base64.urlsafe_b64encode(
            v.private_key.private_numbers().private_value.to_bytes(32, "big")
        )
        .rstrip(b"=")
        .decode()
    )
    monkeypatch.setattr(sender, "vapid_keys", lambda: (pub, priv, "mailto:a@b"))
    assert sender.vapid_ready() is True


def test_vapid_ready_still_false_when_keys_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：空值仍 not ready（不因新增校验改坏原有语义）。"""
    monkeypatch.setattr(sender, "vapid_keys", lambda: ("", "", "mailto:a@b"))
    assert sender.vapid_ready() is False
