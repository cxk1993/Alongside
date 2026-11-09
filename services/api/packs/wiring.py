"""包层与内核的接线层——把内核能力适配成装配器接口。

★ 为什么要有这一层
-------------------
装配器（`packs/assembler.py`）的设计是**所有内核能力都从外部注入**：
`discover` / `enable` / `disable` / `registry` / `data_dir`。
它自己不认识「插件管理器」——这是包层最重要的边界：
包层只做编排，「怎么装」的知识全在内核。

但总得有一处**知道**内核长什么样——否则没人能把这根管子接上。
那一处就是本文件。

★ 放在包层目录内（`services/api/packs/`），不放 `core/`
------------------------------------------------------
1. 内核代码量预算（`check_kernel_budget.py`）的口径是 `core/**/*.py`——
   接线层放这里**不占内核预算**。这不是为了钻空子，而是因为
   **它确实不是内核**：内核不认识包层，是内核调用包层。
2. `core/layers.py` 把 `app.py` 归为「装配器（不算内核）」，
   判据是「只在启动跑一次，不算内核」——接线层同理。
3. 方向不能反：内核 → 包层。若包层 → 内核，包层就会开始认识内核，
   那个边界一旦破，「包层可替换」就假了。

★ 一句话
---------
内核**调用**包层，不**认识**包层。
本文件是唯一的转换点：把「内核的插件管理器」翻译成「装配器要的四个回调」。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from packs.assembler import PackAssembler
from packs.model import LoadReport, PackError

log = logging.getLogger(__name__)

#: 登记已装配包的清单文件（相对 `data_dir`）。
#:
#: ★ 为什么要有「已装配包登记表」：装配是**有副作用**的动作
#:   （启用了插件、迁移了表、改了路由）。启动时不能无脑重装——
#:   用户可能已经禁用了包里的某个成员，或者已经把整个包卸了。
#:   登记表回答的是「哪些包是**用户装过**的」，与包自身的状态无关。
REGISTRY_FILENAME = "packs/registry.json"


def registry_path(data_dir: Path) -> Path:
    """已装配包登记表的位置。"""
    return Path(data_dir) / REGISTRY_FILENAME


def read_registry(data_dir: Path) -> list[dict[str, Any]]:
    """读已装配包登记表。**读不到就当没有**——不是错误。

    ★ 为什么读不到不算错：登记表是**我们自己**写的，
      它不存在只说明「还没装过任何包」——
      这是**正常状态**，不是故障。把它当错误会让新装的 Alongside 起不来。
    """
    path = registry_path(data_dir)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # 登记表坏了不是「没装过」——谎报会导致重复装配。
        # 所以这里**抛**，让启动流程看见它，而不是静默当成空表。
        raise PackError(
            f"已装配包登记表损坏：{path}（{type(exc).__name__}）。"
            "请手工检查或删除该文件后重启。",
            code="B1-0",
        ) from exc
    packs = raw.get("packs") if isinstance(raw, dict) else None
    if not isinstance(packs, list):
        raise PackError(
            f"已装配包登记表格式不对：{path}（缺少 packs 数组）。",
            code="B1-0",
        )
    return [p for p in packs if isinstance(p, dict)]


def write_registry(data_dir: Path, entries: list[dict[str, Any]]) -> Path:
    """写已装配包登记表（原子写）。"""
    path = registry_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "packs": entries}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def build_assembler(
    *,
    discover: Any,
    enable: Any,
    disable: Any,
    registry: Any,
    data_dir: Path,
) -> PackAssembler:
    """用内核能力组装一个装配器。

    ★ 这一层薄得几乎什么也没做——**这正是对的**。
      装配器要的接口形状恰好与内核的插件管理器对得上
      （`enable(plugin_id, registry)` / `disable(plugin_id, registry)`），
      所以不需要适配器模式、不需要包装类。
      真正有价值的不是这层代码，是**它证明了这两者能接上**。

    ★ 为什么不直接把 `PluginManager` 传给装配器：
      装配器要的是「启用一个插件」这一个动作，
      而 `PluginManager` 是一整个对象（带状态机、审计、台账）。
      传整个对象等于把内核的一大块知识塞进包层的接口里——
      包层就会开始「认识内核的内部结构」，那个边界就破了。
      传两个窄回调，包层只知道「能启、能关」两件事。
    """
    return PackAssembler(
        discover=discover,
        enable=enable,
        disable=disable,
        registry=registry,
        data_dir=Path(data_dir),
    )


def load_registered_packs(
    assembler: PackAssembler,
    *,
    data_dir: Path,
    ask_optional: Any = None,
    dry_run: bool = False,
) -> list[LoadReport]:
    """装配登记表里列出的所有包。

    ★ 逐个装，**一个失败不影响后面的**。
      为什么：包与包之间是独立的（除非显式 `includes`），
      一个包坏了不该让另一个也装不上——那是「一颗老鼠屎坏了一锅汤」。
      失败会进报告，调用方决定要不要把它升级成更严重的事。

    ★ 顺序按登记表里的顺序（登记时是有序的，用户能看懂）。
    """
    reports: list[LoadReport] = []
    for entry in read_registry(data_dir):
        pack_id = entry.get("id")
        path = entry.get("path")
        if not isinstance(pack_id, str) or not isinstance(path, str):
            log.warning("已装配包登记表有条目格式不对，已跳过：%r", entry)
            continue
        try:
            report = assembler.load(
                path,
                ask_optional=ask_optional,
                dry_run=dry_run,
            )
        except PackError as exc:
            # 校验类失败（包本身坏了）——记下来，继续下一个。
            log.error("包 %s 装配失败：%s", pack_id, exc)
            reports.append(
                LoadReport(
                    pack_id=pack_id,
                    status="failed",
                    error=str(exc),
                    dry_run=dry_run,
                )
            )
            continue
        reports.append(report)
        if not report.ok:
            log.error(
                "包 %s 装配未完成：%s",
                pack_id,
                report.error or report.summary(),
            )
    return reports


def register_pack(
    data_dir: Path,
    *,
    pack_id: str,
    path: str,
    **extra: Any,
) -> list[dict[str, Any]]:
    """把一个包登记进「已装配包登记表」。

    ★ 幂等：同一个 `pack_id` 再登记一次是**更新**，不是追加——
      否则重复登记会让启动时装配两遍。
    """
    entries = read_registry(data_dir)
    entry: dict[str, Any] = {"id": pack_id, "path": path, **extra}
    for i, existing in enumerate(entries):
        if existing.get("id") == pack_id:
            entries[i] = entry
            break
    else:
        entries.append(entry)
    write_registry(data_dir, entries)
    return entries


def unregister_pack(data_dir: Path, pack_id: str) -> bool:
    """从登记表里移除一个包。返回是否真的移除了。"""
    entries = read_registry(data_dir)
    kept = [e for e in entries if e.get("id") != pack_id]
    if len(kept) == len(entries):
        return False
    write_registry(data_dir, kept)
    return True
