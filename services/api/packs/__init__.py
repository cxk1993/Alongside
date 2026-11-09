"""包层（Pack 层）。

对外只暴露装配器、清单解析、校验、存储与作者命令面。内部实现按职责拆开，便于单测。

★ 边界：包层不含「怎么装」—— enable / disable 由内核提供，装配器只做编排。

★ 两条「锁」的区别（这是本层最容易搞混的一处）：
  · `PackLock` —— **基线锁**（`pack.lock.json`，包目录）：
    「我装的是什么」。由显式命令 `pack lock` 写，装载**只读**。
  · `AssemblySnapshot` —— **装配快照**（`pack.assembly.json`，数据目录）：
    「上次装成了什么」。由装载流程写。
  详见 `packs/state.py` 的模块头。
"""

from __future__ import annotations

from packs.assembler import PackAssembler, PackBusyError
from packs.author import (
    CheckResult,
    DiffResult,
    check_pack,
    diff_pack,
    format_check,
    format_diff,
    lock_pack,
)
from packs.digest import content_digest
from packs.model import (
    PACK_FORMAT,
    AdjustmentAction,
    AssemblySnapshot,
    LoadReport,
    LoadStatus,
    LockedMember,
    MemberRef,
    PackError,
    PackLock,
    PackManifest,
    PackState,
    UserAdjustment,
)
from packs.state import (
    PackLockBroken,
    PackStore,
    baseline_lock_path,
    read_baseline_lock,
    write_baseline_lock,
)
from packs.validate import (
    expand_includes,
    parse_manifest,
    validate_consistency,
    validate_structure,
)

__all__ = [
    "PACK_FORMAT",
    "AdjustmentAction",
    "AssemblySnapshot",
    "CheckResult",
    "DiffResult",
    "LoadReport",
    "LoadStatus",
    "LockedMember",
    "MemberRef",
    "PackAssembler",
    "PackBusyError",
    "PackError",
    "PackLock",
    "PackLockBroken",
    "PackManifest",
    "PackState",
    "PackStore",
    "UserAdjustment",
    "baseline_lock_path",
    "check_pack",
    "content_digest",
    "diff_pack",
    "expand_includes",
    "format_check",
    "format_diff",
    "lock_pack",
    "parse_manifest",
    "read_baseline_lock",
    "validate_consistency",
    "validate_structure",
    "write_baseline_lock",
]
