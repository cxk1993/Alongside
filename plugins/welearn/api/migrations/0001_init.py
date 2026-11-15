"""插件迁移 0001：建 welearn_session / welearn_synced。

★ 只增不改：已经执行过的迁移不许再动，要改就加 0002。
★ 模块加载（r8 · N1）：迁移比路由**先**执行，且自己不是任何包的子模块，
  所以这里主动把 `api/` 按内核的包命名（`plugin_thirdparty_welearn`）注册进
  sys.modules，再把 models 作为该包的子模块加载 —— 于是路由那边的
  `from . import models` 命中**同一份**模块对象，表只定义一次；
  卸载时 `unload_thirdparty_modules()` 按包前缀清理，也能一并清掉。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_PKG = "plugin_thirdparty_welearn"


def _ensure_pkg(api_dir: Path) -> None:
    """把 api/ 注册成包（幂等），与 core/plugins/discover.py 的口径逐字一致。"""
    existing = sys.modules.get(_PKG)
    if existing is not None and getattr(existing, "__path__", None) == [str(api_dir)]:
        return
    spec = importlib.util.spec_from_file_location(
        _PKG, api_dir / "__init__.py", submodule_search_locations=[str(api_dir)]
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"welearn 无法把 {api_dir} 注册为包")
    pkg = importlib.util.module_from_spec(spec)
    pkg.__path__ = [str(api_dir)]  # type: ignore[attr-defined]
    pkg.__package__ = _PKG
    sys.modules[_PKG] = pkg


def _load_models() -> Any:
    api_dir = Path(__file__).resolve().parent.parent
    _ensure_pkg(api_dir)
    key = f"{_PKG}.models"
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, api_dir / "models.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"welearn 无法加载 {api_dir / 'models.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def upgrade(engine: Any) -> None:
    models = _load_models()
    models.WelearnSession.__table__.create(bind=engine, checkfirst=True)
    models.WelearnSynced.__table__.create(bind=engine, checkfirst=True)


def downgrade(engine: Any) -> None:
    models = _load_models()
    models.WelearnSynced.__table__.drop(bind=engine, checkfirst=True)
    models.WelearnSession.__table__.drop(bind=engine, checkfirst=True)
