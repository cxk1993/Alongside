"""插件迁移 0001：建 yuketang_session / yuketang_synced。

★ 只增不改：已经执行过的迁移不许再动，要改就加 0002。
★ models.py 必须按文件路径加载（第三方插件不能用相对导入），
  且模块键必须与 router.py 的 `_MODEL_KEY` 逐字一致 —— 否则 SQLModel
  会把同一张表定义两次，抛 "Table ... is already defined"。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_MODEL_MODULE = "yuketang_models"


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
    models.YuketangSession.__table__.create(bind=engine, checkfirst=True)
    models.YuketangSynced.__table__.create(bind=engine, checkfirst=True)


def downgrade(engine: Any) -> None:
    models = _load_models()
    models.YuketangSynced.__table__.drop(bind=engine, checkfirst=True)
    models.YuketangSession.__table__.drop(bind=engine, checkfirst=True)
