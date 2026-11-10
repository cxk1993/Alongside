"""今日摘要聚合模块（U2 · 小日历聚合）—— **薄壳**。

★ 本模块**不提供** `/today`（2026-11-10 裁决，选项 a）：

  今日摘要只有一个实现 —— 内核 BFF `GET /api/v1/summary/today`（`core/app.py`）。
  它按 manifest 声明（`provides: x.summary.today`）动态发现聚合源，
  并发取各模块的 `today-summary`，只转发、不解析业务字段。

  本模块原先也有一个 `/today`，与 BFF **同路径**：`include_router(events_router)`
  先于模块挂载执行，FastAPI 先注册先匹配 ⇒ 模块那条永远不触发。
  那是「看起来有两个实现，其实只有一个」—— 已删，别再加回来
  （`tests_protocol/test_route_shadowing.py` 会拦）。

  为什么不是「把 BFF 挪走、让模块活过来」：summary 模块**没有独立数据源**，
  它读的全是别人的表/端点。留着它 = 在内核之外再放一份聚合逻辑，
  而「内核只转发不解析」的边界正是靠「聚合只有一处」撑住的。

  本模块现在只保留 `/health`（内核据此判断该能力可用）。
"""
from .router import router

__all__ = ["router"]