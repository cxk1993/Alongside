"""B1 / B2 校验 —— 包结构（B1）与包内一致性（B2）。

★ 报错即文档（I12）：每条拒绝都要报出「哪个字段 / 什么值 / 为什么不接受」。
★ 不发明规则：跨插件冲突交给既有的 `core.plugins.crosscheck`，包层只汇总（§10.2 第 3 条）。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from core.plugins.version import KERNEL_API_VERSION, satisfies
from packs.model import (
    MAX_INCLUDE_DEPTH,
    PACK_FORMAT,
    MemberRef,
    PackError,
    PackManifest,
    PackRef,
    PermissionBudget,
    SecretRef,
)

log = logging.getLogger("kernel.packs.validate")

# ─────────────────────────── 字段白名单（B1-2） ───────────────────────────

TOP_LEVEL_FIELDS = frozenset(
    {
        "packFormat", "id", "name", "version", "kind", "kernelApi",
        "description", "author", "license", "homepage", "source",
        "members", "config", "secrets", "permissionBudget", "includes",
        "providerChoice",
        "icon", "tagline", "onboarding", "entry",
        # 预留位（第一期不生效，但形状仍校验 —— B1-7 / 报告 §17）
        "derivedFrom", "overrides", "deprecated", "renames", "removedAt",
        "views", "extensions",
    }
)

RESERVED_FIELDS = frozenset(
    {"derivedFrom", "overrides", "deprecated", "renames", "removedAt", "views", "extensions"}
)

RISK_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
"""风险级次序。数值越小越安全。"""

_KIND_BY_PREFIX = {"official": "official"}

_ID_RE = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+$")
"""**包的** id —— 反向命名空间，至少两段（official.life-tasks）。"""

_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
"""**成员的** id —— 就是插件 id，单段小写连字符（finance / weekly-report）。

★ 两者不能混用：包是命名空间，插件是名字。用包的规则校验成员 id，
  会把所有真实插件（finance / todo）全部判成非法。
