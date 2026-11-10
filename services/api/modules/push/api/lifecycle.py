"""push 模块的生命周期钩子：让「事件联动」跟着插件状态一起启停。

★ 为什么这个文件是必需的（2026-11-10，P0）：
  push 的 `manifest.kind` 是 `builtin`，用户可以**停用**它
  （`manager.disable` 只拦 `kind == "core"`）。而三路事件联动是在
  `push_link` 被 import 时一次性注册的，handler 走模块函数、不经过路由 ——
  只摘路由拦不住它：停用后 `calendar.reminder.fired` 一来照样写库发推送。

  ⇒ `manager.disable` / `enable` 会走本文件的固定钩子：
      · on_disable → 摘掉三路订阅（`uninstall_push_link`）
      · on_enable  → 重新按 manifest.consumes 声明订阅（`install_push_link`）

★ 钩子名固定（on_install / on_enable / on_disable / on_uninstall），
  由 `core/plugins/lifecycle.py` 按固定名取；manifest 里的 `lifecycle`
  映射只是描述，不参与取用（见 D4 裁决）。

★ 函数体里用局部 import：`push_link` 导入时会装载订阅，把它放在模块顶层
  import 会让「加载钩子模块」本身产生副作用 —— 那是 P0-2 的根因形态。
"""
from __future__ import annotations

from typing import Any


def on_enable(db: Any = None) -> None:
    """启用 push：按 manifest 声明重新装载三路联动（幂等）。"""
    from modules.push.push_link import install_push_link

    install_push_link()


def on_disable(db: Any = None) -> None:
    """停用 push：摘掉三路联动，停用后不再产生任何推送。"""
    from modules.push.push_link import uninstall_push_link

    uninstall_push_link()


def on_uninstall(db: Any = None) -> None:
    """卸载 push：与停用同样的清理（builtin 实际不可卸载，留作对称）。"""
    from modules.push.push_link import uninstall_push_link

    uninstall_push_link()
