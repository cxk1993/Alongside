"""Web Push 发送核心（push 插件领地；ADR-0003：AI/前端经 REST，不 import 其他插件）。

设计：
- VAPID 密钥从环境变量 / 项目根 .env 读（read_setting 先例，Settings 未声明字段吞值的坑）。
  PUSH_VAPID_PUBLIC_KEY / PUSH_VAPID_PRIVATE_KEY / PUSH_VAPID_CLAIMS_EMAIL
- pywebpush 惰性 import：没装依赖时插件可加载、health 报 pywebpush_installed=False，
  发送明确报错——不许静默假成功（铁律 #8）。
- 订阅 endpoint 唯一：重复订阅刷新 keys + 复活 active（浏览器换 key 场景）。
- 404/410 响应 = 订阅失效：标 inactive（prune），不再重试轰炸。
- 每次批量发送落 push_log（审计：谁被推了什么）。
"""
from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlmodel import Session, col, select

from core.config import read_setting
from core.errors import ValidationError

from .models import PushLog, PushSubscription

log = logging.getLogger("push")

# 失效订阅的 HTTP 状态（RFC 8030/8292 惯例：404/410 = gone）
_GONE_STATUS = {404, 410}

# ── F3 加固：从源头收窄「可被登记的对象」 ────────────────────────────────
# 依据：CodeArts 知默《无鉴权端点全扫描》F3。`/push/subscribe` 不鉴权是 **Web Push
# 惯例**（Service Worker 在后台续订时拿不到前端 token，endpoint 本身即设备能力
# 凭证）—— 故本轮**不动鉴权语义**，改为两条源头收窄：
#   ① 主机白名单：只接受主流推送服务商的 endpoint（可配置，`*` = 不限制）
#   ② 活跃订阅上限：防灌爆订阅表
_DEFAULT_ALLOWED_HOST_SUFFIXES: tuple[str, ...] = (
    "fcm.googleapis.com",  # Chrome / Edge（Chromium 系）
    "updates.push.services.mozilla.com",  # Firefox autopush
    "push.services.mozilla.com",  # Firefox（旧端点）
    ".notify.windows.com",  # Edge 原生 WNS
    ".push.apple.com",  # Safari APNs
)

# 活跃订阅上限（单用户系统：20 台设备足够；防"灌爆订阅表"）
MAX_ACTIVE_SUBSCRIPTIONS = 20


def allowed_host_suffixes() -> tuple[str, ...] | None:
    """允许的 endpoint 主机后缀；返回 None 表示不限制（配置为 `*`）。"""
    raw = (read_setting("PUSH_ENDPOINT_ALLOWED_HOSTS", "") or "").strip()
    if raw == "*":
        return None
    if raw:
        return tuple(x.strip().lower() for x in raw.split(",") if x.strip())
    return _DEFAULT_ALLOWED_HOST_SUFFIXES


def endpoint_host_allowed(endpoint: str) -> bool:
    """endpoint 主机是否在白名单内（`example.com` 与 `.example.com` 两种写法都支持）。"""
    allowed = allowed_host_suffixes()
    if allowed is None:
        return True
    host = (urlparse(endpoint).hostname or "").lower()
    if not host:
        return False
    for suf in allowed:
        if host == suf.lstrip(".") or host.endswith(suf if suf.startswith(".") else f".{suf}"):
            return True
    return False


def vapid_keys() -> tuple[str, str, str]:
    pub = (read_setting("PUSH_VAPID_PUBLIC_KEY", "") or "").strip()
    priv = (read_setting("PUSH_VAPID_PRIVATE_KEY", "") or "").strip()
    claims = (read_setting("PUSH_VAPID_CLAIMS_EMAIL", "mailto:admin@localhost") or "").strip()
    return pub, priv, claims


def _public_key_shape_ok(pub_b64: str) -> bool:
    """公钥必须是 base64url 解码后 65 字节、首字节 0x04 的未压缩点。

    ★ 这正是前端 `applicationServerKey` 要求的形状，也是 VAPID 生成器
      （`tools/gen_vapid_keys.py`）三重自验之一 —— 同一个判据两处复用。
    """
    try:
        raw = base64.urlsafe_b64decode(pub_b64 + "=" * (-len(pub_b64) % 4))
    except Exception:  # noqa: BLE001 — 任何解码失败都算"形状不对"
        return False
    return len(raw) == 65 and raw[0] == 0x04


def _private_key_parseable(priv_b64: str) -> bool:
    """私钥必须能被 py_vapid 反解（from_string 规则：去换行 → b64url → 32B from_raw / DER）。"""
    try:
        from py_vapid import Vapid  # type: ignore[import-untyped]  # noqa: PLC0415

        Vapid.from_string(priv_b64)
        return True
    except Exception:  # noqa: BLE001 — 解不开就算不可用
        return False


def vapid_ready() -> bool:
    """VAPID 是否就绪。

    ★ 静默失败防御（讨论第五轮 · 提案 G1）：**不只查"非空"**。
      旧实现 `bool(pub and priv)` 下，私钥粘错位置 / 少一位 / 带空格副本 / 公私填反，
      都会让 `/push/health` 报 `vapid_ready: true`，**直到真正发送才炸**（"假成功"）。
      现补两道格式校验：
        - 公钥：base64url → 65 字节未压缩点（0x04 开头）
        - 私钥：可被 py_vapid 反解
      ★ 惰性容错：未装 pywebpush 时**跳过私钥解析**（health 另有 `pywebpush_installed`
        单独上报），不改变"没装依赖时插件仍可加载"的既有设计。
    """
    pub, priv, _ = vapid_keys()
    if not (pub and priv):
        return False
    if not _public_key_shape_ok(pub):
        return False
    return _private_key_parseable(priv) if pywebpush_available() else True