"""
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CAP_RE = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z0-9-]+)+$")

# 可选字段的期望类型。★ 键必须是 TOP_LEVEL_FIELDS 的子集（门禁会查）。
_OPTIONAL_FIELD_TYPES: dict[str, type | tuple[type, ...]] = {
    "description": str,
    "author": str,
    "license": str,
    "homepage": str,
    "icon": str,
    "tagline": str,
    "entry": str,
    "source": dict,
    "onboarding": list,
}


def _type_name(want: type | tuple[type, ...]) -> str:
    """把期望类型说成人话。"""
    if isinstance(want, tuple):
        return " / ".join(t.__name__ for t in want)
    return want.__name__


# ─────────────────────────── 形状解析（B1-1 ~ B1-3、B1-7） ───────────────────────────


def parse_manifest(raw: Mapping[str, Any], *, directory: Path | None = None) -> PackManifest:
    """把 `pack.json` 的原始字典变成 `PackManifest`，顺带跑 B1-1 / B1-2 / B1-3 / B1-7。"""
    if not isinstance(raw, Mapping):
        raise PackError("pack.json 根必须是对象。", code="B1-3")

    # B1-1 · packFormat
    fmt = raw.get("packFormat")
    if fmt != PACK_FORMAT:
        raise PackError(
            f"packFormat 缺失或为 {fmt!r}，本实现支持 {PACK_FORMAT}。"
            "这个包是按更新的清单格式写的。",
            code="B1-1",
        )

    # B1-2 · 未知顶层字段
    unknown = sorted(set(raw) - TOP_LEVEL_FIELDS)
    if unknown:
        raise PackError(
            f"pack.json 有未知字段 {unknown}。包清单承载装配副作用，"
            "拼错的字段不会被忽略——请删掉它们，或确认拼写。",
            code="B1-2",
        )

    # B1-7 · 预留位形状（接受、校验形状、不生效）
    for reserved in sorted(RESERVED_FIELDS & set(raw)):
        value = raw[reserved]
        if not isinstance(value, dict | list | str | bool):
            raise PackError(
                f"预留字段 {reserved} 的形状不合法：{type(value).__name__}。"
                "它当前不生效，但仍会被校验。",
                code="B1-7",
            )

    # B1-3 · 必填与类型
    for key in ("id", "name", "version", "kind", "kernelApi"):
        if key not in raw:
            raise PackError(f"pack.json 缺少必填字段 {key}。", code="B1-3")
        if not isinstance(raw[key], str) or not raw[key]:
            raise PackError(f"字段 {key} 必须是非空字符串，实际为 {raw[key]!r}。", code="B1-3")

    if not _ID_RE.match(raw["id"]):
        raise PackError(
            f"包的 id={raw['id']!r} 不符合反向命名空间格式（如 official.life-tasks）。",
            code="B1-3",
        )
    if not _VERSION_RE.match(raw["version"]):
        raise PackError(f"包的 version={raw['version']!r} 必须是三段式语义化版本。", code="B1-3")
    if raw["kind"] not in {"official", "community", "local"}:
        raise PackError(
            f"包的 kind={raw['kind']!r} 不在官方/社区/本地三类之内。", code="B1-3"
        )

    # B1-3 · 可选字段的类型
    #   ★ 为什么连这些也要查：包清单会被界面、审计、市场索引直接消费。
    #     早期实现只查了 5 个必填字段，于是 `description: 123`、
    #     `source: "x"` 这类脏值能一路过校验，落到消费者手里才炸——
    #     报错点离出错点很远，排查成本高。形状查在前头，错在门口。
    for key, want in _OPTIONAL_FIELD_TYPES.items():
        if key not in raw:
            continue
        value = raw[key]
        if not isinstance(value, want):
            raise PackError(
                f"字段 {key} 的类型不合法：期望 {_type_name(want)}，实际为 {type(value).__name__}。",
                code="B1-3",
            )

    members_raw = raw.get("members") or {}
    if not isinstance(members_raw, Mapping):
        raise PackError("members 必须是对象。", code="B1-3")
    unknown_members = sorted(set(members_raw) - {"required", "optional", "excluded"})
    if unknown_members:
        raise PackError(
            f"members 下有未知字段 {unknown_members}。合法的是 required / optional / excluded。",
            code="B1-3",
        )

    required = tuple(_member(m, where="required") for m in members_raw.get("required", []))
    optional = tuple(_member(m, where="optional") for m in members_raw.get("optional", []))
    excluded = tuple(str(x) for x in members_raw.get("excluded", []))
    includes = tuple(_pack_ref(x) for x in raw.get("includes", []))

    if "includes" in raw and not isinstance(raw["includes"], list):
        raise PackError("includes 必须是数组。", code="B1-3")

    config_raw = raw.get("config") or {}
    if not isinstance(config_raw, Mapping):
        raise PackError("config 必须是对象（成员 id → 该成员的配置）。", code="B1-3")
    config: dict[str, dict[str, Any]] = {}
    for member_id, value in config_raw.items():
        if not isinstance(value, Mapping):
            raise PackError(f"config.{member_id} 必须是对象。", code="B1-3")
        config[str(member_id)] = dict(value)

    secrets = tuple(_secret(s) for s in raw.get("secrets", []))
    budget = _budget(raw.get("permissionBudget"))
    provider_choice = _provider_choice(raw.get("providerChoice"))

    return PackManifest(
        pack_format=fmt,
        id=raw["id"],
        name=raw["name"],
        version=raw["version"],
        kind=raw["kind"],
        kernel_api=raw["kernelApi"],
        required=required,
        optional=optional,
        excluded=excluded,
        includes=includes,
        config=config,
        secrets=secrets,
        permission_budget=budget,
        provider_choice=provider_choice,
        entry=raw.get("entry"),
        license=raw.get("license"),
        raw=dict(raw),
        directory=directory,
    )


def _provider_choice(value: Any) -> dict[str, str]:
    """B1-8 · `providerChoice` 的形状（键 = 能力名，值 = 成员插件 id）。

    ★ 键与值形状**不同**，必须分别查：
      · 键是能力名（`<域>.<实体>`，带点）；
      · 值是成员插件 id（单段小写连字符，如 `finance`）。
      写成一样的正则，就会出现「拿成员 id 去校验能力名」这种静默放行。
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PackError(
            f"providerChoice 必须是对象（能力名 → 提供者插件 id），实际为 {type(value).__name__}。",
            code="B1-8",
        )
    result: dict[str, str] = {}
    for cap, pid in value.items():
        cap = str(cap)
        pid = str(pid)
        if not _CAP_RE.match(cap):
            raise PackError(
                f"providerChoice 的键 {cap!r} 不是合法能力名（应为 <域>.<实体>，如 finance.bill.read）。",
                code="B1-8",
            )
        if not _PLUGIN_ID_RE.match(pid):
            raise PackError(
                f"providerChoice[{cap!r}] 的值 {pid!r} 不是合法插件 id"
                "（单段小写连字符，如 finance）。",
                code="B1-8",
            )
        result[cap] = pid
    return result


