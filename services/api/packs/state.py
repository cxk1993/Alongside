"""包层存储 —— 三样留档，各管一件事。

| 文件 | 住在哪 | 是什么 | 谁写 |
|---|---|---|---|
| `pack.json` | 包目录 | **作者当初声明了什么**（原件） | 包作者 |
| `pack.lock.json` | 包目录 | **基线锁**：「我装的是什么」 | 显式命令 `pack lock` |
| `pack.assembly.json` | 数据目录 | **装配快照**：「上次装成了什么」 | 装载流程（成功后） |
| `state.json` | 数据目录 | **用户改了什么**（本地调整层） | **只由用户动作写** |

★ 最重要的一条纪律：**`save_state` 只允许由用户动作触发**。
  装配器读 `state.json`，但永不写它——写一次，用户的调整就被装载覆盖，
  §21.4 规则一当场失效，而且没人会立刻发现。

★ 为什么基线锁与装配快照必须分成两个文件
------------------------------------------------
协议 §15.2 规则 2 说「锁默认只读，只有显式命令才写」，
§19.2 步骤 10 说「全部成功 → 写锁」。这两条**字面矛盾**。

根因不是笔误，是**两个不同的东西共用了「锁」这个名**：

- **基线锁** = 「我装的是哪个版本、哪个内容」——**作者声明的期望**。
  它必须**不被装载改写**，否则 §15.3 的「摘要不符 ⇒ 硬拒绝」永远不会触发：
  每次装载都把它写成当前状态，基线就永远等于现状。
  ⇒ 它由显式命令写（§15.2 规则 2、§25.4 的 `alongside pack lock`），
    住在**包目录**里，跟着包分发，别人能拿来对账。

- **装配快照** = 「这台机器上次装成了什么」——**运行时的结果**。
  它必须**每次装载都重写**，否则第二次装载会拿第一次的结果当基线，
  差量装配（§19.2 步骤 3「复用」）就没有依据。

⇒ 合成一个文件，两条规则里必有一条要牺牲；分成两个，两条都成立。
  本实现据此把「锁」拆成 `pack.lock.json`（基线）与 `pack.assembly.json`（快照）。
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packs.model import (
    AdjustmentAction,
    AssemblySnapshot,
    LockedMember,
    PackLock,
    PackState,
    UserAdjustment,
)
from packs.validate import validate_lock_payload

ORIGINAL_NAME = "pack.json"
LOCK_NAME = "pack.lock.json"
SNAPSHOT_NAME = "pack.assembly.json"
STATE_NAME = "state.json"
CONFIG_APPLIED_NAME = "config.applied.json"


class PackLockBroken(Exception):
    """基线锁损坏。★ 单独一个类型，因为它需要的是**人**处理，不是重试。"""


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: Any) -> None:
    """原子写：先写临时文件再 replace，避免半截 JSON 留在盘上。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _members_from_payload(payload: Any) -> tuple[LockedMember, ...]:
    """从 JSON 里读出成员列表。摘要允许缺失（那是「没算出来」，不是「空」）。"""
    out: list[LockedMember] = []
    raw_members = payload.get("members", []) if isinstance(payload, dict) else []
    if not isinstance(raw_members, list):
        return ()
    for m in raw_members:
        if not isinstance(m, dict):
            continue
        if not {"id", "version"} <= set(m):
            continue
        digest = m.get("digest")
        from_packs = m.get("fromPacks")
        out.append(
            LockedMember(
                id=str(m["id"]),
                version=str(m["version"]),
                digest=str(digest) if isinstance(digest, str) and digest else None,
                from_packs=(
                    tuple(str(p) for p in from_packs)
                    if isinstance(from_packs, list)
                    else ()
                ),
            )
        )
    return tuple(out)


