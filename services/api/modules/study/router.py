"""学业页（study）—— 薄壳。

前端 `apps/study/StudyApp.tsx` 复用 todo 的「学业」层级标签取数（不新建表、不复制接口）；
本模块只提供 manifest 与健康探针，让 `schedule.tabs[].entry = "study"` 有真实模块可解析。
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    """插件健康探针（恒 200）。"""
    return {"ok": True}