def _member(value: Any, *, where: str) -> MemberRef:
    if not isinstance(value, Mapping):
        raise PackError(f"members.{where} 的每一项必须是对象。", code="B1-3")
    mid, version = value.get("id"), value.get("version")
    if not isinstance(mid, str) or not _PLUGIN_ID_RE.match(mid):
        raise PackError(
            f"members.{where} 里的 id={mid!r} 不符合插件 id 格式（单段小写连字符，如 finance）。",
            code="B1-3",
        )
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise PackError(
            f"成员 {mid} 的 version={version!r} 必须是精确三段式（包层没有版本求解器）。",
            code="B1-3",
        )
    digest = value.get("digest")
    if digest is not None and (not isinstance(digest, str) or not _DIGEST_RE.match(digest)):
        raise PackError(
            f"成员 {mid} 的 digest={digest!r} 不合法：必须是 sha256: 加 64 位小写十六进制。"
            "人不可能手写，请用工具生成。",
            code="B1-3",
        )
    return MemberRef(id=mid, version=version, digest=digest)


def _pack_ref(value: Any) -> PackRef:
    if not isinstance(value, Mapping):
        raise PackError("includes 的每一项必须是对象。", code="B1-3")
    pid, version = value.get("id"), value.get("version")
    if not isinstance(pid, str) or not _ID_RE.match(pid):
        raise PackError(f"includes 里的包 id={pid!r} 格式不合法。", code="B1-3")
    if not isinstance(version, str) or not (version.startswith("^") or _VERSION_RE.match(version)):
        raise PackError(
            f"includes 里 {pid} 的 version={version!r} 必须是精确三段式或 caret 区间。",
            code="B1-3",
        )
    return PackRef(id=pid, version=version)


def _secret(value: Any) -> SecretRef:
    if not isinstance(value, Mapping):
        raise PackError("secrets 的每一项必须是对象。", code="B1-3")
    key, title = value.get("key"), value.get("title")
    if not isinstance(key, str) or not key.isupper():
        raise PackError(
            f"secrets 里的 key={key!r} 必须是大写环境变量风格（如 BILL_MAIL_TOKEN）。",
            code="B1-3",
        )
    if not isinstance(title, str) or not title:
        raise PackError(f"凭据 {key} 缺少面向用户的中文 title。", code="B1-3")
    return SecretRef(
        key=key,
        title=title,
        sensitive=bool(value.get("sensitive", False)),
        required=bool(value.get("required", False)),
        for_member=value.get("for"),
    )


def _budget(value: Any) -> PermissionBudget | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PackError("permissionBudget 必须是对象。", code="B1-3")
    level = value.get("objectLevel")
    if level not in RISK_ORDER:
        raise PackError(
            f"permissionBudget.objectLevel={level!r} 必须是 L0 / L1 / L2 / L3 之一。",
            code="B1-3",
        )
    caps = value.get("capabilities", [])
    if not isinstance(caps, list) or any(not isinstance(c, str) for c in caps):
        raise PackError("permissionBudget.capabilities 必须是字符串数组。", code="B1-3")
    for cap in caps:
        if not _CAP_RE.match(cap):
            raise PackError(
                f"能力 {cap!r} 不符合 <id>.<名词>.<动作> 格式。", code="B1-3"
            )
    approvals = value.get("approvalRequired", [])
    if not isinstance(approvals, list):
        raise PackError("permissionBudget.approvalRequired 必须是数组。", code="B1-3")
    return PermissionBudget(
        object_level=level,
        capabilities=tuple(caps),
        approval_required=tuple(str(a) for a in approvals),
    )


# ─────────────────── 锁与快照的形状校验（LOCK-1 ~ LOCK-6） ───────────────────

#: 锁与快照的**顶层必填字段**。两者同构（§15.1「形状与基线锁同构」）。
LOCK_REQUIRED_FIELDS = frozenset({"packId", "packVersion", "generatedAt"})

