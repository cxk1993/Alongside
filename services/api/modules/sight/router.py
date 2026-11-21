"""插件路由：此刻（sight）。

HTTP 契约（全项目统一）：
  成功 → 直接返回资源 JSON（200）
  失败 → 由内核统一转成 RFC7807 application/problem+json

★ 前缀由内核按 manifest.api.base 自动加，这里不要写 prefix=。
★ 插件之间不许 import —— 聚合走 HTTP（aggregator.py）。
★ 本模块只读，不持有任何表（migrations: null）。
"""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from core.deps import get_current_user
from core.security import User

from .aggregator import aggregate_now

router = APIRouter()

_MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"
with _MANIFEST_PATH.open(encoding="utf-8") as _f:
    _MANIFEST = json.load(_f)


@router.get("/health")
def health() -> dict[str, bool]:
    """每个插件都必须有 health —— 内核据此判断「该能力是否可用」。"""
    return {"ok": True}


@router.get("/manifest")
def manifest() -> dict:
    """模块清单（前端 / AI 发现能力用）。"""
    return _MANIFEST


@router.get("/now")
async def now(_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """★ 此刻处境（契约：`contracts/sight.schema.json`）。

    设计要点（不要在实现里丢）：
      1. 返回的是**处境**，不是数据表 —— `tensions` 由系统先判断再交出。
      2. **永远 200**：单源超时/失败只让 `degraded` 变长，绝不 500。
      3. `degraded` 里的 `not_implemented` 是合法答案 —— 没接的源如实列出，不装深度。
    """
    return await aggregate_now()
