#!/usr/bin/env python
"""包层不变量门禁 —— 逐条实现协议 §6 的 I1~I13（静态可判部分）。

★ 为什么需要这个文件
--------------------
§6 的不变量表是包层唯一一份「什么算装对了」的清单。它此前只存在于文档里，
而文档不会在有人改坏 `packs/` 时报警。本脚本把它变成跑一次就知道的东西。

★ 三条纪律（照内核既有门禁的写法）
--------------------------------
1. **每条不变量单独可判**：一条失败不影响其余各条继续跑——
   只报第一条会让人来回好几轮。
2. **判不了就说判不了**：运行期行为（I8 / I10 / I13）静态不可判，
   记 `skip` 并写明「由哪个单测覆盖」，**不记 pass**。
   门禁最常见的假绿是「目标不存在 ⇒ 扫了零个文件 ⇒ 零违规 ⇒ 退出码 0」，
   所以无包时打印「无包，跳过」+ 扫到的包数与路径，而不是安静地绿。
3. **契约与写侧必须对得上**：真实锁文件用 `contracts/pack-lock.schema.json`
   交叉校验；另外用**真实写侧函数**（`packs.state.write_baseline_lock`）
   在临时目录生成一份锁再校验——证明契约接受代码实际写出的形状。
   ★ 后者是「写侧自测」，不是「仓库样本」；两者在输出里分开报，不混为一谈。

★ 为什么直接 import `packs.*` 而不重写规则
------------------------------------------
重写一遍白名单/正则，就是给自己造第二个真相源：实现改了、门禁没改，
门禁会继续绿着守一份已经不存在的规则。所以 I1 用 `validate.TOP_LEVEL_FIELDS`、
I9 用 `digest.pack_digest`、I12 直接调 `parse_manifest` / `validate_lock_payload`。
import 失败 ⇒ 退出码 1，不假装通过。

用法：
    python tools/gates/verify_pack_invariants.py
    python tools/gates/verify_pack_invariants.py --json
    python tools/gates/verify_pack_invariants.py --verbose

退出码：0 = 无违规（含「仓库里没有包」）；1 = 有违规 / 契约校验失败 / 实现加载失败。
"""
from __future__ import annotations