#: 锁与快照的**顶层可选字段**。
LOCK_OPTIONAL_FIELDS = frozenset({"selfDigest", "kernelApi", "members"})

#: 成员记录的必填字段。
LOCK_MEMBER_REQUIRED = frozenset({"id", "version"})

#: 成员记录的可选字段。
LOCK_MEMBER_OPTIONAL = frozenset({"digest", "fromPacks"})


def validate_lock_payload(payload: Mapping[str, Any], *, kind: str = "锁") -> None:
    """校验一份锁 / 快照的**形状**。**写之前必须过这一关。**

    ★ 为什么要有这一步
    ------------------
    读侧是**宽容**的（坏文件跳过、不让整个包装不上）——那是对的。
    但**写侧必须严格**：写出来的东西是自己生成的，
    生成一份坏形状，等于把问题推给下游：
    - `selfDigest` 写成 `sha256:xyz` ⇒ 后续每次装载都判「摘要不符」⇒
      **用户永远装不上这个包**，而且报错信息指向「内容被改过」，完全指错方向；
    - `packId` 写成 `finance`（那是插件 id 的形状）⇒ 别人对账时
      「这个锁属于哪个包」就答不上来了；
    - 成员 id 写成 `a.b` ⇒ 它是**插件** id，包的命名空间规则不适用于它。
    ⇒ 与其让下游去承受，不如在写出去之前挡住。

    ★ `kind` 只影响报错措辞（「锁」/「快照」），不改变规则——
      两者同构，规则只有一份。
    """
    unknown = sorted(set(payload) - LOCK_REQUIRED_FIELDS - LOCK_OPTIONAL_FIELDS)
    if unknown:
        raise PackError(
            f"{kind}里有未知字段 {unknown}。锁与快照的字段是封闭的——"
            "多出来的字段不会被忽略，而会被下游当成「不认识的东西」静默跳过。",
            code="LOCK-1",
        )

    missing = sorted(LOCK_REQUIRED_FIELDS - set(payload))
    if missing:
        raise PackError(f"{kind}缺少必填字段 {missing}。", code="LOCK-2")

    pack_id = payload.get("packId")
    if not isinstance(pack_id, str) or not _ID_RE.match(pack_id):
        raise PackError(
            f"{kind}的 packId={pack_id!r} 不符合反向命名空间格式（如 official.life-tasks）。"
            "它必须与包清单的 id 同格式——否则「这份锁属于哪个包」就答不上来。",
            code="LOCK-3",
        )

    pack_version = payload.get("packVersion")
    if not isinstance(pack_version, str) or not _VERSION_RE.match(pack_version):
        raise PackError(
            f"{kind}的 packVersion={pack_version!r} 必须是三段式语义化版本。",
            code="LOCK-4",
        )

    generated_at = payload.get("generatedAt")
    if not isinstance(generated_at, str) or not generated_at:
        raise PackError(f"{kind}的 generatedAt 必须是非空字符串。", code="LOCK-2")

    self_digest = payload.get("selfDigest")
    if self_digest is not None and (
        not isinstance(self_digest, str) or not _DIGEST_RE.match(self_digest)
    ):
        raise PackError(
            f"{kind}的 selfDigest={self_digest!r} 不合法：必须是 sha256: 加 64 位小写十六进制。"
            "写错的摘要比没有摘要更糟——它会让后续每一次装载都判「不符」。",
            code="LOCK-5",
        )

    kernel_api = payload.get("kernelApi")
    if kernel_api is not None and (not isinstance(kernel_api, str) or not kernel_api):
        raise PackError(f"{kind}的 kernelApi 必须是非空字符串。", code="LOCK-2")

    members = payload.get("members")
    if members is None:
        return
    if not isinstance(members, list):
        raise PackError(f"{kind}的 members 必须是数组。", code="LOCK-6")
    for m in members:
        _validate_lock_member(m, kind=kind)


