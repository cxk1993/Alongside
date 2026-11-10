"""web 业务逻辑：条目 CRUD + 开关 + 打开计数 + ★ 能力条目形状的产出。

★ 本卡**不实现**"能力目录页面"（那是 T20）。本卡只负责：
  1) 存住条目（含可选的 API/MCP 声明）
  2) 把它翻译成 catalog 认得的形状（CapabilityEntry）
★ 事件在 service 层发布（web.entry.created / updated / deleted），
  内核 SSE 自动下推，路由层不重复做。
"""

from __future__ import annotations

import os
from typing import Any

from sqlmodel import Session, col, select

from core.errors import ConflictError, NotFoundError, ValidationError
from core.events import event_bus
from db.base import utcnow

from .models import WebEntry, caps_from_json, caps_to_json
from .schema import (
    CapabilityEntry,
    FrameUrlOut,
    WebEntryCreate,
    WebEntryUpdate,
    normalize_auth_ref,
    normalize_kind,
    normalize_slug,
    normalize_url,
)

SOURCE = "web_entry"

#: 事件的 `source` = **发布者模块 id**（emit 契约按它查 manifest.emits）。
#: ★ 与上面的 `SOURCE` 不是一回事：那是 catalog 条目的「来源」展示标签
#:   （`web_entry` 是给人看的值），事件源必须是模块 id `web`。
#:   早先两者共用一个常量，于是 web 模块发布的事件 source 是 `web_entry` ——
#:   与 manifest.id 不一致，按模块查声明会落空。这里把两件事分开写。
EVENT_SOURCE = "web"


class WebEntryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ───────────────────────── 查询 ─────────────────────────

    def list_entries(self, enabled: bool | None = None) -> tuple[list[dict[str, Any]], int]:
        stmt = select(WebEntry)
        if enabled is not None:
            stmt = stmt.where(col(WebEntry.enabled) == enabled)
        stmt = stmt.order_by(col(WebEntry.order), col(WebEntry.created_at))
        rows = list(self.db.exec(stmt).all())
        return [self.dump(r) for r in rows], len(rows)

    def get(self, entry_id: str) -> WebEntry:
        row = self.db.get(WebEntry, entry_id)
        if row is None:
            raise NotFoundError(f"网页条目不存在：{entry_id}")
        return row

    def get_by_slug(self, slug: str) -> WebEntry | None:
        stmt = select(WebEntry).where(col(WebEntry.slug) == slug)
        return self.db.exec(stmt).first()

    # ───────────────────────── 写 ─────────────────────────

    def create(self, body: WebEntryCreate) -> dict[str, Any]:
        slug = normalize_slug(body.slug)
        if self.get_by_slug(slug) is not None:
            raise ConflictError(f"slug 已被占用：{slug}")

        url = normalize_url(body.url)
        kind = normalize_kind(body.kind)
        endpoint = (body.endpoint or "").strip() or None
        auth_ref = normalize_auth_ref(body.auth_ref)
        self._check_kind_endpoint(kind, endpoint)

        row = WebEntry(
            slug=slug,
            title=(body.title or "").strip() or slug,
            url=url,
            icon=body.icon,
            order=body.order,
            enabled=body.enabled,
            kind=kind,
            endpoint=endpoint,
            auth_ref=auth_ref,
            capabilities=caps_to_json(body.capabilities),
            note=body.note,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        event_bus.publish("web.entry.created", self.dump(row), source=EVENT_SOURCE)
        return self.dump(row)

    def update(self, entry_id: str, body: WebEntryUpdate) -> dict[str, Any]:
        row = self.get(entry_id)
        data = body.model_dump(exclude_unset=True)

        if "url" in data and data["url"] is not None:
            row.url = normalize_url(data["url"])
        if "title" in data and data["title"] is not None:
            row.title = data["title"].strip() or row.slug
        if "icon" in data and data["icon"] is not None:
            row.icon = data["icon"] or None
        if "order" in data and data["order"] is not None:
            row.order = int(data["order"])
        if "enabled" in data and data["enabled"] is not None:
            row.enabled = bool(data["enabled"])
        if "kind" in data and data["kind"] is not None:
            row.kind = normalize_kind(data["kind"])
        if "endpoint" in data and data["endpoint"] is not None:
            row.endpoint = (data["endpoint"] or "").strip() or None
        if "auth_ref" in data:
            row.auth_ref = normalize_auth_ref(data["auth_ref"])
        if "capabilities" in data and data["capabilities"] is not None:
            row.capabilities = caps_to_json(data["capabilities"])
        if "note" in data and data["note"] is not None:
            row.note = data["note"] or None

        self._check_kind_endpoint(row.kind, row.endpoint)

        row.updated_at = utcnow()
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        event_bus.publish("web.entry.updated", self.dump(row), source=EVENT_SOURCE)
        return self.dump(row)

    def delete(self, entry_id: str) -> None:
        row = self.get(entry_id)
        snapshot = self.dump(row)
        self.db.delete(row)
        self.db.commit()
        event_bus.publish("web.entry.deleted", snapshot, source=EVENT_SOURCE)

    def touch(self, entry_id: str) -> dict[str, Any]:
        """记一次"打开"。v0.1 只回时间戳（不落库），端点先在，便于以后加使用统计。"""
        row = self.get(entry_id)
        return {"id": row.id, "opened_at": utcnow()}

    def frame_url(self, entry_id: str) -> FrameUrlOut:
        """返回 iframe 内嵌用的真实 URL（含 auth_ref 解析后的凭据）。

        ★ 条目表永不明文凭据：auth_ref 形如 "pat:env:PI_TOKEN"，运行时经
          core.config.read_setting 取（os.environ 优先，缺失回退项目根 .env）。
        ★ 解析失败（env 变量缺失）→ 422 + 错误详情，不吞异常。
        ★ 无 auth_ref 或 "none" → 直接返回原 url。
        """
        row = self.get(entry_id)
        auth_ref = row.auth_ref

        if not auth_ref or auth_ref.lower() == "none":
            return FrameUrlOut(
                id=row.id,
                slug=row.slug,
                title=row.title,
                url=row.url,
                auth_ref=auth_ref,
                parsed=False,
            )

        # 解析 auth_ref: "<type>:env:<VAR_NAME>"
        try:
            auth_type, env_var = auth_ref.split(":env:", 1)
        except ValueError:
            raise ValidationError(f"auth_ref 格式错误：{auth_ref}")

        # ★ 从配置读凭据 —— 必须走 core.config.read_setting，**不能**用 os.environ.get：
        #   本项目的 .env 只由 pydantic-settings 加载，**不会注入进程 os.environ**
        #   （web 模块曾因此拿不到值；finance 模块早先也栽过同一个坑，见
        #   core/config.py::_dotenv_values 的注释）。read_setting 语义 =
        #   真实环境变量优先，缺失时回退解析项目根 .env。
        from core.config import read_setting

        secret = read_setting(env_var, "")
        if not secret:
            raise ValidationError(
                f"凭据解析失败：环境变量 {env_var} 未设置。"
                f"请在 .env 中配置 {env_var}=<凭据值>，然后重启服务。"
            )

        # 拼接到 URL query 参数
        from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

        parsed = urlparse(row.url)
        query_params = parse_qs(parsed.query)
        query_params[auth_type] = [secret]
        new_query = urlencode(query_params, doseq=True)
        new_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                              parsed.params, new_query, parsed.fragment))

        return FrameUrlOut(
            id=row.id,
            slug=row.slug,
            title=row.title,
            url=new_url,
            auth_ref=auth_ref,
            parsed=True,
        )

    # ───────────────────────── 内部 ─────────────────────────

    @staticmethod
    def _check_kind_endpoint(kind: str, endpoint: str | None) -> None:
        """kind 含 rest/mcp 时必须有 endpoint —— 否则"声明了能力却没地方调"。"""
        if kind != "web" and not endpoint:
            raise ValidationError(f"kind={kind} 时必须提供 endpoint（能力端点）")

    @staticmethod
    def _capability(row: WebEntry) -> CapabilityEntry:
        """★ 把一行 web_entry 翻成 catalog 认得的形状（T20 直接消费）。"""
        return CapabilityEntry(
            id=row.slug,
            name=row.title,
            kind=row.kind,
            url=row.url,
            endpoint=row.endpoint,
            auth_ref=row.auth_ref,
            capabilities=caps_from_json(row.capabilities),
            enabled=bool(row.enabled),
            note=row.note,
            source=SOURCE,
        )

    def dump(self, row: WebEntry) -> dict[str, Any]:
        return {
            "id": row.id,
            "slug": row.slug,
            "title": row.title,
            "url": row.url,
            "icon": row.icon,
            "order": row.order,
            "enabled": row.enabled,
            "kind": row.kind,
            "endpoint": row.endpoint,
            "auth_ref": row.auth_ref,
            "capabilities": caps_from_json(row.capabilities),
            "note": row.note,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "capability": self._capability(row),
        }
