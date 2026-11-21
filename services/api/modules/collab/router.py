"""共处工作台路由（★ 极薄：只 health + manifest，v1 灰盒）。

本模块是**纯前端模块的后端壳**：桌面级背景面（desktop.widget）由前端 apps/collab 提供，
这里只满足契约要求（builtin 必带 api）并提供健康检查。未来布局保存/处境聚合
若落后端，再在此扩端点（布局即数据 / Sight 另立模块）。
★ 前缀由内核按 manifest.api.base 自动加，这里不要写 prefix=。
"""

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

_MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"
with _MANIFEST_PATH.open(encoding="utf-8") as _f:
    _MANIFEST = json.load(_f)


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/manifest")
def manifest() -> dict:
    return _MANIFEST