def pywebpush_available() -> bool:
    try:
        import pywebpush  # type: ignore[import-untyped]  # noqa: F401
        return True
    except ImportError:
        return False


def subscribe(
    db: Session, data: dict[str, Any], *, user_agent: str | None = None
) -> PushSubscription:
    """登记/刷新订阅。endpoint 已存在 → 更新 keys、复活。"""
    endpoint = str(data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        raise ValidationError("订阅缺 endpoint/keys.p256dh/keys.auth")
    # F3①：主机白名单（挡垃圾 endpoint 登记）
    if not endpoint_host_allowed(endpoint):
        raise ValidationError(
            "订阅端点主机不在允许列表内（仅接受主流推送服务）；"
            "自建推送服务请配置 PUSH_ENDPOINT_ALLOWED_HOSTS"
        )
    stmt = select(PushSubscription).where(col(PushSubscription.endpoint) == endpoint)
    row = db.exec(stmt).first()
    ua_raw = user_agent or data.get("user_agent")
    ua = (str(ua_raw)[:200] if ua_raw else None)
    if row is None:
        # F3②：新增订阅前检查上限（已存在订阅的刷新不受限）
        if len(list_active(db)) >= MAX_ACTIVE_SUBSCRIPTIONS:
            raise ValidationError(
                f"活跃订阅已达上限 {MAX_ACTIVE_SUBSCRIPTIONS} 条，请先注销不用的设备"
            )
        row = PushSubscription(
            endpoint=endpoint, p256dh=p256dh, auth=auth, user_agent=ua,
        )
        db.add(row)
    else:
        row.p256dh, row.auth = p256dh, auth
        if ua:
            row.user_agent = ua
        row.active = True
        row.fail_count = 0
    db.commit()
    db.refresh(row)
    return row


def unsubscribe(db: Session, endpoint: str) -> bool:
    stmt = select(PushSubscription).where(col(PushSubscription.endpoint) == endpoint.strip())
    row = db.exec(stmt).first()
    if row is None:
        return False
    row.active = False
    db.add(row)
    db.commit()
    return True


def list_active(db: Session) -> list[PushSubscription]:
    return list(db.exec(select(PushSubscription).where(col(PushSubscription.active) == True)).all())  # noqa: E712


def _send_one(
    sub: PushSubscription, title: str, body: str, url: str, tag: str
) -> tuple[bool, int | None, str]:
    from pywebpush import WebPushException, webpush  # 惰性：依赖缺失时调用方兜错

    pub, priv, claims = vapid_keys()
    payload = {"title": title, "body": body, "url": url, "tag": tag}
    try:
        resp = webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            },
            data=json_dumps(payload),
            vapid_private_key=priv,
            vapid_claims={"sub": claims},
            ttl=3600,
        )
        status = getattr(resp, "status_code", 201)
        return (200 <= status < 300), status, ""
    except WebPushException as exc:
        status = None
        resp = getattr(exc, "response", None)
        if resp is not None:
            status = getattr(resp, "status_code", None)
        return False, status, str(exc)[:200]
    except Exception as exc:  # noqa: BLE001 — 单订阅失败不拖垮批次
        return False, None, str(exc)[:200]


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def send_broadcast(
    db: Session,
    *,
    title: str,
    body: str = "",
    url: str = "/",
    tag: str = "alongside",
    topic: str = "manual",
) -> dict[str, Any]:
    """向全部活跃订阅广播。返回 {ok, sent, pruned, detail}。写 push_log。"""
    subs = list_active(db)
    if not vapid_ready() or not pywebpush_available():
        missing = []
        if not vapid_ready():
            missing.append("VAPID 密钥（PUSH_VAPID_PUBLIC_KEY/PRIVATE_KEY）")
        if not pywebpush_available():
            missing.append("pywebpush 库")
        detail = "推送未配置：" + "、".join(missing)
        db.add(
            PushLog(
                topic=topic,
                title=title[:200],
                body=body[:2000] or None,
                ok=False,
                detail=detail,
                sent_at=datetime.now(UTC),
            )
        )
        db.commit()
        return {"ok": False, "sent": 0, "pruned": 0, "detail": detail}

    sent = 0
    pruned = 0
    errs: list[str] = []
    now = datetime.now(UTC)
    for sub in subs:
        ok, status, err = _send_one(sub, title, body, url, tag)
        sub.last_sent_at = now
        sub.last_status = status
        if ok:
            sent += 1
            sub.fail_count = 0
        else:
            sub.fail_count += 1
            if status in _GONE_STATUS:
                sub.active = False
                pruned += 1
            errs.append(err or f"status={status}")
        db.add(sub)
    db.add(PushLog(
        topic=topic, title=title[:200], body=body[:2000] or None,
        ok=sent > 0 or not subs, detail=("; ".join(errs)[:500] or None), sent_at=now,
    ))
    db.commit()
    if sent or not subs:
        log.info("push broadcast", extra={"topic": topic, "sent": sent, "pruned": pruned})
    return {"ok": sent > 0 or not subs, "sent": sent, "pruned": pruned,
            "detail": ("; ".join(errs)[:500] or None)}