def _validate_lock_member(member: Any, *, kind: str) -> None:
    """校验一条成员记录。"""
    if not isinstance(member, Mapping):
        raise PackError(f"{kind}的 members 每一项必须是对象。", code="LOCK-6")

    unknown = sorted(set(member) - LOCK_MEMBER_REQUIRED - LOCK_MEMBER_OPTIONAL)
    if unknown:
        raise PackError(
            f"{kind}的成员里有未知字段 {unknown}。", code="LOCK-1"
        )

    missing = sorted(LOCK_MEMBER_REQUIRED - set(member))
    if missing:
        raise PackError(f"{kind}的成员缺少必填字段 {missing}。", code="LOCK-2")

    mid = member.get("id")
    if not isinstance(mid, str) or not _PLUGIN_ID_RE.match(mid):
        raise PackError(
            f"{kind}的成员 id={mid!r} 不符合插件 id 格式（单段小写连字符，如 finance）。"
            "成员指的是插件，用包的命名空间规则校验它会把所有真实插件全判成非法。",
            code="LOCK-3",
        )

    version = member.get("version")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise PackError(
            f"{kind}的成员 {mid} 的 version={version!r} 必须是三段式语义化版本。",
            code="LOCK-4",
        )

    digest = member.get("digest")
    if digest is not None and (
        not isinstance(digest, str) or not _DIGEST_RE.match(digest)
    ):
        raise PackError(
            f"{kind}的成员 {mid} 的 digest={digest!r} 不合法："
            "必须是 sha256: 加 64 位小写十六进制。",
            code="LOCK-5",
        )

    from_packs = member.get("fromPacks")
    if from_packs is not None and (
        not isinstance(from_packs, list)
        or any(not isinstance(p, str) or not p for p in from_packs)
    ):
        raise PackError(
            f"{kind}的成员 {mid} 的 fromPacks 必须是非空字符串组成的数组。",
            code="LOCK-6",
        )


# ─────────────────────────── 结构校验（B1-4 ~ B1-8） ───────────────────────────


def validate_structure(manifest: PackManifest, *, dirname: str | None = None) -> None:
    """B1-4 ~ B1-8。不需要读取成员插件。"""
    # B1-4 · id 等于目录名
    if dirname is not None and manifest.id != dirname:
        raise PackError(
            f"pack.json 的 id={manifest.id} 必须等于目录名 {dirname}。", code="B1-4"
        )

    # B1-5 · 保留名前缀与 kind 一致
    if manifest.id.startswith("official.") and manifest.kind != "official":
        raise PackError(
            f"id 以 official. 开头，但 kind={manifest.kind}。"
            "official.* 只允许官方包使用。",
            code="B1-5",
        )

    # B1-6 · 路径逃逸（扫 plugins/ 与所有引用路径）
    if manifest.directory is not None:
        _check_no_escape(manifest.directory)

    # B1-8 · 包的 kernelApi 满足当前内核（caret 语义）
    if not satisfies(manifest.kernel_api, KERNEL_API_VERSION):
        raise PackError(
            f"包要求内核接口 {manifest.kernel_api}，当前内核为 {KERNEL_API_VERSION}。"
            "整包不适用当前内核——请升级内核或换包版本。",
            code="B1-8",
        )


def _check_no_escape(pack_dir: Path) -> None:
    """扫一遍包目录里的实体路径，确保都留在包内。"""
    root = pack_dir.resolve()
    for child in pack_dir.rglob("*"):
        try:
            child.resolve().relative_to(root)
        except ValueError:
            raise PackError(
                f"路径 {child} 越出包目录：软链接指向了包外。"
                "装配会写文件，路径必须留在允许的范围内。",
                code="B1-6",
            ) from None


# ─────────────────────────── 一致性校验（B2-1 ~ B2-6、B2-10、B2-11） ───────────────────────────


