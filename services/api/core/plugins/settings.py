"""插件设置读写与校验（总纲 §1.3 / 验收 #8）。

插件若声明了 settingsSchema（一个 JSON Schema 文件，路径相对插件根），
则其设置必须经该 Schema 校验后才能落库（plugin_setting 表）。
写错类型 / 缺字段 / 越界 → 明确拒绝，不许静默存进去。
"""
from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from core.errors import ValidationError
from core.plugins.validate import _validate_node
from db.models.system import PluginSetting

_SCHEMA_CACHE: dict[str, dict[str, Any]] = {}


def _load_schema(info: Any) -> dict[str, Any] | None:
    """取该插件的设置 Schema。**声明了却找不到 ⇒ 报错，不静默放行。**

    ★ 为什么不能「找不到就当没有 schema」
    ----------------------------------
    「没有 schema」与「schema 找不到」是**两件事**：
      · 没有 schema ⇒ 这个插件**没有设置面**，不校验，合理；
      · schema 找不到 ⇒ manifest **声明了**要按它校验，而文件不在。
    后者是**配置错误**，而静默放行的后果是：
    「声明了要校验」变成了「不校验」——**看起来一切正常，
    而所有设置都绕过了校验直接落库**。
    ⇒ 实测踩过：18 个内置模块的 manifest 都写 `api/settings.schema.json`，
      而文件在模块根（内置模块没有 api/ 子目录）⇒ 校验被静默跳过。
      迁移字段有「双目录探测」兜住所以没出事，设置字段没有兜底——
      **一个字段有兜底、另一个没有，就只坏一个**。

    ⇒ 所以这里 **fail closed**：找不到就报错，
      报错信息说清「哪个插件 · 声明的路径 · 期望在哪」。
    """
    rel = info.manifest.get("settingsSchema")
    if not rel:
        # ★ 声明为 null ⇒ 本插件没有设置面，不校验（这是**正常**状态）。
        return None
    path = info.directory / rel
    if not path.is_file():
        # ★ fail closed：不静默放行。放行 = 所有设置绕过校验直接落库，
        #   而「看起来一切正常」——那正是当初 18 个内置模块的病。
        raise ValidationError(
            f"插件「{info.id}」的设置 Schema 找不到：声明 settingsSchema={rel!r}，"
            f"但 {path} 不存在。声明了要按它校验、文件却不在 ⇒ "
            "只能放行的话，所有设置都会绕过校验直接落库。"
            "请把 Schema 放到该路径，或把 settingsSchema 改成 null（无设置面）。"
        )
    if path.as_posix() in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[path.as_posix()]
    schema = json.loads(path.read_text(encoding="utf-8"))
    _SCHEMA_CACHE[path.as_posix()] = schema
    return schema


def validate_settings(info: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """按插件的 settingsSchema 校验；无 schema 则原样通过。"""
    if not isinstance(settings, dict):
        raise ValidationError("设置必须是对象")
    schema = _load_schema(info)
    if schema is None:
        return settings
    errors: list[str] = []
    _validate_node(settings, schema, "settings", errors)
    if errors:
        raise ValidationError(
            f"插件「{info.id}」设置校验失败：\n  - " + "\n  - ".join(errors)
        )
    return settings


def read_settings(session: Session, plugin_id: str) -> dict[str, Any]:
    """读取插件全部设置 → {key: value_json 解析后的值}。"""
    rows = session.exec(
        select(PluginSetting).where(PluginSetting.plugin_id == plugin_id)
    ).all()
    out: dict[str, Any] = {}
    for r in rows:
        try:
            out[r.key] = json.loads(r.value_json)
        except json.JSONDecodeError:
            out[r.key] = r.value_json
    return out


def write_settings(
    session: Session,
    info: Any,
    plugin_id: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """校验后写入（逐 key upsert）。返回写入后的完整设置。"""
    validate_settings(info, settings)
    for key, value in settings.items():
        value_json = json.dumps(value, ensure_ascii=False)
        existing = session.exec(
            select(PluginSetting).where(
                PluginSetting.plugin_id == plugin_id, PluginSetting.key == key
            )
        ).first()
        if existing is None:
            session.add(
                PluginSetting(plugin_id=plugin_id, key=key, value_json=value_json)
            )
        else:
            existing.value_json = value_json
    session.commit()
    return read_settings(session, plugin_id)