def _self_digest_from_payload(payload: Any) -> str | None:
    """读包级摘要。**兼容旧字段名** `packDigest`。

    ★ 为什么留兼容：早期实现写的是 `packDigest`，而规范（§13 / §15.1）叫 `selfDigest`。
      规范与代码分叉时**以规范为准**（`selfDigest` 是对外的字段名），
      但不能因此让已经生成的旧锁文件读不出来——
      读不出来 ⇒ 被当成「基线坏了」⇒ 硬拒绝 ⇒ 用户白白装不上包。
      ⇒ 读时两个名字都认，写时只写规范名。
    """
    if not isinstance(payload, dict):
        return None
    for key in ("selfDigest", "packDigest"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _member_payload(member: LockedMember) -> dict[str, Any]:
    """成员记录的 JSON 形状。**基线锁与装配快照共用**。

    ★ 为什么共用一个函数：两个文件是**同构**的（§15.1「形状与基线锁同构」），
      各自写一份序列化逻辑，早晚会长歪——那时两个文件的字段悄悄不一样，
      读的人要在两个文件之间做心算对照。
      ⇒ 形状只有一处定义。
    """
    return {
        "id": member.id,
        "version": member.version,
        **({"digest": member.digest} if member.digest else {}),
        **({"fromPacks": list(member.from_packs)} if member.from_packs else {}),
    }


class PackStore:
    """一个包在**数据目录**里的留档（不碰包目录本身）。

    ★ 住址分工：
      · 住**数据目录**的：原件副本、装配快照、用户调整层
        ——它们描述「这台机器上发生过什么」，跟着机器走。
      · 住**包目录**的：基线锁（`read_baseline_lock` / `write_baseline_lock`）
        ——它描述「我装的是什么」，跟着包走。
    """

    def __init__(self, data_dir: Path, pack_id: str) -> None:
        self.pack_id = pack_id
        self.root = Path(data_dir) / "packs" / pack_id

    # ── 原件 ──

    @property
    def original_path(self) -> Path:
        return self.root / ORIGINAL_NAME

    def archive_original(self, raw: dict[str, Any]) -> Path:
        """存档 `pack.json` 原件。**只增不改**。

        已存在且内容不同，说明同一个 pack id 换了版本——
        保留为 `pack.json.<version>`，不覆盖旧的，否则历史的「作者声明」就丢了。
        """
        version = str(raw.get("version", "unknown"))
        target = self.original_path
        if target.exists():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = None
            if existing != raw:
                target = self.root / f"pack.json.{version}"
        if not target.exists():
            _atomic_write_json(target, raw)
        return target

    # ── 装配快照（数据目录；装载流程写） ──

    @property
    def snapshot_path(self) -> Path:
        return self.root / SNAPSHOT_NAME

    def load_snapshot(self) -> AssemblySnapshot | None:
        if not self.snapshot_path.exists():
            return None
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return AssemblySnapshot(
            pack_id=str(payload.get("packId", self.pack_id)),
            pack_version=str(payload.get("packVersion", "")),
            generated_at=str(payload.get("generatedAt", "")),
            members=_members_from_payload(payload),
            self_digest=_self_digest_from_payload(payload),
            kernel_api=str(payload["kernelApi"]) if payload.get("kernelApi") else None,
        )

    def save_snapshot(self, snapshot: AssemblySnapshot) -> Path:
        """写装配快照。**只有装载流程成功后调它**（§15.2 规则 1）。

        ★ 写之前同样过形状校验：快照是**差量装配的依据**，
          坏形状的快照会让下次装载拿一个错误的基线去做「复用」判断。
        """
        payload = {
            "packId": snapshot.pack_id,
            "packVersion": snapshot.pack_version,
            "generatedAt": snapshot.generated_at,
            **({"selfDigest": snapshot.self_digest} if snapshot.self_digest else {}),
            **({"kernelApi": snapshot.kernel_api} if snapshot.kernel_api else {}),
            "members": [_member_payload(m) for m in snapshot.members],
        }
        validate_lock_payload(payload, kind="装配快照")
        _atomic_write_json(self.snapshot_path, payload)
        return self.snapshot_path

    # ── 用户调整 ──

    @property
    def state_path(self) -> Path:
        return self.root / STATE_NAME

    # ── 包写过的配置（数据目录；装载流程写） ──

    @property
    def config_applied_path(self) -> Path:
        """「这个包写过哪些设置项」的记录。

        ★ 为什么需要它：`plugin_setting` 表**不记来源**——
          包写的值与用户写的值混成一份。
          没有这份记录，就答不出「这一项现在是包的还是用户的」，
          而协议 §16.1 的层序（用户覆盖层在包层之上）就落不下去。

        ★ 住**数据目录**而不是包目录：它描述「这台机器上发生过什么」，
          跟着机器走（同 `state.json` / `pack.assembly.json` 的归属）。
        """
        return self.root / CONFIG_APPLIED_NAME

    def load_config_applied(self) -> dict[str, dict[str, Any]]:
        """读「我写过什么」。读不到就当没写过（不是错误）。"""
        path = self.config_applied_path
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for member_id, values in payload.get("applied", {}).items():
            if isinstance(member_id, str) and isinstance(values, dict):
                out[member_id] = dict(values)
        return out

    def save_config_applied(self, applied: dict[str, dict[str, Any]]) -> Path:
        """写「我写过什么」。**只写包自己写的那些**。"""
        _atomic_write_json(
            self.config_applied_path,
            {"packId": self.pack_id, "applied": applied},
        )
        return self.config_applied_path

    def load_state(self) -> PackState:
        if not self.state_path.exists():
            return PackState(pack_id=self.pack_id)
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return PackState(pack_id=self.pack_id)
        adjustments: list[UserAdjustment] = []
        for item in payload.get("adjustments", []):
            if not isinstance(item, dict):
                continue
            try:
                action = AdjustmentAction(str(item.get("action")))
            except ValueError:
                continue
            adjustments.append(
                UserAdjustment(
                    member_id=str(item.get("memberId", "")),
                    action=action,
                    by=str(item.get("by", "user")),
                    at=str(item.get("at", "")),
                )
            )
        return PackState(pack_id=self.pack_id, adjustments=adjustments)

    def record_adjustment(
        self, member_id: str, action: AdjustmentAction, *, by: str = "user"
    ) -> PackState:
        """**这是唯一允许写 `state.json` 的入口，且只能由用户动作调用。**

        装载流程绝不调用它 —— 装载只 `load_state()`（只读）。
        """
        state = self.load_state()
        state.adjustments.append(
            UserAdjustment(member_id=member_id, action=action, by=by, at=_now())
        )
        _atomic_write_json(
            self.state_path,
            {
                "packId": state.pack_id,
                "adjustments": [
                    {
                        "memberId": a.member_id,
                        "action": a.action.value,
                        "by": a.by,
                        "at": a.at,
                    }
                    for a in state.adjustments
                ],
            },
        )
        return state


# ─────────────────────── 基线锁（包目录；显式命令写） ───────────────────────


def baseline_lock_path(pack_dir: Path) -> Path:
    """基线锁在**包目录**里的位置。

    ★ 为什么住包目录而不是数据目录：
      它是**作者声明的期望**，要跟着包一起分发。
      §25.4 要求「三个命令都能只读地跑在任意目录上（不要求系统已装载该包）」
      ——若锁住在数据目录，别的机器上根本没有它，基线就无从谈起。
    """
    return Path(pack_dir) / LOCK_NAME


def read_baseline_lock(pack_dir: Path) -> PackLock | None:
    """读基线锁。**读不到就当没有**——不是错误。

    ★ 为什么读不到不算错：第一次装一个包时本来就没有基线。
      那不是「基线坏了」，那是「还没钉过」。
      真正的坏（JSON 坏了）要报错——谎报会导致静默跳过校验。
    """
    path = baseline_lock_path(pack_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackLockBroken(
            f"基线锁读不出来：{path}（{type(exc).__name__}）。"
            "基线锁坏了必须人处理——自动当成「没有基线」会悄悄跳过摘要校验。"
        ) from exc
    if not isinstance(payload, dict):
        raise PackLockBroken(f"基线锁格式不对：{path}（顶层不是对象）。")
    return PackLock(
        pack_id=str(payload.get("packId", "")),
        pack_version=str(payload.get("packVersion", "")),
        generated_at=str(payload.get("generatedAt", "")),
        members=_members_from_payload(payload),
        self_digest=_self_digest_from_payload(payload),
        kernel_api=str(payload["kernelApi"]) if payload.get("kernelApi") else None,
    )


def write_baseline_lock(pack_dir: Path, lock: PackLock) -> Path:
    """写基线锁。**只有显式命令能调它**（§15.2 规则 2）。

    装载流程绝不调用它 —— 装载只读基线。

    ★ **写之前必须过形状校验**（`validate_lock_payload`）：
      写出去的锁要跟着包分发给别人，坏形状的代价由**用户**承担
      （摘要写错 ⇒ 别人每次装载都判「不符」⇒ 永远装不上）。
      ⇒ 自己写的东西自己把关，不让下游替我们承担。
    """
    payload = {
        "packId": lock.pack_id,
        "packVersion": lock.pack_version,
        "generatedAt": lock.generated_at,
        **({"selfDigest": lock.self_digest} if lock.self_digest else {}),
        **({"kernelApi": lock.kernel_api} if lock.kernel_api else {}),
        "members": [_member_payload(m) for m in lock.members],
    }
    validate_lock_payload(payload, kind="基线锁")
    path = baseline_lock_path(pack_dir)
    _atomic_write_json(path, payload)
    return path