def validate_consistency(
    manifest: PackManifest,
    plugins: Mapping[str, Mapping[str, Any]],
) -> None:
    """B2 的单包部分。`plugins` 是「插件 id → 插件 manifest」的映射。"""
    required_ids = [m.id for m in manifest.required]
    optional_ids = [m.id for m in manifest.optional]

    # B2-1 · excluded ∩ required = ∅
    overlap = sorted(set(manifest.excluded) & set(required_ids))
    if overlap:
        raise PackError(
            f"成员 {overlap[0]} 同时出现在 required 与 excluded。"
            "排除一个必需成员会让包不成立——要么把它移进 optional，要么从 excluded 移除。",
            code="B2-1",
        )

    # B2-2 · 成员 id 唯一
    seen: dict[str, str] = {}
    for where, ids in (("required", required_ids), ("optional", optional_ids)):
        for mid in ids:
            if mid in seen:
                raise PackError(
                    f"成员 {mid} 被声明了两次（{seen[mid]} 与 {where}）。", code="B2-2"
                )
            seen[mid] = where

    # B2-10 · remote 第一期拒绝
    source = manifest.raw.get("source") or {}
    if isinstance(source, Mapping) and source.get("type") == "remote":
        raise PackError("源类型 remote 尚未开放。", code="B2-10")

    # B2-11 · 引用第三方成员时 license 必写
    for member in manifest.required:
        plugin = plugins.get(member.id)
        if plugin is None:
            continue
        if plugin.get("kind") == "third-party" and not manifest.license:
            raise PackError(
                f"成员 {member.id} 是第三方插件，但没有声明 license。发行合规需要它。",
                code="B2-11",
            )

    budget = manifest.permission_budget
    if budget is not None:
        declared: set[str] = set()
        for member in manifest.required:
            plugin = plugins.get(member.id) or {}
            declared.update(str(c) for c in plugin.get("provides", []) or [])

        # B2-3 · 预算 ⊆ 必需成员声明之并集
        extra = sorted(set(budget.capabilities) - declared)
        if extra:
            raise PackError(
                f"包声明的能力 {extra[0]} 没有任何必需成员声明过。"
                "包的预算是上限，不是授予——它只能收紧成员的声明，不能扩大。",
                code="B2-3",
            )

        # B2-5 · 不得声明 L3
        if budget.object_level == "L3":
            raise PackError(
                "包声明了 L3（内核级）。包不得声明对内核的操作。", code="B2-5"
            )

        # B2-4 · objectLevel 不高于成员最高级
        member_max_level, member_max_id = "L0", None
        for member in manifest.required:
            plugin = plugins.get(member.id) or {}
            level = effective_risk_level(plugin)
            if RISK_ORDER[level] > RISK_ORDER[member_max_level]:
                member_max_level, member_max_id = level, member.id
        if RISK_ORDER[budget.object_level] > RISK_ORDER[member_max_level]:
            raise PackError(
                f"包的 objectLevel={budget.object_level} 高于其成员的最高风险级 "
                f"{member_max_level}（来自 {member_max_id}）。",
                code="B2-4",
            )

    # B2-6 · secrets[].for 指向存在的成员
    known = set(required_ids) | set(optional_ids)
    for secret in manifest.secrets:
        if secret.for_member is not None and secret.for_member not in known:
            raise PackError(
                f"凭据 {secret.key} 指定给成员 {secret.for_member}，但该成员不在包内。",
                code="B2-6",
            )

    _validate_provider_choice(manifest, plugins, known)


def _validate_provider_choice(
    manifest: PackManifest,
    plugins: Mapping[str, Mapping[str, Any]],
    known_members: set[str],
) -> None:
    """B2-12 · `providerChoice` 的四项校验（报告 §10.5）。

    ★ 键和值都必须落在**本包成员内**——包不能替外部世界做选择。
      这与 I5「预算不可放大」是同一条红线：包只能引用与收窄，不能向外发号施令。

    ★ 与「装配后仍冲突」的区别（这两件事不要混）：
      · 这里校验的是**清单写错**（选了包外的插件、选了本包没人提供的能力）→ 硬拒绝；
      · 「本包内确实有两个提供者、但没写 providerChoice」→ **不拒绝**，
        那是**待裁决冲突**，装配层处理（见 `packs/assembler.py`）。
    """
    if not manifest.provider_choice:
        return

    # 本包成员各自 provides 的能力 → 提供者列表
    provides_of: dict[str, list[str]] = {}
    for member_id in sorted(known_members):
        plugin = plugins.get(member_id) or {}
        for cap in plugin.get("provides", []) or []:
            provides_of.setdefault(str(cap), []).append(member_id)

    for cap, pid in sorted(manifest.provider_choice.items()):
        candidates = provides_of.get(cap, [])

        if not candidates:
            # 键不在本包成员的 provides 并集内
            if pid not in known_members:
                raise PackError(
                    f"providerChoice[{cap!r}] 指定了 {pid!r}，但 {pid} 不在本包成员内"
                    f"（本包成员：{sorted(known_members) or '（空）'}）。"
                    "包不能替包外的插件做选择。",
                    code="B2-12",
                )
            raise PackError(
                f"providerChoice[{cap!r}] 指向成员 {pid!r}，但本包内没有任何成员 provides 这个能力"
                f"（本包成员 provides 的能力：{sorted(provides_of) or '（空）'}）。",
                code="B2-12",
            )

        if pid not in candidates:
            raise PackError(
                f"providerChoice[{cap!r}] 指定 {pid!r}，但该能力在本包内的提供者是 "
                f"{', '.join(candidates)}。",
                code="B2-12",
            )

        if len(candidates) == 1:
            # 只有一个提供者时显式指定无意义 —— 警告，不阻断。
            # ★ 用日志而不是抛错：这是「写了但没必要」，不是「写错了」。
            log.warning(
                "providerChoice 指定了唯一提供者 %s（能力 %s），显式指定无意义。",
                pid,
                cap,
            )


