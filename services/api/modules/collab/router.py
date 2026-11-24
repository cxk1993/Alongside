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
    """健康检查：本模块是否已激活。

    只回 `{"ok": true}`，不查数据库、不连外部服务 —— 它回答的是
    「这个模块的代码加载起来了吗」，不是「它依赖的东西都好吗」。
    """
    return {"ok": True}


@router.get("/manifest")
def manifest() -> dict:
    """返回本模块的 manifest 原文（前端据此渲染入口、内核据此做契约校验）。

    与 `services/api/modules/collab/manifest.json` 逐字一致；
    改 manifest 不需要重启，但前端要刷新才看到新值。
    """
    return _MANIFEST
