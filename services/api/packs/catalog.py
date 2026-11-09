"""包目录（catalog）—— 「界面上能看见哪些包」的**唯一组装处**。

★ 为什么要有这个文件
-------------------
早先 `GET /packs` 只列**已登记**的包（`packs.wiring.read_registry` 的内容）。
于是发行版自带的官方包——那些躺在项目根 `packs/` 里、**不需要登记就在那儿**
的包——在界面上**根本看不见**。

★ 症状与病因的错位
--------------------
「官方包为什么不见了」的直觉答案是「它没被登记」。
但按协议 §6，官方包是**随发行版提供**的：它不需要被登记，
它**本来就在那儿**，用户要做的是「登记并装配」它。

⇒ 登记表回答「用户装过它」，**不回答「它存在吗」**。
  把两者合成一张表，需要把「可发现的」与「已登记的」并起来。

★ 为什么做成纯函数（而不是在路由里现拼）
--------------------------------------
路由层要连数据库（查成员的启用态），测试成本高；
而「并表」这件事本身是纯逻辑——两张表并成一张。
⇒ 把它抽成包层纯函数，可以**不碰数据库**就单测，
  路由只负责把「成员启用态」这个唯一的外部依赖喂进来。

★ 三个状态的区分
----------------
一行包可能处于三种状态，**它们不是一回事**：

  · **可发现**（discovered）：目录里有 `pack.json` ⇒ 这个包存在
  · **已登记**（registered）：用户登记过它 ⇒ 用户打算装它
  · **已装配**（installed）：成员都在启用态 ⇒ 它现在真的装起来了

★ 为什么要把「可发现」与「已登记」分开：
  一个包可以在「存在但未登记」的状态下被看见——那正是官方包刚发行时的样子。
  只列「已登记」的表 ⇒ 官方包永远要用户先手填路径才能被发现，
  而它明明就在发行版里躺着。

★ 为什么要把「已登记」与「已装配」分开：
  这是协议早就写死的区分（登记是**声明**，装配是**有副作用的动作**）。
  本文件保留它，不是新增语义。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packs.roots import discover_pack_dirs


def _read_manifest_head(pack_dir: Path) -> dict[str, Any]:
    """读 `pack.json` 的头部字段（name/version/description/icon/kind）。

    ★ 读不出来就返回空 dict——**不抛**。
      列表是**展示**用途：一个包清单坏了，不该让整个列表挂掉。
      那个包照样会被列出来（它有 `pack.json`），
      只是名字显示成 id——「它在，但它坏得读不出名字」，
      这个状态本身就该看得见，而不是让用户看到一片空白。
    """
    path = Path(pack_dir) / "pack.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _row(
    pack_id: str,
    *,
    path: Path | None,
    source: str,
    registered: bool,
    installed: bool,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装一行包。字段名与前端 `PackRow` 对齐。"""
    head = manifest or {}
    return {
        "id": pack_id,
        "path": str(path) if path is not None else None,
        "source": source,
        # ★ 三个状态都在，界面上才能说清「它存在、你想装、它装上了吗」。
        "registered": registered,
        "installed": installed,
        "pack_dir_exists": path is not None and Path(path).is_dir(),
        # ★ 展示用字段：读不到就退回 id / 空串，不留 None——
        #   界面上「名字缺失」比「名字是 null」更容易看懂。
        "name": str(head.get("name") or pack_id),
        "version": str(head.get("version") or ""),
        "description": str(head.get("description") or ""),
        "icon": str(head.get("icon") or ""),
        "kind": str(head.get("kind") or ""),
        "tagline": str(head.get("tagline") or ""),
    }


def build_catalog(
    data_dir: Path,
    registry_entries: list[dict[str, Any]],
    *,
    is_installed: Any,
) -> list[dict[str, Any]]:
    """并出「可发现的」与「已登记的」两张表。

    `is_installed(pack_id) -> bool`：由调用方提供——
    查成员启用态要连数据库，那是**路由层**的事；
    本函数只负责并表，不碰数据库。

    ★ 顺序：先列**可发现的**（含发行版），再补**已登记但目录不在的**。
      后者是「半残」状态——登记过、目录被删了。
      它必须出现在列表里（否则用户永远不知道自己的登记里有死条目），
      但要能看出它「目录不在」。
    """
    data_dir = Path(data_dir)

    # ── 已登记的 → id → 登记项（登记表是「用户装过它」的真相）──
    registered_by_id: dict[str, dict[str, Any]] = {}
    for entry in registry_entries:
        pid = entry.get("id")
        if isinstance(pid, str) and pid:
            registered_by_id[pid] = entry

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    # ── 可发现的（本地 + 发行版）──
    for pack_id, pack_dir, source in discover_pack_dirs(data_dir):
        if pack_id in seen:
            continue
        seen.add(pack_id)
        rows.append(
            _row(
                pack_id,
                path=pack_dir,
                source=source,
                registered=pack_id in registered_by_id,
                installed=bool(is_installed(pack_id)),
                manifest=_read_manifest_head(pack_dir),
            )
        )

    # ── 已登记但目录不在的（半残）──
    for pack_id, entry in registered_by_id.items():
        if pack_id in seen:
            continue
        seen.add(pack_id)
        raw_path = entry.get("path")
        rows.append(
            _row(
                pack_id,
                path=Path(raw_path) if isinstance(raw_path, str) and raw_path else None,
                source=str(entry.get("source") or "local"),
                registered=True,
                installed=False,
                manifest=None,
            )
        )

    return rows


__all__ = ["build_catalog"]
