"""学习软件聚合（study-hub）—— 薄壳。

本模块只提供一个「按钮/窗口」的注册位：真正的学习插件（mooc / ucampus / welearn /
yuketang / zhidao / sdu-feeds / pi-agent）各自独立注册在自己的 manifest 里，
前端 `apps/study-hub` 通过 PluginContext 读取它们的启用态并调用各自入口。

这里不聚合数据、不代理请求、不存任何账号信息——避免成为第二个真相源。
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    """插件健康探针（恒 200）。"""
    return {"ok": True}