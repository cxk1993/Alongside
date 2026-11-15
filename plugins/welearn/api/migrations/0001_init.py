"""插件迁移 0001：建 welearn_session / welearn_synced。

★ 只增不改；models.py 必须按路径加载，模块键与 router.py 的 `_MODEL_KEY` 一致。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_MODEL_MODULE = "welearn_models"


def _load_models() -> Any:
    cached = sys.modules.get(_MODEL_MODULE)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parent.parent / "models.py"
    spec = importlib.util.spec_from_file_location(_MODEL_MODULE, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODEL_MODULE] = module
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
