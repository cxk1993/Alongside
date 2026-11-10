"""lifecycle 模块加载的缓存纪律（P0 回归测试）。

★ 守什么：`run_lifecycle_hook` 反复调用**不允许**每次重新 `exec_module`。

为什么这是 P0：插件在 `api/lifecycle.py` 的**模块级代码**里注册副作用是常态
（典型是 `event_bus.subscribe_declared(...)`）。原实现每次都重新执行模块，
于是这些副作用逐次累积 —— 实测 enable→disable→enable 三次之后，
事件总线里该插件的条目数 1→2→3，一条待办到期事件被投递给 3 个 handler。
用户点五次停用/启用，同一条推送就发五次。

这三个测试在**修复前都是红的**（第一个尤其关键：修复前每次都是新模块对象，
计数器永远是 1，看起来"正常"，所以之前的绿测没能发现它）。
"""
from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

from core.plugins import lifecycle


def _make_plugin(tmp_path, body: str, plugin_id: str = "demo") -> SimpleNamespace:
    """造一个最小插件目录：<tmp>/<id>/api/lifecycle.py。"""
    root = tmp_path / plugin_id
    (root / "api").mkdir(parents=True)
    (root / "api" / "lifecycle.py").write_text(body, encoding="utf-8")
    return SimpleNamespace(id=plugin_id, directory=root)


def _reset_cache() -> None:
    """清缓存。**故意写成 getattr 兜底**：这样同一份测试能在「无缓存的旧实现」
    上跑出真实行为差异（1 vs 3），而不是一上来就 AttributeError —— 后者会掩盖
    「模块被重新 exec」这个真正的缺陷，让测试失去分辨力。
    """
    fn = getattr(lifecycle, "invalidate_lifecycle_cache", None)
    if fn is not None:
        fn()


def test_repeated_hooks_share_one_module_instance(tmp_path) -> None:
    """★ P0 回归：三次钩子调用必须落在**同一个模块对象**上。

    判据用模块级计数器：同一实例 ⇒ 3 次调用累计到 3；
    若每次都重新 exec_module，最后一次的模块只有 1 次记录（这才是 bug 的表现）。
    """
    _reset_cache()
    plugin = _make_plugin(
        tmp_path,
        "calls = []\n\n\ndef on_enable():\n    calls.append(1)\n",
    )

    lifecycle.run_lifecycle_hook(plugin, "enable")
    lifecycle.run_lifecycle_hook(plugin, "enable")
    lifecycle.run_lifecycle_hook(plugin, "enable")

    mod = sys.modules.get("plugin_lifecycle_demo")
    assert mod is not None, "lifecycle 模块应当被加载进 sys.modules"
    assert len(mod.calls) == 3, (
        f"三次钩子调用应共享同一模块实例（calls 累计到 3），实际 {len(mod.calls)} —— "
        "如果是 1，说明每次都在重新 exec_module，模块级副作用会逐次累积"
    )


def test_editing_the_file_still_reloads(tmp_path) -> None:
    """缓存不能把「改了文件不生效」变成新坑：mtime 变了就要重新加载。"""
    _reset_cache()
    plugin = _make_plugin(tmp_path, "VERSION = 1\n")

    lifecycle.run_lifecycle_hook(plugin, "enable")
    assert sys.modules["plugin_lifecycle_demo"].VERSION == 1

    target = plugin.directory / "api" / "lifecycle.py"
    time.sleep(0.01)
    target.write_text("VERSION = 2\n", encoding="utf-8")
    os.utime(target, (time.time() + 1, time.time() + 1))  # 保证 mtime 严格变化

    lifecycle.run_lifecycle_hook(plugin, "enable")
    assert sys.modules["plugin_lifecycle_demo"].VERSION == 2, "改了文件必须重新加载"


def test_invalidate_drops_the_cached_module(tmp_path) -> None:
    """卸载插件时要能把缓存摘掉，否则旧模块的尸体留在内存里。"""
    _reset_cache()
    plugin = _make_plugin(tmp_path, "MARK = 'first'\n")
    lifecycle.run_lifecycle_hook(plugin, "enable")
    assert "plugin_lifecycle_demo" in lifecycle._LIFECYCLE_CACHE

    fn = getattr(lifecycle, "invalidate_lifecycle_cache", None)
    if fn is not None:
        fn("demo")
    assert "plugin_lifecycle_demo" not in lifecycle._LIFECYCLE_CACHE

    # 再加载会拿到新模块对象（内容相同也算重新执行过）
    first = sys.modules["plugin_lifecycle_demo"]
    lifecycle.run_lifecycle_hook(plugin, "enable")
    assert sys.modules["plugin_lifecycle_demo"] is not first


def test_missing_lifecycle_file_is_not_cached(tmp_path) -> None:
    """没有 api/lifecycle.py 的插件不该在缓存里留空条目。"""
    _reset_cache()
    root = tmp_path / "bare"
    root.mkdir()
    plugin = SimpleNamespace(id="bare", directory=root)

    assert lifecycle.run_lifecycle_hook(plugin, "enable") is None
    assert "plugin_lifecycle_bare" not in lifecycle._LIFECYCLE_CACHE
