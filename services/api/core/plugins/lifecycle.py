"""生命周期钩子（总纲 §1.3 / 验收 #5）。

插件在 api/lifecycle.py 里可定义四个可选钩子（**名字固定，不可自定义**）：
    def on_install(db: Session) -> None
    def on_enable(db: Session) -> None
    def on_disable(db: Session) -> None
    def on_uninstall(db: Session) -> None

## 声明是契约（2026-11-10 裁决 b）

manifest 的 `lifecycle` 字段从「钩子名映射对象」**降级为枚举**：
`"hooks"` = 本插件提供固定名钩子；`null`/缺省 = 不提供。

为什么降级（而不是让内核去读那份映射、支持自定义名）：
  · 全仓唯一写过映射的 pi-agent，三个值恰好等于固定名 —— 零信息；
  · 自定义名唯一的作用是让作者换名字，代价是「写错名 = 静默不执行」（fail-open），
    与 emits/consumes 的 fail-closed 精神相反；
  · 一个字段如果允许配置却没人需要配置，它就是负担，不是能力。
  ⇒ 名字固定，声明只回答「有没有」，内核用它做**一致性校验**。

## 软失败语义（不变）

钩子内部抛异常 → 被 manager 捕获并写进 plugin_state.last_error，
不许拖垮整个安装/卸载流程。

★ 声明与实现不一致（声明了 hooks 却没文件 / 有钩子却没声明）也走**同一个通道**：
  写进 last_error 供 readyz 目击，但不拦启停 —— 因为钩子本来就是尽力而为的，
  为一句声明把插件挡在门外，代价比收益大。**看得见**比**拦得住**更适合这一层。
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from typing import Any

_STAGES = ("on_install", "on_enable", "on_disable", "on_uninstall")

_STAGE_ATTR = {
    "install": "on_install",
    "enable": "on_enable",
    "disable": "on_disable",
    "uninstall": "on_uninstall",
}

#: 已加载的 lifecycle 模块缓存：`{模块名: (mtime, 模块)}`。
#:
#: ★ 为什么必须缓存（实测 P0）：`_load_lifecycle_module` 原来每次调用都重新
#:   `exec_module`，而模块级代码里的副作用（典型是插件在 import 期调用
#:   `event_bus.subscribe_declared(...)`）会**逐次累积**。
#:   实测 enable→disable→enable 三次之后，事件总线里该插件的条目数 1→2→3，
#:   一条待办到期事件被投递给 3 个 handler —— 用户点五次停用/启用，
#:   同一条推送就会发五次。而且插件自己的计数器在新模块里，它看不见自己被调了三次。
#:
#: 按 mtime 失效：开发时改了 lifecycle.py 仍然会重新加载，不会「改了不生效」。
_LIFECYCLE_CACHE: dict[str, tuple[float, Any]] = {}


def invalidate_lifecycle_cache(plugin_id: str | None = None) -> None:
    """清 lifecycle 模块缓存。插件卸载时调用，避免旧模块的尸体留在内存里。

    `plugin_id=None` 表示全清（测试用）。
    """
    if plugin_id is None:
        _LIFECYCLE_CACHE.clear()
        return
    _LIFECYCLE_CACHE.pop(f"plugin_lifecycle_{plugin_id}", None)


def _load_lifecycle_module(info: Any) -> Any | None:
    path = info.directory / "api" / "lifecycle.py"
    if not path.is_file():
        return None
    name = f"plugin_lifecycle_{info.id}"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    hit = _LIFECYCLE_CACHE.get(name)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001
        return None
    _LIFECYCLE_CACHE[name] = (mtime, mod)
    return mod


def _declared(info: Any) -> str | None:
    """读 manifest 的 `lifecycle` 声明（只认 `"hooks"` 与 `None`）。

    ★ 三种 info 形态（PluginInfo / Manifest 模型 / 原始 dict）都支持 ——
      与 `event_bus._declaration_of` 同口径，内核不为此认识插件目录结构。
    """
    manifest = getattr(info, "manifest", None)
    if manifest is None:
        manifest = info
    if isinstance(manifest, dict):
        raw = manifest.get("lifecycle")
    else:
        raw = getattr(manifest, "lifecycle", None)
    if raw is None:
        return None
    return raw if isinstance(raw, str) else str(raw)


def declaration_problem(info: Any) -> str | None:
    """校验「manifest.lifecycle 声明」与「api/lifecycle.py 实现」是否一致。

    返回 None = 一致；返回字符串 = 不一致（调用方写进 last_error 供 readyz 目击）。

    四种情形：
      · 声明 hooks + 文件在 + 至少一个钩子可调用 ⇒ 一致
      · 声明 hooks + 文件不在 / 没有钩子         ⇒ 「假承诺」
      · 未声明 + 文件不在                        ⇒ 一致（绝大多数插件）
      · 未声明 + 文件里有钩子                     ⇒ 「隐形钩子」（声明漏了）
    """
    declared = _declared(info)
    plugin_id = getattr(info, "id", "?")
    mod = _load_lifecycle_module(info)
    hooks = []
    if mod is not None:
        hooks = [name for name in _STAGE_ATTR.values() if callable(getattr(mod, name, None))]

    if declared == "hooks":
        if mod is None:
            return (
                f"插件 {plugin_id!r} 声明 lifecycle=\"hooks\"，但 api/lifecycle.py 不存在"
                " —— 声明是契约，要么补文件，要么把声明去掉"
            )
        if not hooks:
            return (
                f"插件 {plugin_id!r} 声明 lifecycle=\"hooks\"，但 api/lifecycle.py 里"
                f"没有可调用的固定名钩子 {sorted(_STAGE_ATTR.values())}"
            )
        return None

    if declared is None:
        if hooks:
            return (
                f"插件 {plugin_id!r} 的 api/lifecycle.py 定义了钩子 {hooks}，"
                "但 manifest 未声明 lifecycle=\"hooks\" —— 隐形钩子没人知道会执行，"
                "请补声明（或删掉钩子）"
            )
        return None

    return f"插件 {plugin_id!r} 的 lifecycle 声明值非法：{declared!r}（只认 \"hooks\"）"


def run_lifecycle_hook(info: Any, stage: str, *, db: Any = None) -> str | None:
    """执行某阶段钩子。返回 None 表示成功，返回字符串表示捕获到的错误。

    ★ 声明与实现不一致时，**先返回那句不一致**（软失败，写进 last_error），
      但仍然执行钩子 —— 文件在、函数在，就没理由不跑。
      这样「声明写错了」会被看见，而不是把功能一起停掉。
    """
    if stage not in _STAGE_ATTR:
        raise ValueError(f"未知生命周期阶段：{stage}")
    problem = declaration_problem(info)
    mod = _load_lifecycle_module(info)
    if mod is None:
        return problem
    fn = getattr(mod, _STAGE_ATTR[stage], None)
    if not callable(fn):
        return problem
    try:
        if db is not None:
            fn(db=db)
        else:
            fn()
    except Exception:  # noqa: BLE001
        return traceback.format_exc()
    return problem
