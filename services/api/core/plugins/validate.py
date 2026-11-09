"""用 contracts/plugin.schema.json 对 manifest 做强校验（总纲 §1.3.1）。

★ 这是"一切皆插件"的机器守门人之一：校验失败必须精确到
  「哪个插件 · 哪个字段 · 期望什么 · 实际什么」，不许静默跳过。

★ 原则：代码/Schema 为协议，文档为说明。
  本文件实现的是**协议本身**：字段形状由 contracts/plugin.schema.json 定义，
  跨字段一致性规则由本文件的 _check_consistency 实现。
  协议技术报告（Alongside_插件协议技术报告_v1.0.md）是这份代码的说明文档。

校验分三层（协议报告 §19）：
  ① 结构校验（JSON Schema 层）—— 类型、格式、必填、枚举、条件必填
  ② 跨字段一致性（单插件内）—— requires ∩ optionalDependencies = ∅ 等
  ③ 跨插件一致性（需全量插件）—— 见 core/plugins/crosscheck.py

实现说明：不引入 jsonschema 依赖，自实现一个覆盖本项目用到的 JSON Schema
关键字子集（type / required / enum / pattern / minLength / minimum /
maxLength / items / properties / additionalProperties / patternProperties /
allOf / anyOf / oneOf / if-then-else / const / 可空联合），足够支撑契约校验，
且错误信息完全可控。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.errors import ManifestError

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[4] / "contracts" / "plugin.schema.json"
)

_SCHEMA: dict[str, Any] = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))

# 能力名格式：<id>.<名词>.<动作>，至少一段点分
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z0-9-]+)+$")

# 定时选择器：六个，且必须只给一个
_SCHEDULE_SELECTORS = (
    "at",
    "after_seconds",
    "every_seconds",
    "daily",
    "weekly",
    "cron",
)

# parallelSafe 只允许只读操作
_READ_ONLY_OPS = {"read", "list"}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _match_type(value: Any, typ: Any) -> bool:
    """支持单类型与 [a, b, ...] 联合（本项目用于可空联合 [string, null]）。"""
    if isinstance(typ, list):
        return any(_match_type(value, t) for t in typ)
    name = _type_name(value)
    if typ == "integer":
        # JSON 里整数是 int；bool 不是 integer
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if typ == "boolean":
        return name == "boolean"
    if typ == "string":
        return name == "string"
    if typ == "array":
        return name == "array"
    if typ == "object":
        return name == "object"
    if typ == "null":
        return value is None
    return name == typ


def _validate_node(
    value: Any,
    schema: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    """递归校验一个节点。errors 里追加的是「人话」错误，不含栈信息。"""
    if not isinstance(schema, dict):
        return

    # ── const：值必须完全相等 ──
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path or '<root>'}: 期望常量 {schema['const']!r}，实际为 {value!r}")

    # ── type ──
    typ = schema.get("type")
    if typ is not None and not _match_type(value, typ):
        errors.append(
            f"{path or '<root>'}: 期望类型 {typ}，实际为 {_type_name(value)}"
        )
        # 类型都不对，后续细项校验无意义
        return

    # ── 枚举 ──
    if "enum" in schema and value not in schema["enum"]:
        errors.append(
            f"{path}: 取值 {value!r} 不在允许集合 {schema['enum']} 内"
        )

    # ── 否定：not ──
    #   注意方向：not 的内层 schema **通过** 时，not 本身**失败**。
    if "not" in schema:
        not_errors: list[str] = []
        _validate_node(value, schema["not"], path, not_errors)
        if not not_errors:
            errors.append(f"{path}: 不应满足被否定的约束 {schema['not']!r}")

    # ── 组合关键字：allOf / anyOf / oneOf ──
    if "allOf" in schema:
        for sub in schema["allOf"]:
            _validate_node(value, sub, path, errors)

    if "anyOf" in schema:
        branch_errors: list[list[str]] = []
        for sub in schema["anyOf"]:
            sub_errors: list[str] = []
            _validate_node(value, sub, path, sub_errors)
            if not sub_errors:
                break
            branch_errors.append(sub_errors)
        else:
            detail = " | ".join(
                "{" + "; ".join(e) + "}" for e in branch_errors
            )
            errors.append(f"{path}: 不满足任何一个允许的分支（{detail}）")

    if "oneOf" in schema:
        matched = 0
        for sub in schema["oneOf"]:
            one_errors: list[str] = []
            _validate_node(value, sub, path, one_errors)
            if not one_errors:
                matched += 1
        if matched != 1:
            errors.append(
                f"{path}: 必须恰好满足 oneOf 中的 1 个分支，实际满足 {matched} 个"
            )

    # ── 条件必填：if / then / else ──
    if "if" in schema:
        probe: list[str] = []
        _validate_node(value, schema["if"], path, probe)
        branch = "then" if not probe else "else"
        if branch in schema:
            _validate_node(value, schema[branch], path, errors)

    # ── 对象 ──
    if isinstance(value, dict):
        required = schema.get("required", [])
        for field in required:
            if field not in value:
                errors.append(f"{path}.{field}: 缺少必填字段（契约要求：{field}）")
        props = schema.get("properties", {})
        for field, sub in props.items():
            if field in value:
                _validate_node(value[field], sub, f"{path}.{field}", errors)
        # patternProperties：正则命名的额外属性
        pattern_props = schema.get("patternProperties", {})
        for pattern, sub in pattern_props.items():
            try:
                rx = re.compile(pattern)
            except re.error:
                continue
            for field, val in value.items():
                if field in props:
                    continue
                if rx.search(field):
                    _validate_node(val, sub, f"{path}.{field}", errors)
        addl = schema.get("additionalProperties", True)
        if addl is False:
            for field in value:
                if field in props:
                    continue
                if any(
                    re.search(p, field)
                    for p in pattern_props
                    if _safe_compile(p)
                ):
                    continue
                errors.append(
                    f"{path}.{field}: 未知字段（契约不允许，请删除或走 RFC 流程）"
                )
        elif isinstance(addl, dict):
            for field, val in value.items():
                if field in props:
                    continue
                if any(re.search(p, field) for p in pattern_props if _safe_compile(p)):
                    continue
                _validate_node(val, addl, f"{path}.{field}", errors)

    # ── 数组 ──
    elif isinstance(value, list):
        items_schema = schema.get("items")
        if items_schema is not None:
            for idx, item in enumerate(value):
                _validate_node(item, items_schema, f"{path}[{idx}]", errors)

    # ── 字符串（pattern 用 search 语义：JSON Schema 规范，正则不隐式锚定）──
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        errors.append(
            f"{path}: 值 {value!r} 不匹配格式 {schema['pattern']}"
        )
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        errors.append(
            f"{path}: 字符串长度至少为 {schema['minLength']}，实际为 {len(value)}"
        )
    if "maxLength" in schema and isinstance(value, str) and len(value) > schema["maxLength"]:
        errors.append(
            f"{path}: 字符串长度最多为 {schema['maxLength']}，实际为 {len(value)}"
        )

    # ── 数值 ──
    if "minimum" in schema and isinstance(value, int | float) and value < schema["minimum"]:
        errors.append(
            f"{path}: 数值至少为 {schema['minimum']}，实际为 {value}"
        )
    if "maximum" in schema and isinstance(value, int | float) and value > schema["maximum"]:
        errors.append(
            f"{path}: 数值最多为 {schema['maximum']}，实际为 {value}"
        )


def _safe_compile(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


# ══════════════════════════════════════════════════════════════════════
# ② 跨字段一致性（单插件内）—— 协议报告 §19 第②层 / §20 规则表
# ══════════════════════════════════════════════════════════════════════


def _check_consistency(raw: dict[str, Any]) -> list[str]:
    """单 manifest 内部的跨字段一致性校验。

    ★ 每一项都对应协议报告 §20 规则表里的一条。规则的特征是：
      单看每个字段都合法，但组合起来语义矛盾。
    """
    errors: list[str] = []

    kind = raw.get("kind")
    req = raw.get("requires") or []
    opt = raw.get("optionalDependencies") or []

    # 规则 2：requires ∩ optionalDependencies = ∅
    dup = sorted(set(req) & set(opt))
    if dup:
        errors.append(
            f"manifest.requires 与 manifest.optionalDependencies 重复声明 {dup}："
            "同一能力不得既是硬依赖又是软依赖（从 optionalDependencies 里删掉它）"
        )

    # 规则 3：kind != core 时 approval != never
    execution = raw.get("execution") or {}
    approval = execution.get("approval")
    if kind != "core" and approval == "never":
        errors.append(
            f"manifest.execution.approval = 'never' 仅允许 kind='core' 使用"
            f"（当前 kind={kind!r}）：never 会把插件锁死成永远不可用，"
            "请改为 'ask'"
        )

    # 规则 4：third-party 不得声明 riskLevel L0
    if kind == "third-party" and raw.get("riskLevel") == "L0":
        errors.append(
            "manifest.riskLevel = 'L0' 不允许 kind='third-party' 声明："
            "免审批的收益归插件自己，代价归用户，因此这一项只能由内核推断"
        )

    # 规则 5：signed ⟹ signature
    origin = raw.get("origin") or {}
    if origin.get("signed") is True and not origin.get("signature"):
        errors.append(
            "manifest.origin.signed = true 但缺少 manifest.origin.signature"
        )
    # 规则 6：installedBy 必填
    # ★ 同上：仅当 origin 存在时才要求完整（存量插件没有 origin 字段）。
    #   一旦声明了 origin，它就是一份「来源承诺」，缺字段就等于没承诺 → 拒。
    if "origin" in raw and not origin.get("installedBy"):
        errors.append(
            "manifest.origin.installedBy 必填（缺失则无法审计谁装了它）"
        )

    # 规则 7：migrations ⟹ concurrency.exclusive
    # 规则 8：migrations ⟹ track == persistent
    # ★ 缺失 vs 显式错的区分（协议落地关键）：
    #   字段**缺失** → 按内核推断的保守默认值处理（迁移独占天然 = true）。
    #     否则 18 个存量插件会因「没写新字段」而集体起不来 —— 那不是协议收紧，
    #     是协议升级把存量系统打死了。
    #   字段**显式声明错** → 启动失败（这才是真正要拦的语义矛盾）。
    has_migrations = raw.get("migrations") not in (None, "", [])
    concurrency = raw.get("concurrency") or {}
    if has_migrations:
        if "exclusive" in concurrency and concurrency.get("exclusive") is not True:
            errors.append(
                "manifest.migrations 非空时必须声明 manifest.concurrency.exclusive = true"
                "（迁移必须独占，否则并发跑迁移会损坏数据）"
            )
        if "track" in raw and raw.get("track") != "persistent":
            errors.append(
                "manifest.migrations 非空时 manifest.track 必须为 'persistent'"
                "（临时插件不该改数据库结构，否则会留下无人管理的残留表）"
            )

    # 规则 9：sideEffects.external ⟹ checkpoint == before-side-effect
    side = raw.get("sideEffects") or {}
    if side.get("external") is True and side.get("checkpoint") != "before-side-effect":
        errors.append(
            "manifest.sideEffects.external = true 时必须声明 "
            "manifest.sideEffects.checkpoint = 'before-side-effect'"
            "（检查点是出事之后能恢复的前提）"
        )

    # 规则 10：parallelSafe 只能含只读操作
    parallel_safe = concurrency.get("parallelSafe") or []
    bad_ops = sorted(
        op for op in parallel_safe if op not in _READ_ONLY_OPS
    )
    if bad_ops:
        errors.append(
            f"manifest.concurrency.parallelSafe 含非只读操作 {bad_ops}："
            "只有 read / list 可以标为可并发"
        )

    # 规则 11：optimisticLock 不许 false（除非 exclusive）
    if (
        concurrency.get("optimisticLock") is False
        and concurrency.get("exclusive") is not True
    ):
        errors.append(
            "manifest.concurrency.optimisticLock = false 仅在 "
            "manifest.concurrency.exclusive = true 时允许"
            "（非独占状态下关掉乐观锁 = 允许丢失更新）"
        )

    # 规则 14：schedule 选择器互斥
    schedule = raw.get("schedule") or {}
    given = [s for s in _SCHEDULE_SELECTORS if s in schedule]
    if len(given) > 1:
        errors.append(
            f"manifest.schedule 同时给了多个选择器 {given}："
            "at / after_seconds / every_seconds / daily / weekly / cron 必须只给一个"
        )
    if schedule and not given:
        errors.append(
            "manifest.schedule 必须声明且只声明一个选择器"
            "（at / after_seconds / every_seconds / daily / weekly / cron）"
        )
    if "every_seconds" in schedule and schedule["every_seconds"] == 86400:
        errors.append(
            "manifest.schedule.every_seconds = 86400 不能替代 daily："
            "固定间隔对齐创建时刻，daily 对齐墙上时间，是两种语义"
        )

    return errors


# ══════════════════════════════════════════════════════════════════════
# 公开入口
# ══════════════════════════════════════════════════════════════════════


def validate_manifest(raw: dict[str, Any]) -> dict[str, Any]:
    """校验 manifest 字典。

    顺序（协议报告 §19）：① 结构校验失败即停；② 跨字段一致性继续查。
    ② 不因为 ① 失败而跳过——但 ① 失败时 ② 的结论多半无意义，
    故只有 ① 通过才跑 ②（结构错了往下查没意义）。
    """
    errors: list[str] = []
    _validate_node(raw, _SCHEMA, "manifest", errors)
    if errors:
        raise ManifestError(
            "manifest 校验失败（对照 contracts/plugin.schema.json）：\n  - "
            + "\n  - ".join(errors)
        )

    consistency = _check_consistency(raw)
    if consistency:
        raise ManifestError(
            "manifest 跨字段一致性校验失败：\n  - "
            + "\n  - ".join(consistency)
        )
    return raw


def validate_manifest_text(text: str, source: str = "manifest.json") -> dict[str, Any]:
    """从文本校验（顺便检查 JSON 是否合法）。"""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{source} 不是合法 JSON：{exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{source} 根必须是对象")
    return validate_manifest(raw)
