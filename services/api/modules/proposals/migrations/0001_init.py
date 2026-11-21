"""插件迁移 0001：建 proposals_change_proposal 表（变更提案 · 签字/撤销地基）。

★ 只增不改：已执行过的迁移文件不许再动，要改就加 0002。
★ models.py 必须**按文件路径**加载，不能写 `from .models import ...`：
  内核是按文件路径加载插件文件的（core/plugins/discover.py），
  相对导入会抛 ImportError: attempted relative import with no known parent package。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_MODEL_MODULE = "proposals_models"


def _load_models() -> Any:
    # ★ 全量 pytest / lifespan：create_app 可能已 import modules.proposals.models，
    #   再按文件路径 exec 会重复定义 Table → "already defined"。优先复用。
    for key in ("modules.proposals.models", _MODEL_MODULE):
        existing = sys.modules.get(key)
        if existing is not None:
            return existing
    path = Path(__file__).resolve().parent.parent / "models.py"
    spec = importlib.util.spec_from_file_location(_MODEL_MODULE, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODEL_MODULE] = mod
    spec.loader.exec_module(mod)
    return mod


def upgrade(engine: Any) -> None:
    models = _load_models()
    models.ChangeProposal.__table__.create(bind=engine, checkfirst=True)


def downgrade(engine: Any) -> None:
    models = _load_models()
    models.ChangeProposal.__table__.drop(bind=engine, checkfirst=True)