def effective_risk_level(plugin: Mapping[str, Any]) -> str:
    """插件声明的 riskLevel；未声明时按 kind 推断（与插件协议一致）。"""
    declared = plugin.get("riskLevel")
    if declared in RISK_ORDER:
        return str(declared)
    return {"core": "L3", "builtin": "L2", "third-party": "L2", "container": "L1"}.get(
        str(plugin.get("kind", "third-party")), "L2"
    )


# ─────────────────────────── 嵌套包展开（B2-7 ~ B2-9） ───────────────────────────


def expand_includes(
    manifest: PackManifest,
    load_pack: Callable[[str], PackManifest | None],
) -> tuple[list[PackManifest], dict[str, list[str]]]:
    """深度优先展开 `includes`。

    返回 (展开后的包清单（含自身，依赖在前）, 成员归属表 member_id → [pack_id...])。

    ★ 三条纪律：
      · 限深 `MAX_INCLUDE_DEPTH`、必须做环检测（B2-7）；
      · 同一成员被不同包以不同版本引入 → **报错**，不裁决（B2-9）；
      · 同一成员的 `config` 只能由一个包提供 → **报错**，不按装载顺序裁决（B2-8）。
    """
    ordered: list[PackManifest] = []
    origin: dict[str, list[str]] = {}
    versions: dict[str, dict[str, str]] = {}
    config_owner: dict[str, str] = {}
    visiting: list[str] = []

    def walk(current: PackManifest, depth: int) -> None:
        if current.id in visiting:
            chain = " → ".join(visiting + [current.id])
            raise PackError(f"包依赖成环：{chain}。", code="B2-7")
        if depth > MAX_INCLUDE_DEPTH:
            raise PackError(
                f"包嵌套深度 {depth} 超过上限 {MAX_INCLUDE_DEPTH}。", code="B2-7"
            )
        visiting.append(current.id)
        for ref in current.includes:
            child = load_pack(ref.id)
            if child is None:
                raise PackError(
                    f"includes 里的包 {ref.id} 不存在或校验未通过。", code="B2-7"
                )
            if not satisfies(ref.version, child.version):
                raise PackError(
                    f"includes 要求 {ref.id} 的版本 {ref.version}，实际为 {child.version}。",
                    code="B2-7",
                )
            walk(child, depth + 1)
        visiting.pop()

        for member in current.all_members:
            origin.setdefault(member.id, [])
            if current.id not in origin[member.id]:
                origin[member.id].append(current.id)
            versions.setdefault(member.id, {})[current.id] = member.version
            if member.id in current.config:
                previous = config_owner.get(member.id)
                if previous is not None and previous != current.id:
                    raise PackError(
                        f"成员 {member.id} 的配置同时被 {previous} 与 {current.id} 提供。"
                        "同一个成员的配置只能由一个包提供——否则最终配置会取决于装载顺序，"
                        "而装载顺序不是用户能预期或审计的。",
                        code="B2-8",
                    )
                config_owner[member.id] = current.id
        ordered.append(current)

    walk(manifest, 0)

    # B2-9 · 版本冲突必须报错，不择一
    for member_id, per_pack in versions.items():
        distinct = set(per_pack.values())
        if len(distinct) > 1:
            pairs = "，".join(f"{v} 来自 {p}" for p, v in per_pack.items())
            raise PackError(
                f"成员 {member_id} 被要求两个版本（{pairs}）。"
                "包层没有版本求解器，也不该有「自动选一个」——请人工对齐版本。",
                code="B2-9",
            )

    return ordered, origin