# pyright: reportMissingImports=false
# ↑ `paths` 与 `packs.*` 是**运行期注入 sys.path** 的（见下方 import 块），
#   静态分析器看不到它们，但运行时一定在——`services/api/scripts/pack.py`
#   用的是同一套做法。给这几行加 type: ignore 会把真正的 import 错误也一起吞掉。

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_TOOLS = Path(__file__).resolve().parents[1]
_REPO = _TOOLS.parent
for _entry in (str(_TOOLS), str(_REPO / "services" / "api")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from paths import (  # noqa: E402
    CONTRACTS,
    PACKS_BUNDLED,
    PACKS_DATA,
    exists_report,
    rel,
)

try:
    from packs.digest import content_digest, pack_digest
    from packs.model import MAX_INCLUDE_DEPTH, PackError, PackLock
    from packs.state import write_baseline_lock
    from packs.validate import (
        RISK_ORDER,
        TOP_LEVEL_FIELDS,
        parse_manifest,
        validate_lock_payload,
    )
except ImportError as exc:  # pragma: no cover —— 环境坏了要立刻说，不能静默
    print(
        f"✗ 无法加载包层实现（{type(exc).__name__}: {exc}）。\n"
        "  门禁不能在没有实现的情况下假装通过——请用 services/api/.venv 的解释器运行，"
        "或确认 services/api/packs/ 完整。",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

MANIFEST_NAME = "pack.json"
LOCK_NAME = "pack.lock.json"
SNAPSHOT_NAME = "pack.assembly.json"

#: 包清单里**不得出现**的能力字段（I11）。
#: ★ `permissionBudget.capabilities` 是**预算**（上限），不是能力声明，不在禁列——
#:   把它一起禁掉，等于禁止包收紧自己的权限，正好与红线二相反。
FORBIDDEN_CAPABILITY_FIELDS = frozenset({"provides", "defines", "requires", "consumes"})

PASS, FAIL, SKIP = "pass", "fail", "skip"

#: 不变量标题（照协议 §6 的表）。顺序即输出顺序。
INVARIANTS: dict[str, str] = {
    "I1": "未知顶层字段为 0",
    "I2": "id 等于目录名",
    "I3": "保留名不被误用",
    "I4": "无路径逃逸",
    "I5": "预算不放大",
    "I6": "config 唯一来源",
    "I7": "嵌套无环、不超深",
    "I8": "成员冲突报错",
    "I9": "基线与清单一致",
    "I10": "装载失败不写快照",
    "I11": "包不声明能力",
    "I12": "报错即文档",
    "I13": "用户调整不被装载覆盖",
}

#: 静态不可判、由单测覆盖的三条。写清覆盖位置，免得有人以为门禁漏了。
RUNTIME_ONLY: dict[str, str] = {
    "I8": "运行期行为：同一成员被两个包要求不同版本时 B2-9 必须报错。"
    "覆盖：services/api/tests_packs/test_packs.py",
    "I10": "运行期行为：装载失败不得写装配快照。"
    "覆盖：services/api/tests_packs/test_packs.py（快照断言）",
    "I13": "运行期行为：用户停用的成员在再次装载后仍停用（§21.4 规则一）。"
    "覆盖：services/api/tests_packs/test_packs.py",
}


@dataclass(frozen=True)
class Finding:
    """一条判定。`level` 取 pass / fail / skip。"""

    invariant: str
    pack: str
    level: str
    detail: str


# ─────────────────────────── 扫描 ───────────────────────────


def discover_pack_dirs(extra: list[Path] | None = None) -> list[Path]:
    """扫 `packs/`（发行版）与 `data/packs/`（本地）下所有含 pack.json 的目录。

    ★ 判据是**有 pack.json**，不是「有目录」：数据目录里还躺着 registry.json、
    state.json、pack.assembly.json、pack.json.<version> 留档，那些都不是包。

    ★ `extra` 是显式追加的包目录（`--pack-dir`）：作者要在自己的包上跑门禁时用，
      不必先把包放进仓库。显式 > 默认，且按解析后的真实路径去重。
    """
    found: list[Path] = []
    seen: set[Path] = set()
    for root in (PACKS_BUNDLED, PACKS_DATA):
        if not root.is_dir():
            continue
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            if (child / MANIFEST_NAME).is_file():
                key = child.resolve()
                if key not in seen:
                    seen.add(key)
                    found.append(child)
    for candidate in extra or []:
        path = Path(candidate)
        if not (path / MANIFEST_NAME).is_file():
            continue
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            found.append(path)
    return found


def discover_snapshots() -> list[Path]:
    """扫 `data/packs/*/pack.assembly.json`（装配快照住数据目录）。"""
    if not PACKS_DATA.is_dir():
        return []
    return sorted(p for p in PACKS_DATA.glob(f"*/{SNAPSHOT_NAME}") if p.is_file())


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ─────────────────────────── 单条不变量 ───────────────────────────


def check_i1(pack: str, raw: Any) -> list[Finding]:
    """I1 · 未知顶层字段为 0（B1-2）。

    ★ 白名单取自实现（`validate.TOP_LEVEL_FIELDS`），不在这里抄一份。
    """
    if not isinstance(raw, dict):
        return [Finding("I1", pack, FAIL, "pack.json 根不是对象（B1-3）。")]
    unknown = sorted(set(raw) - TOP_LEVEL_FIELDS)
    if unknown:
        return [
            Finding(
                "I1",
                pack,
                FAIL,
                f"未知顶层字段 {unknown}。包清单承载装配副作用，拼错的字段不会被忽略——"
                "请删掉它们，或确认拼写。",
            )
        ]
    return [Finding("I1", pack, PASS, f"顶层字段 {len(raw)} 个，全部在白名单内。")]


def check_i2(pack: str, raw: Any, pack_dir: Path) -> list[Finding]:
    """I2 · id 等于目录名（B1-4）。"""
    if not isinstance(raw, dict):
        return [Finding("I2", pack, SKIP, "pack.json 根不是对象，无法取 id。")]
    declared = raw.get("id")
    if declared != pack_dir.name:
        return [
            Finding(
                "I2",
                pack,
                FAIL,
                f"pack.json 的 id={declared!r} 必须等于目录名 {pack_dir.name!r}。"
                "不一致时「装的是哪个包」有两个答案。",
            )
        ]
    return [Finding("I2", pack, PASS, f"id 与目录名一致（{pack_dir.name}）。")]


def check_i3(pack: str, raw: Any) -> list[Finding]:
    """I3 · 保留名不被误用（B1-5）：`official.*` 只允许 kind=official。"""
    if not isinstance(raw, dict):
        return [Finding("I3", pack, SKIP, "pack.json 根不是对象，无法判 kind。")]
    pack_id = str(raw.get("id", ""))
    kind = raw.get("kind")
    if pack_id.startswith("official.") and kind != "official":
        return [
            Finding(
                "I3",
                pack,
                FAIL,
                f"id 以 official. 开头，但 kind={kind!r}。official.* 只允许官方包使用。",
            )
        ]
    return [Finding("I3", pack, PASS, f"保留名前缀与 kind={kind!r} 一致。")]


def check_i4(pack: str, pack_dir: Path) -> list[Finding]:
    """I4 · 无路径逃逸（B1-6）：包目录里的每个实体路径都必须留在包内。

    ★ 装配会写文件。软链接指到包外，写下去就是越权。
    """
    root = pack_dir.resolve()
    escapes: list[str] = []
    for child in sorted(pack_dir.rglob("*")):
        try:
            resolved = child.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root):
            escapes.append(f"{rel(child)} → {resolved.as_posix()}")
    if escapes:
        return [
            Finding(
                "I4",
                pack,
                FAIL,
                f"{len(escapes)} 个路径越出包目录：{escapes[:3]}。装配会写文件，路径必须留在包内。",
            )
        ]
    return [Finding("I4", pack, PASS, "包内所有实体路径都留在包目录内。")]


def _embedded_plugins(pack_dir: Path) -> dict[str, dict[str, Any]]:
    """包内嵌插件（`plugins/<id>/manifest.json`）→ id → manifest。

    ★ 只在包**显式自包含**时才有；引用式包（默认形态）这里为空，
      此时 I5 的 B2-3 / B2-4 无法静态判定——记 skip，不记 pass。
    """
    out: dict[str, dict[str, Any]] = {}
    root = pack_dir / "plugins"
    if not root.is_dir():
        return out
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest = child / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            payload = _read_json(manifest)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            out[child.name] = payload
    return out


def check_i5(pack: str, manifest: Any, pack_dir: Path) -> list[Finding]:
    """I5 · 预算不放大（B2-3 / B2-4 / B2-5 的静态可判部分）。

    预算是**上限，不是授予**：只能收紧成员的声明，不能扩大。
    """
    budget = getattr(manifest, "permission_budget", None)
    if budget is None:
        return [Finding("I5", pack, SKIP, "未声明 permissionBudget，没有可放大的预算。")]

    findings: list[Finding] = []
    if budget.object_level == "L3":
        findings.append(
            Finding(
                "I5",
                pack,
                FAIL,
                "permissionBudget.objectLevel=L3（内核级）。包不得声明对内核的操作（B2-5）。",
            )
        )

    embedded = _embedded_plugins(pack_dir)
    if not embedded:
        findings.append(
            Finding(
                "I5",
                pack,
                SKIP,
                "成员插件不在包内（引用式包），B2-3「预算 ⊆ 成员 provides 并集」与 "
                "B2-4「objectLevel ≤ 成员最高级」无法静态判定——需要本机插件索引。",
            )
        )
        return findings

    declared: set[str] = set()
    max_level = "L0"
    for member in getattr(manifest, "required", ()):
        plugin = embedded.get(member.id)
        if plugin is None:
            continue
        declared.update(str(c) for c in plugin.get("provides", []) or [])
        level = str(plugin.get("riskLevel", "L2"))
        if RISK_ORDER.get(level, 2) > RISK_ORDER.get(max_level, 0):
            max_level = level

    extra = sorted(set(budget.capabilities) - declared)
    if extra:
        findings.append(
            Finding(
                "I5",
                pack,
                FAIL,
                f"包声明的能力 {extra} 没有任何必需成员声明过（B2-3）。"
                "包的预算是上限，不是授予。",
            )
        )
    if RISK_ORDER.get(budget.object_level, 0) > RISK_ORDER.get(max_level, 0):
        findings.append(
            Finding(
                "I5",
                pack,
                FAIL,
                f"objectLevel={budget.object_level} 高于成员最高风险级 {max_level}（B2-4）。",
            )
        )
    if not findings:
        findings.append(
            Finding("I5", pack, PASS, f"预算未放大（objectLevel={budget.object_level}）。")
        )
    return findings


def _config_owners(pack_dir: Path, raw: Any) -> tuple[dict[str, list[str]], list[str]]:
    """沿 includes 收集「谁给哪个成员提供了 config」。

    返回 `(成员 id → 提供者包 id 列表, 无法解析的 includes)`。

    ★ 只解析**仓库里能找到**的子包。找不到的子包记进第二项，
      由调用方转成 skip——「没找到」不等于「没有冲突」。
    """
    owners: dict[str, list[str]] = {}
    unresolved: list[str] = []
    visiting: set[str] = set()

    def walk(current_dir: Path, current_raw: Any) -> None:
        pack_id = str(current_raw.get("id", current_dir.name))
        if pack_id in visiting:
            return
        visiting.add(pack_id)
        for member_id in (current_raw.get("config") or {}):
            bucket = owners.setdefault(str(member_id), [])
            if pack_id not in bucket:
                bucket.append(pack_id)
        for ref in current_raw.get("includes") or []:
            if not isinstance(ref, dict):
                continue
            child_id = str(ref.get("id", ""))
            child_dir = None
            for root in (PACKS_DATA, PACKS_BUNDLED):
                candidate = root / child_id
                if (candidate / MANIFEST_NAME).is_file():
                    child_dir = candidate
                    break
            if child_dir is None:
                unresolved.append(child_id)
                continue
            try:
                walk(child_dir, _read_json(child_dir / MANIFEST_NAME))
            except (OSError, json.JSONDecodeError):
                unresolved.append(child_id)
        visiting.discard(pack_id)

    walk(pack_dir, raw)
    return owners, unresolved


def check_i6(pack: str, raw: Any, pack_dir: Path) -> list[Finding]:
    """I6 · config 唯一来源（B2-8）：同一成员的配置只能由一个包提供。

    ★ 为什么必须唯一：否则最终配置取决于装载顺序，而装载顺序不是用户能预期或审计的。
    """
    owners, unresolved = _config_owners(pack_dir, raw)
    conflicts = {m: ps for m, ps in owners.items() if len(ps) > 1}
    if conflicts:
        detail = "；".join(f"{m} 被 {ps} 同时提供" for m, ps in sorted(conflicts.items()))
        return [Finding("I6", pack, FAIL, f"成员配置有多个来源（B2-8）：{detail}。")]
    if unresolved:
        return [
            Finding(
                "I6",
                pack,
                SKIP,
                f"includes 里的包在仓库内找不到：{sorted(set(unresolved))}——"
                "无法确认它们的 config 是否与本包冲突。",
            )
        ]
    return [Finding("I6", pack, PASS, f"{len(owners)} 个成员的配置各只有一个来源。")]


def check_i7(pack: str, raw: Any, pack_dir: Path) -> list[Finding]:
    """I7 · 嵌套无环、不超深（B2-7）。"""
    problems: list[str] = []
    unresolved: list[str] = []

    def walk(current_dir: Path, current_raw: Any, depth: int, chain: list[str]) -> None:
        pack_id = str(current_raw.get("id", current_dir.name))
        if pack_id in chain:
            problems.append(f"包依赖成环：{' → '.join(chain + [pack_id])}")
            return
        if depth > MAX_INCLUDE_DEPTH:
            problems.append(f"嵌套深度 {depth} 超过上限 {MAX_INCLUDE_DEPTH}（{pack_id}）")
            return
        for ref in current_raw.get("includes") or []:
            if not isinstance(ref, dict):
                continue
            child_id = str(ref.get("id", ""))
            child_dir = None
            for root in (PACKS_DATA, PACKS_BUNDLED):
                candidate = root / child_id
                if (candidate / MANIFEST_NAME).is_file():
                    child_dir = candidate
                    break
            if child_dir is None:
                unresolved.append(child_id)
                continue
            try:
                walk(child_dir, _read_json(child_dir / MANIFEST_NAME), depth + 1, chain + [pack_id])
            except (OSError, json.JSONDecodeError):
                unresolved.append(child_id)

    walk(pack_dir, raw, 0, [])
    if problems:
        return [Finding("I7", pack, FAIL, "；".join(problems))]
    if unresolved:
        return [
            Finding(
                "I7",
                pack,
                SKIP,
                f"includes 里的包在仓库内找不到：{sorted(set(unresolved))}——"
                "环与深度只能验到仓库内这一段。",
            )
        ]
    return [Finding("I7", pack, PASS, "includes 图无环、未超深。")]


def check_i9(pack: str, raw: Any, pack_dir: Path) -> list[Finding]:
    """I9 · 基线与清单一致：对已提交的 pack.lock.json 重算比对。

    ★ 没有锁不是错——第一次装一个包时本来就没有基线。
      那不是「基线坏了」，是「还没钉过」。
    """
    lock_path = pack_dir / LOCK_NAME
    if not lock_path.is_file():
        return [Finding("I9", pack, SKIP, "没有 pack.lock.json（还没钉过基线，不是错）。")]
    try:
        lock_raw = _read_json(lock_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [
            Finding(
                "I9",
                pack,
                FAIL,
                f"基线锁读不出来：{rel(lock_path)}（{type(exc).__name__}）。"
                "基线锁坏了必须人处理——自动当成「没有基线」会悄悄跳过摘要校验。",
            )
        ]

    findings: list[Finding] = []
    try:
        validate_lock_payload(lock_raw, kind="基线锁")
    except PackError as exc:
        findings.append(Finding("I9", pack, FAIL, f"基线锁形状不合法（{exc.code}）：{exc.message}"))

    if not isinstance(raw, dict):
        findings.append(Finding("I9", pack, SKIP, "pack.json 根不是对象，无法比对。"))
        return findings

    if lock_raw.get("packId") != raw.get("id"):
        findings.append(
            Finding(
                "I9",
                pack,
                FAIL,
                f"锁的 packId={lock_raw.get('packId')!r} 与清单 id={raw.get('id')!r} 不一致。",
            )
        )
    if lock_raw.get("packVersion") != raw.get("version"):
        findings.append(
            Finding(
                "I9",
                pack,
                FAIL,
                f"锁的 packVersion={lock_raw.get('packVersion')!r} 与清单 "
                f"version={raw.get('version')!r} 不一致——清单改了但没重新钉锁。",
            )
        )

    declared: dict[str, str] = {}
    members_raw = raw.get("members") or {}
    if isinstance(members_raw, dict):
        for where in ("required", "optional"):
            for item in members_raw.get(where) or []:
                if isinstance(item, dict) and item.get("id"):
                    declared[str(item["id"])] = str(item.get("version", ""))

    locked: dict[str, str] = {}
    for item in lock_raw.get("members") or []:
        if isinstance(item, dict) and item.get("id"):
            locked[str(item["id"])] = str(item.get("version", ""))

    missing = sorted(set(declared) - set(locked))
    extra = sorted(set(locked) - set(declared))
    if missing:
        findings.append(
            Finding("I9", pack, FAIL, f"清单里有、锁里没有的成员 {missing}——锁过期了。")
        )
    if extra:
        findings.append(
            Finding("I9", pack, FAIL, f"锁里有、清单里没有的成员 {extra}——锁比清单旧。")
        )
    for member_id in sorted(set(declared) & set(locked)):
        if declared[member_id] != locked[member_id]:
            findings.append(
                Finding(
                    "I9",
                    pack,
                    FAIL,
                    f"成员 {member_id} 版本不一致：清单 {declared[member_id]}，锁 {locked[member_id]}。",
                )
            )

    # 包级摘要：重算（排除 pack.lock.json 自身）后与锁里记的比。
    expected_self = lock_raw.get("selfDigest")
    if isinstance(expected_self, str) and expected_self:
        try:
            actual_self = pack_digest(pack_dir)
        except (OSError, FileNotFoundError) as exc:
            findings.append(
                Finding("I9", pack, SKIP, f"包级摘要重算失败（{type(exc).__name__}），无法比对。")
            )
        else:
            if actual_self != expected_self:
                findings.append(
                    Finding(
                        "I9",
                        pack,
                        FAIL,
                        f"包级摘要与基线不符：期望 {expected_self}，实际 {actual_self}。"
                        "包本身（清单、内嵌插件、说明文件）在钉锁之后被改过。",
                    )
                )

    # 成员摘要：只有包内嵌插件时才重算得出来。
    embedded = _embedded_plugins(pack_dir)
    unverified: list[str] = []
    for item in lock_raw.get("members") or []:
        if not isinstance(item, dict):
            continue
        member_id = str(item.get("id", ""))
        expected = item.get("digest")
        if not isinstance(expected, str) or not expected:
            unverified.append(member_id)
            continue
        plugin_dir = pack_dir / "plugins" / member_id
        if not plugin_dir.is_dir():
            unverified.append(member_id)
            continue
        try:
            actual = content_digest(plugin_dir)
        except (OSError, FileNotFoundError):
            unverified.append(member_id)
            continue
        if actual != expected:
            findings.append(
                Finding(
                    "I9",
                    pack,
                    FAIL,
                    f"成员 {member_id} 的内容摘要与基线不符：期望 {expected}，实际 {actual}。",
                )
            )

    if unverified:
        findings.append(
            Finding(
                "I9",
                pack,
                SKIP,
                f"成员摘要未校验：{sorted(set(unverified))}——插件目录不在包内（引用式包），"
                "本机没有可比内容。这是「没能力发现」，不是「一致」。",
            )
        )

    if not any(f.level == FAIL for f in findings):
        findings.append(Finding("I9", pack, PASS, "基线与清单一致。"))
    return findings


def check_i11(pack: str, raw: Any) -> list[Finding]:
    """I11 · 包不声明能力：`pack.json` 里不得出现 provides / defines / requires 等。

    ★ 这是「包不许成为第二个真相源」的机器判据：包只引用与收窄（I11），
      能力声明是插件的事。一条静态扫描就能守住，不需要任何人记得这条纪律。
    """
    if not isinstance(raw, dict):
        return [Finding("I11", pack, SKIP, "pack.json 根不是对象，无法扫能力字段。")]

    hits: list[str] = []
    top = sorted(set(raw) & FORBIDDEN_CAPABILITY_FIELDS)
    if top:
        hits.append(f"顶层 {top}")
    members_raw = raw.get("members") or {}
    if isinstance(members_raw, dict):
        for where in ("required", "optional"):
            for index, item in enumerate(members_raw.get(where) or []):
                if not isinstance(item, dict):
                    continue
                found = sorted(set(item) & FORBIDDEN_CAPABILITY_FIELDS)
                if found:
                    hits.append(f"members.{where}[{index}] {found}")
    if hits:
        return [
            Finding(
                "I11",
                pack,
                FAIL,
                f"包声明了能力字段：{'；'.join(hits)}。包只引用与收窄，不声明能力（I11）。",
            )
        ]
    return [Finding("I11", pack, PASS, "包清单里没有能力声明字段。")]


# ─────────────────────────── 校验器自证（I12） ───────────────────────────

_BASE_MANIFEST: dict[str, Any] = {
    "packFormat": 1,
    "id": "official.demo",
    "name": "演示包",
    "version": "1.0.0",
    "kind": "official",
    "kernelApi": "^1.0",
}


def check_i12() -> list[Finding]:
    """I12 · 报错即文档：每条拒绝都要报出「哪个字段 / 什么值 / 为什么不接受」。

    ★ 这条不依赖仓库里的包——它验的是**校验器自己**。
      判据是「报错文案里同时出现字段名与实际值」，不是「抛了异常就算」：
      只说「包不合法」等于让用户自己去猜哪一行错了。
    """
    findings: list[Finding] = []
    cases: list[tuple[str, Any, tuple[str, ...]]] = []

    broken = dict(_BASE_MANIFEST)
    broken.pop("packFormat")
    cases.append(("B1-1 packFormat 缺失", broken, ("packFormat",)))

    cases.append(("B1-2 未知顶层字段", {**_BASE_MANIFEST, "foo": 1}, ("foo",)))

    cases.append(("B1-3 包 id 形状", {**_BASE_MANIFEST, "id": "finance"}, ("id", "finance")))

    for label, payload, needles in cases:
        try:
            parse_manifest(payload)
        except PackError as exc:
            missing = [n for n in needles if n not in exc.message]
            if missing:
                findings.append(
                    Finding(
                        "I12",
                        "（校验器）",
                        FAIL,
                        f"{label}：报错文案缺少 {missing}——用户无法据此定位。原文：{exc.message}",
                    )
                )
        else:
            findings.append(
                Finding("I12", "（校验器）", FAIL, f"{label}：坏样本没有被拒绝——规则失效了。")
            )

    # 锁的封闭性：多一个字段必须被挡住。
    # ★ 这条同时是「契约不虚构字段」的机器判据：schemaVersion 不在锁里，
    #   写侧就该拒绝它，而不是默默写出去。
    lock_cases: list[tuple[str, dict[str, Any], tuple[str, ...]]] = [
        (
            "LOCK-1 锁的未知字段",
            {
                "packId": "official.demo",
                "packVersion": "1.0.0",
                "generatedAt": "2026-01-01T00:00:00+00:00",
                "schemaVersion": 1,
            },
            ("schemaVersion",),
        ),
        (
            "LOCK-5 摘要格式",
            {
                "packId": "official.demo",
                "packVersion": "1.0.0",
                "generatedAt": "2026-01-01T00:00:00+00:00",
                "selfDigest": "sha256:xyz",
            },
            ("selfDigest", "sha256:xyz"),
        ),
    ]
    for label, payload, needles in lock_cases:
        try:
            validate_lock_payload(payload)
        except PackError as exc:
            missing = [n for n in needles if n not in exc.message]
            if missing:
                findings.append(
                    Finding(
                        "I12",
                        "（校验器）",
                        FAIL,
                        f"{label}：报错文案缺少 {missing}。原文：{exc.message}",
                    )
                )
        else:
            findings.append(
                Finding("I12", "（校验器）", FAIL, f"{label}：坏样本没有被拒绝——规则失效了。")
            )

    if not findings:
        findings.append(
            Finding(
                "I12",
                "（校验器）",
                PASS,
                f"{len(cases) + len(lock_cases)} 条坏样本全部被拒绝，且报错含字段名与实际值。",
            )
        )
    return findings


# ─────────────────────────── 契约交叉验证 ───────────────────────────


def _load_schema(name: str) -> tuple[dict[str, Any] | None, str, str]:
    """读契约文件并做 schema 元校验。返回 `(schema, 说明, level)`。

    ★ 三态而不是两态：`jsonschema` 不在时 schema 仍然读得出来（JSON 合法），
      但**没做元校验**——那既不是通过，也不是失败，是「没验」。
      把它记成 PASS 就是假绿：读的人会以为 draft 2020-12 的合法性已经查过了。
    """
    path = CONTRACTS / name
    if not path.is_file():
        return None, f"✗ {rel(path)} 不存在", FAIL
    try:
        schema = _read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"✗ {rel(path)} 不是合法 JSON（{type(exc).__name__}）", FAIL
    try:
        import jsonschema  # noqa: PLC0415 —— 可选依赖，缺了要如实说
    except ImportError:
        # ★ 返回 None 而不是 schema：schema 读得出来 ≠ 能拿它校验。
        #   返回 schema 会让下游以为「契约可用」，然后在真正校验时崩在 import 上；
        #   返回 None 则让「有真实锁却没有裁判」被明确判成失败（见 check_real_artifacts）。
        return None, f"△ {rel(path)} 是合法 JSON；jsonschema 不可用，无法用它校验", SKIP
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        return None, f"✗ {rel(path)} 不是合法 draft 2020-12 schema：{exc.message}", FAIL
    return schema, f"✓ {rel(path)} 是合法 draft 2020-12 schema", PASS


def _validator(schema: dict[str, Any]) -> Any:
    import jsonschema  # noqa: PLC0415

    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def _validate_file(path: Path, schema: dict[str, Any], label: str) -> list[Finding]:
    """用 schema 校验一个真实文件。"""
    try:
        payload = _read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return [Finding("契约", rel(path), FAIL, f"[{label}] 读不出来（{type(exc).__name__}）。")]
    errors = sorted(_validator(schema).iter_errors(payload), key=lambda e: list(e.path))
    if not errors:
        return [Finding("契约", rel(path), PASS, f"[{label}] 与契约一致。")]
    out: list[Finding] = []
    for err in errors[:5]:
        where = "/".join(str(p) for p in err.absolute_path) or "（根）"
        out.append(Finding("契约", rel(path), FAIL, f"[{label}] {where}: {err.message}"))
    if len(errors) > 5:
        out.append(
            Finding("契约", rel(path), FAIL, f"[{label}] 另有 {len(errors) - 5} 处不符，未逐条列出。")
        )
    return out


def check_write_side_selfcheck(lock_schema: dict[str, Any] | None) -> list[Finding]:
    """写侧自测：用**真实写侧函数**生成一份锁，再拿契约校验它。

    ★ 这不是「仓库样本」——它是「代码写出来的东西，契约认不认」。
      两者在报告里分开写：仓库里没有真实锁时，本项通过**不能**替代
      「真实锁与契约一致」这一条。
    """
    if lock_schema is None:
        return [Finding("契约", "（写侧自测）", SKIP, "锁契约不可用，跳过写侧自测。")]
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        return [
            Finding(
                "契约",
                "（写侧自测）",
                SKIP,
                "jsonschema 不可用，跳过写侧自测（用 services/api/.venv 的解释器可启用）。",
            )
        ]

    with tempfile.TemporaryDirectory(prefix="pack-lock-selfcheck-") as tmp:
        pack_dir = Path(tmp) / "official.demo"
        pack_dir.mkdir(parents=True)
        (pack_dir / MANIFEST_NAME).write_text(
            json.dumps(_BASE_MANIFEST, ensure_ascii=False), encoding="utf-8"
        )
        lock = PackLock(
            pack_id="official.demo",
            pack_version="1.0.0",
            generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            self_digest=pack_digest(pack_dir),
            kernel_api="^1.0",
            members=(),
        )
        try:
            written = write_baseline_lock(pack_dir, lock)
        except PackError as exc:
            return [
                Finding(
                    "契约",
                    "（写侧自测）",
                    FAIL,
                    f"写侧拒绝了自测锁（{exc.code}）：{exc.message}",
                )
            ]
        payload = _read_json(written)
        errors = list(_validator(lock_schema).iter_errors(payload))
        if errors:
            return [
                Finding(
                    "契约",
                    "（写侧自测）",
                    FAIL,
                    f"写侧写出的锁不被契约接受：{errors[0].message}"
                    f"（路径 {'/'.join(str(p) for p in errors[0].absolute_path) or '（根）'}）",
                )
            ]
        return [
            Finding(
                "契约",
                "（写侧自测）",
                PASS,
                "write_baseline_lock 写出的锁被 pack-lock.schema.json 接受"
                "（临时目录生成，非仓库样本）。",
            )
        ]


def check_real_artifacts(
    lock_schema: dict[str, Any] | None,
    assembly_schema: dict[str, Any] | None,
    pack_dirs: list[Path],
) -> tuple[list[Finding], dict[str, Any]]:
    """用契约校验仓库里**真实存在**的锁与快照。不存在就明说跳过。"""
    findings: list[Finding] = []
    locks = [d / LOCK_NAME for d in pack_dirs if (d / LOCK_NAME).is_file()]
    snapshots = discover_snapshots()

    summary: dict[str, Any] = {
        "lock_files": [rel(p) for p in locks],
        "snapshot_files": [rel(p) for p in snapshots],
        "expected_lock": rel(PACKS_BUNDLED / "official.life-tasks" / LOCK_NAME),
        "expected_lock_exists": (PACKS_BUNDLED / "official.life-tasks" / LOCK_NAME).is_file(),
    }

    if not locks and not snapshots:
        findings.append(
            Finding(
                "契约",
                "（真实产物）",
                SKIP,
                "仓库里没有 pack.lock.json，也没有 pack.assembly.json——"
                f"跳过真实产物交叉验证（期望路径 {summary['expected_lock']} 不存在）。"
                "这不是通过，是没有样本。",
            )
        )
        return findings, summary

    if locks and lock_schema is None:
        findings.append(
            Finding("契约", "（真实产物）", FAIL, "有真实锁文件，但锁契约不可用——有东西该验却没验。")
        )
    for path in locks:
        if lock_schema is not None:
            findings.extend(_validate_file(path, lock_schema, "契约·锁"))

    if snapshots and assembly_schema is None:
        findings.append(
            Finding(
                "契约", "（真实产物）", FAIL, "有真实快照文件，但装配契约不可用——有东西该验却没验。"
            )
        )
    for path in snapshots:
        if assembly_schema is not None:
            findings.extend(_validate_file(path, assembly_schema, "契约·快照"))

    return findings, summary


# ─────────────────────────── 主流程 ───────────────────────────


def run(extra_pack_dirs: list[Path] | None = None) -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    pack_dirs = discover_pack_dirs(extra_pack_dirs)

    scan = {
        "roots": [
            {"path": rel(PACKS_BUNDLED), "exists": PACKS_BUNDLED.is_dir()},
            {"path": rel(PACKS_DATA), "exists": PACKS_DATA.is_dir()},
        ],
        "extra_roots": [rel(p) for p in (extra_pack_dirs or [])],
        "pack_count": len(pack_dirs),
        "packs": [rel(p) for p in pack_dirs],
    }

    # ── 不依赖包的部分：I12 校验器自证 ──
    findings.extend(check_i12())

    # ── 逐包 ──
    for pack_dir in pack_dirs:
        pack = rel(pack_dir)
        try:
            raw = _read_json(pack_dir / MANIFEST_NAME)
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(
                Finding("I1", pack, FAIL, f"pack.json 读不出来（{type(exc).__name__}）。")
            )
            continue

        findings.extend(check_i1(pack, raw))
        findings.extend(check_i2(pack, raw, pack_dir))
        findings.extend(check_i3(pack, raw))
        findings.extend(check_i4(pack, pack_dir))
        findings.extend(check_i11(pack, raw))
        findings.extend(check_i6(pack, raw, pack_dir))
        findings.extend(check_i7(pack, raw, pack_dir))
        findings.extend(check_i9(pack, raw, pack_dir))

        try:
            manifest = parse_manifest(raw, directory=pack_dir)
        except PackError as exc:
            findings.append(
                Finding(
                    "I5",
                    pack,
                    SKIP,
                    f"清单未通过 B1 解析（{exc.code}），预算检查无从谈起：{exc.message}",
                )
            )
        else:
            findings.extend(check_i5(pack, manifest, pack_dir))

    # ── 运行期不变量：如实记 skip ──
    for key, why in RUNTIME_ONLY.items():
        findings.append(Finding(key, "（全部）", SKIP, why))

    # ── 契约 ──
    lock_schema, lock_note, lock_level = _load_schema("pack-lock.schema.json")
    assembly_schema, assembly_note, assembly_level = _load_schema("pack-assembly.schema.json")
    findings.append(Finding("契约", "（schema）", lock_level, lock_note))
    findings.append(Finding("契约", "（schema）", assembly_level, assembly_note))
    findings.extend(check_write_side_selfcheck(lock_schema))
    real_findings, artifacts = check_real_artifacts(lock_schema, assembly_schema, pack_dirs)
    findings.extend(real_findings)

    return findings, {"scan": scan, "artifacts": artifacts}


def summarize(findings: list[Finding]) -> dict[str, dict[str, int]]:
    """按不变量汇总 pass / fail / skip 计数。"""
    out: dict[str, dict[str, int]] = {
        key: {PASS: 0, FAIL: 0, SKIP: 0} for key in (*INVARIANTS, "契约")
    }
    for item in findings:
        bucket = out.setdefault(item.invariant, {PASS: 0, FAIL: 0, SKIP: 0})
        bucket[item.level] = bucket.get(item.level, 0) + 1
    return out


def render_text(findings: list[Finding], meta: dict[str, Any], verbose: bool) -> str:
    scan = meta["scan"]
    lines: list[str] = ["包层不变量门禁（协议 §6 · I1~I13）", "扫描根："]

    for root in scan["roots"]:
        path = _REPO / root["path"]
        lines.append(f"  {exists_report(path)}")
    for extra in scan.get("extra_roots", []):
        lines.append(f"  {exists_report(Path(extra))}（--pack-dir 显式追加）")

    lines.append(f"扫到 {scan['pack_count']} 个包。")
    for pack in scan["packs"]:
        lines.append(f"  · {pack}")

    if scan["pack_count"] == 0:
        lines.append("")
        lines.append(
            "无包，跳过：仓库里没有任何 pack.json（packs/ 与 data/packs/ 都不存在或为空）。"
        )
        lines.append(
            "  ★ 这不等于「通过」——是「没有样本可查」。不依赖包的部分（I12 校验器自证、"
            "契约元校验、写侧自测）照常跑了。"
        )

    stats = summarize(findings)
    lines.append("")
    lines.append("不变量：")

    def _mark(s: dict[str, int]) -> str:
        """★ 零样本不能画勾：`pass 0 / fail 0 / skip 0` 是「没查」，
        不是「查过没问题」。门禁最常见的假绿就长这样。"""
        if s[FAIL]:
            return "✗"
        if s[PASS] == 0 and s[SKIP] == 0:
            return "—"
        if s[SKIP] and not s[PASS]:
            return "△"
        return "✓"

    for key, title in INVARIANTS.items():
        s = stats.get(key, {PASS: 0, FAIL: 0, SKIP: 0})
        note = "（无样本）" if s[PASS] == 0 and s[FAIL] == 0 and s[SKIP] == 0 else ""
        lines.append(
            f"  {_mark(s)} {key:<4} {title}：pass {s[PASS]} / fail {s[FAIL]} / skip {s[SKIP]}{note}"
        )
    s = stats.get("契约", {PASS: 0, FAIL: 0, SKIP: 0})
    lines.append(
        f"  {_mark(s)} 契约  锁 / 装配契约与真实产物："
        f"pass {s[PASS]} / fail {s[FAIL]} / skip {s[SKIP]}"
    )

    failures = [f for f in findings if f.level == FAIL]
    skips = [f for f in findings if f.level == SKIP]

    if verbose:
        passes = [f for f in findings if f.level == PASS]
        lines.append("")
        lines.append(f"✓ 通过 {len(passes)} 项：")
        for item in passes:
            lines.append(f"  [{item.invariant}] {item.pack}：{item.detail}")

    if failures:
        lines.append("")
        lines.append(f"✗ 失败 {len(failures)} 项：")
        for item in failures:
            lines.append(f"  [{item.invariant}] {item.pack}：{item.detail}")

    if skips:
        lines.append("")
        lines.append(f"△ 未判定 {len(skips)} 项（不是通过）：")
        for item in skips:
            lines.append(f"  [{item.invariant}] {item.pack}：{item.detail}")

    lines.append("")
    lines.append("结论：" + ("✗ 有违规" if failures else "✓ 无违规"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="包层不变量门禁：逐条校验协议 §6 的 I1~I13（静态可判部分），"
        "并用契约交叉验证真实锁 / 快照。"
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（给 CI / 别的脚本消费）")
    parser.add_argument(
        "--verbose", action="store_true", help="打印每条通过项的明细（未判定项默认就打印）"
    )
    parser.add_argument(
        "--pack-dir",
        action="append",
        default=[],
        metavar="PATH",
        help="额外扫描的包目录（可重复）。作者在自己的包上跑门禁时用，不必先放进仓库。",
    )
    args = parser.parse_args(argv)

    findings, meta = run([Path(p) for p in args.pack_dir])
    failures = [f for f in findings if f.level == FAIL]
    code = 1 if failures else 0

    if args.json:
        stats = summarize(findings)
        payload = {
            "ok": not failures,
            "exit_code": code,
            "scan": meta["scan"],
            "artifacts": meta["artifacts"],
            "invariants": {
                key: {"title": title, **stats.get(key, {})} for key, title in INVARIANTS.items()
            },
            "contract": stats.get("契约", {}),
            "findings": [
                {
                    "invariant": f.invariant,
                    "pack": f.pack,
                    "level": f.level,
                    "detail": f.detail,
                }
                for f in findings
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(findings, meta, args.verbose))

    return code


if __name__ == "__main__":
    raise SystemExit(main())
