"""路由遮蔽门禁：模块路由与内核路由**同路径**时，模块那条永远是死代码。

## 为什么要有这个

`/api/v1/summary/today` 曾经有两个实现：
  ① 内核 BFF（`core/app.py` 的 `events_router`）—— 聚合各模块的 `today-summary`；
  ② `modules/summary/router.py` 自己的 `/today` —— 转发到同一个聚合。

`app.include_router(events_router, prefix="/api/v1")` 在模块挂载**之前**执行，
FastAPI 先注册先匹配 ⇒ ②**永不触发**。它看起来像「模块也有这个能力」，
其实是死代码；更糟的是有人会去改 ②，然后疑惑「为什么改了没效果」。

⇒ 现在只保留 ①（内核只转发不解析；summary 模块没有独立数据源，
  它的数据来自 calendar/todo/diary/review 的 `today-summary`），
  模块侧只剩 `/health`。

★ 这条测试守的不是「summary 一个模块」，而是**同一类错误**：
  任何模块只要把自己的路由路径撞到内核显式路径上，就会静默失效。
  判据是路径集合求交，不是逐个模块人肉记。

## 判据

- 内核显式路径：`create_app()` 后 `app.routes` 里**带 path** 的那些
  （模块是 `Mount` 挂进去的，没有 path —— 所以这个集合干净地就是内核自己的）。
- 模块路径：`manifest.api.base` + 模块 router 里每条 route 的 `path`。
- 交集非空 ⇒ 死代码，报红。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

MODULES = Path(__file__).resolve().parents[2] / "modules"


def _kernel_paths(app: Any) -> set[tuple[str, str]]:
    """内核显式注册的 (method, path) 集合（Mount 没有 path，自然被排除）。"""
    out: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in getattr(route, "methods", None) or ():
            out.add((method, path))
    return out


def _module_paths(module_id: str, base: str, router: Any) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in router.routes:
        sub = getattr(route, "path", None)
        if not sub:
            continue
        full = base.rstrip("/") + sub
        for method in getattr(route, "methods", None) or ():
            out.add((method, full))
    return out


def _shadowed(
    module_paths: set[tuple[str, str]], kernel_paths: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    """模块路径 ∩ 内核路径 = 被内核抢先匹配、永远不会执行的那些。"""
    return module_paths & kernel_paths


def _builtin_manifests() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(MODULES.glob("*/manifest.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("api"):
            out.append(raw)
    return out


def test_no_module_route_is_shadowed_by_kernel() -> None:
    """真实仓库：没有任何模块路由撞上内核显式路径。"""
    logging.disable(logging.CRITICAL)
    try:
        from core.app import create_app
        from core.manifest import load_router

        app = create_app()
        kernel = _kernel_paths(app)
    finally:
        logging.disable(logging.NOTSET)

    assert kernel, "没取到任何内核路径 —— 这个判据等于没验"

    shadowed: dict[str, list[tuple[str, str]]] = {}
    for manifest in _builtin_manifests():
        mid = manifest["id"]
        router = load_router(mid)
        hit = _shadowed(_module_paths(mid, manifest["api"]["base"], router), kernel)
        if hit:
            shadowed[mid] = sorted(hit)

    assert not shadowed, (
        "以下模块路由与内核路径同路径，先注册的内核会抢先匹配 ⇒ 模块那条是死代码："
        f"{shadowed}"
    )


def test_summary_module_does_not_implement_today() -> None:
    """summary 模块只管 /health：今日摘要是内核 BFF 的职责（裁决 (a)）。"""
    logging.disable(logging.CRITICAL)
    try:
        from core.manifest import load_router

        router = load_router("summary")
    finally:
        logging.disable(logging.NOTSET)

    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/today" not in paths, (
        "summary 模块的 /today 又回来了 —— 它与内核 BFF 同路径，永远是死代码；"
        "今日摘要只该有一个实现（内核 BFF，只转发不解析）"
    )
    assert "/health" in paths


def test_judge_catches_injected_shadow() -> None:
    """反向自证：注入一条同路径路由，判定必须报出来。"""
    kernel = {("GET", "/api/v1/summary/today")}
    injected = {("GET", "/api/v1/summary/today"), ("GET", "/api/v1/summary/health")}
    assert _shadowed(injected, kernel) == {("GET", "/api/v1/summary/today")}


def test_judge_ignores_different_method_on_same_path() -> None:
    """同路径不同 method 不是遮蔽（POST /x 与 GET /x 各自独立）。"""
    kernel = {("GET", "/api/v1/x")}
    mine = {("POST", "/api/v1/x")}
    assert _shadowed(mine, kernel) == set()
