"""包作者命令面（协议 §25.4）。

三个命令：

| 命令 | 作用 | 谁写文件 |
|---|---|---|
| `pack check <dir>` | 跑 B1 + B2 校验，输出人话报告 | **不写** |
| `pack lock <dir>` | 生成 / 更新 `pack.lock.json`（**基线锁**） | **写**（唯一写入口） |
| `pack diff <dir>` | 对比清单与基线，列出「将会新增 / 移除 / 升级什么」 | **不写** |

★ 三条纪律
------------
1. **三个命令都必须能只读地跑在任意目录上**（不要求系统已装载该包）——
   否则包作者没法在自己的机器上验证（§25.4 的原话）。
   ⇒ `check` 与 `diff` **一个字节都不写**；`lock` 只写基线锁一个文件。

2. **基线锁的写入口只有 `lock` 这一个**（§15.2 规则 2：
   「谁在什么时候改了基线」必须有唯一答案）。
   ⇒ 装配流程**只读**基线锁，从不写它（见 `packs/assembler.py`）。

3. **命令不认识内核**。
   它只做三件事：读 `pack.json`、算摘要、比对。
   找插件目录这件事由**注入的发现器**完成——装配器与命令面共用同一个
   `discover` 回调形状，所以两边对「插件在哪」的口径必然一致。
   ⇒ 若命令自己去扫 `plugins/` 目录，它就会与内核的发现规则分叉
     （内置模块 vs 第三方插件的判定、校验失败的插件算不算在内……）。

★ 为什么 `lock` 写的是**基线锁**而不是装配快照
---------------------------------------------
两者角色不同（详见 `packs/state.py` 的模块头）：

- **基线锁**（`pack.lock.json`，**包目录**）= 「我装的是什么」
  → 作者声明的期望，跟着包分发，别人能拿来对账。
- **装配快照**（`pack.assembly.json`，**数据目录**）= 「上次装成了什么」
  → 某台机器的运行时结果，由装载流程写。

`lock` 命令写前者，因为它服务于**作者**：作者说「我就是要装这个内容」，
这是一条**声明**，该跟着包走。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packs.digest import content_digest, pack_digest
from packs.model import LockedMember, PackError, PackLock, PackManifest
from packs.state import baseline_lock_path, read_baseline_lock, write_baseline_lock
from packs.validate import parse_manifest, validate_consistency, validate_structure


@dataclass(frozen=True, slots=True)
class CheckResult:
    """`pack check` 的结论。"""

    pack_id: str
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiffResult:
    """`pack diff` 的结论：**将要**发生什么。"""

    pack_id: str
    added: tuple[str, ...] = ()
    """基线里没有、清单里有的成员（新增）。"""
    removed: tuple[str, ...] = ()
    """基线里有、清单里没有的成员（移除）。"""
    upgraded: tuple[str, ...] = ()
    """版本或摘要都变了（升级）。"""
    unchanged: tuple[str, ...] = ()
    """完全没变。"""
    digest_changed: tuple[str, ...] = ()
    """版本没变但**内容变了**——最值得注意的一类。"""


def _plugin_index(
    discover: Callable[[], Any] | None,
) -> dict[str, dict[str, Any]]:
    """插件 id → 插件信息（manifest + 目录）。

    ★ 没有发现器时返回空表：命令面**不自己扫目录**（见模块头的纪律 3）。
      此时摘要算不出来，成员会被记进「摘要未校验」。
    """
    if discover is None:
        return {}
    found = discover()
    index: dict[str, dict[str, Any]] = {}
    for info in getattr(found, "plugins", []):
        manifest = getattr(info, "manifest", None)
        if isinstance(manifest, Mapping):
            entry = dict(manifest)
            directory = getattr(info, "directory", None)
            if directory is not None:
                entry["_directory"] = str(directory)
            index[str(getattr(info, "id", ""))] = entry
    return index


def _actual_digest(plugin: Mapping[str, Any]) -> str | None:
    """算插件的**实际内容摘要**。算不出来返回 `None`（不是全零占位）。"""
    declared = plugin.get("digest")
    if isinstance(declared, str) and declared.startswith("sha256:"):
        return declared
    directory = plugin.get("_directory")
    if not directory:
        return None
    try:
        return content_digest(Path(str(directory)))
    except (OSError, FileNotFoundError):
        return None


def _pack_digest(pack_dir: Path) -> str | None:
    """算**包目录**的内容摘要。算不出来返回 `None`。

    ★ 为什么算不出来也要留位置：包目录读不到（权限、断链）是**待办**，
      不是「摘要为空」。写 null 意味着「这一次没算出来」，
      下次对账时它不会被当成「一致」——而写成空串或全零会让
      「一致」这个判断恒真，等于把这个字段架空。
    """
    try:
        return pack_digest(pack_dir)
    except (OSError, FileNotFoundError):
        return None


def read_pack(pack_dir: Path) -> PackManifest:
    """读并解析 `pack.json`。"""
    path = Path(pack_dir) / "pack.json"
    if not path.is_file():
        raise PackError(f"找不到包清单：{path}", code="B1-0")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackError(f"pack.json 不是合法 JSON：{exc}", code="B1-3") from exc
    return parse_manifest(raw, directory=Path(pack_dir))


def check_pack(
    pack_dir: Path,
    *,
    discover: Callable[[], Any] | None = None,
) -> CheckResult:
    """`pack check` —— 跑 B1 + B2 校验，输出人话报告。**不写任何文件。**

    ★ 为什么要独立于装配：装配前有**副作用**（启用插件、迁移表、改路由），
      而作者在提交前只想知道「这个包写对了吗」。
      ⇒ 校验必须是**只读**的，且能在没装这个包的机器上跑（§25.4）。
    """
    pack_dir = Path(pack_dir)
    errors: list[str] = []
    warnings: list[str] = []

    try:
        manifest = read_pack(pack_dir)
    except PackError as exc:
        return CheckResult(pack_id=pack_dir.name, ok=False, errors=(exc.message,))

    try:
        validate_structure(manifest, dirname=pack_dir.name)
    except PackError as exc:
        errors.append(exc.message)

    index = _plugin_index(discover)
    if index:
        try:
            validate_consistency(manifest, index)
        except PackError as exc:
            errors.append(exc.message)

    # 摘要能算出来吗？算不出来是**警告**不是错误——
    # 第一次写包时插件可能还没装到本机。
    for member in manifest.all_members:
        plugin = index.get(member.id)
        if plugin is None:
            warnings.append(f"成员 {member.id} 不在本机（未安装？）——摘要未校验。")
            continue
        if _actual_digest(plugin) is None:
            warnings.append(f"成员 {member.id} 的摘要算不出来（目录不可达？）——摘要未校验。")

    return CheckResult(
        pack_id=manifest.id,
        ok=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def lock_pack(
    pack_dir: Path,
    *,
    discover: Callable[[], Any] | None = None,
    now: str = "",
    kernel_api: str | None = None,
) -> PackLock:
    """`pack lock` —— 生成 / 更新**基线锁**。**基线锁的唯一写入口**（§15.2 规则 2）。

    ★ 写的是什么：清单里每个成员的**期望版本** + **实际内容摘要**，
      外加**包级摘要**（这个包本身是什么）与**归属集合**（谁带进来的）。
      摘要取实际值而不是清单里的值——因为清单里的摘要是「人钉死的」，
      而基线锁记的是「现在这份内容就是这些」。

    ★ 写的是**基线锁**（包目录），不是装配快照（数据目录）：
      基线是**声明**，要跟着包分发；快照是**结果**，各机不同。

    ★ 摘要算不出来的成员：写 `digest: null`——
      不是全零占位（全零会让「摘要一致」恒真，把机制架空）。

    ★ `from_packs` 记的是**归属**（§10.2 第 4 条：归属是集合）。
      一个成员可能被多个包引入——锁里要记全，否则无法回答
      「这个成员是谁带进来的」。
    """
    pack_dir = Path(pack_dir)
    manifest = read_pack(pack_dir)
    validate_structure(manifest, dirname=pack_dir.name)

    index = _plugin_index(discover)
    if index:
        validate_consistency(manifest, index)

    members: list[LockedMember] = []
    for member in manifest.all_members:
        plugin = index.get(member.id)
        digest = _actual_digest(plugin) if plugin else None
        members.append(
            LockedMember(
                id=member.id,
                version=member.version,
                digest=digest,
                from_packs=(manifest.id,),
            )
        )

    lock = PackLock(
        pack_id=manifest.id,
        pack_version=manifest.version,
        generated_at=now,
        members=tuple(members),
        self_digest=_pack_digest(pack_dir),
        kernel_api=kernel_api,
    )
    write_baseline_lock(pack_dir, lock)
    return lock


def diff_pack(
    pack_dir: Path,
    *,
    discover: Callable[[], Any] | None = None,
) -> DiffResult:
    """`pack diff` —— 对比清单与基线，列出「将会新增 / 移除 / 升级什么」。**不写任何文件。**

    ★ 它回答的是「装下去会发生什么」，所以是**预览**，不是操作。
      与 `lock` 的分工：`lock` 改基线，`diff` 只看。
    """
    pack_dir = Path(pack_dir)
    manifest = read_pack(pack_dir)
    index = _plugin_index(discover)

    baseline = read_baseline_lock(pack_dir)
    baseline_members = baseline.by_id() if baseline else {}

    added: list[str] = []
    removed: list[str] = []
    upgraded: list[str] = []
    unchanged: list[str] = []
    digest_changed: list[str] = []

    for member in manifest.all_members:
        actual = _actual_digest(index.get(member.id, {})) if member.id in index else None
        prior = baseline_members.get(member.id)
        if prior is None:
            added.append(member.id)
            continue
        if prior.version != member.version:
            upgraded.append(member.id)
            continue
        if prior.digest and actual and prior.digest != actual:
            digest_changed.append(member.id)
            continue
        unchanged.append(member.id)

    for member_id in baseline_members:
        if member_id not in {m.id for m in manifest.all_members}:
            removed.append(member_id)

    return DiffResult(
        pack_id=manifest.id,
        added=tuple(added),
        removed=tuple(removed),
        upgraded=tuple(upgraded),
        unchanged=tuple(unchanged),
        digest_changed=tuple(digest_changed),
    )


def format_check(result: CheckResult) -> str:
    """把校验结论变成人话（§25.4「输出人话报告」）。"""
    lines = [f"包 {result.pack_id}：{'通过' if result.ok else '不通过'}"]
    for err in result.errors:
        lines.append(f"  ✗ {err}")
    for warn in result.warnings:
        lines.append(f"  ! {warn}")
    if result.ok and not result.warnings:
        lines.append("  ✓ 没有发现问题")
    return "\n".join(lines)


def format_diff(result: DiffResult) -> str:
    """把差异结论变成人话。"""
    lines = [f"包 {result.pack_id} 的差异："]
    for label, ids in (
        ("新增", result.added),
        ("移除", result.removed),
        ("升级", result.upgraded),
        ("内容变了（版本未变）", result.digest_changed),
        ("未变", result.unchanged),
    ):
        if ids:
            lines.append(f"  {label}：{'、'.join(ids)}")
    if not any(
        (result.added, result.removed, result.upgraded, result.digest_changed, result.unchanged)
    ):
        lines.append("  （清单是空的）")
    return "\n".join(lines)


__all__ = [
    "CheckResult",
    "DiffResult",
    "baseline_lock_path",
    "check_pack",
    "diff_pack",
    "format_check",
    "format_diff",
    "lock_pack",
    "read_pack",
]
