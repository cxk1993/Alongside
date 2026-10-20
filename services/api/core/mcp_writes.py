"""MCP / AI 写操作的**来源识别**（内核级，零业务词）。

★ 为什么需要（主人 2026-10-02 令）：
    「在 mcp 这里，创建待办的时候强制加标签，防止忘加」
  硬性要求**只在 AI/MCP 来路**生效 —— 人的前端操作一点不受影响。
  （若把 required 加在 pydantic 模型上，前端手输「买牛奶」会被 422 拦住，
    而 QuickAdd 的 placeholder 明示标签是可选的 —— 那是打断主人，不是护栏。）

★ 为什么放在内核而不是 mcp 插件里：
  MCP 插件有三个入口（本地转发 forward / 外部源 external / 审计 audit），
  但**只有 forward 这一条**打到插件自己的 REST 端点。若把标记写进 mcp 插件，
  就等于把「内核机制」塞进一个业务无关的适配层。放在 core/ 下，
  任何来路（今天 MCP、明天别的 AI 通道）都复用同一条判据。

★ 与 ADR-0003 同一哲学：
  内核只回答「是不是 MCP 来路」；**「MCP 来路要带哪些字段」留给插件自己声明**
  —— 机制归内核，业务规则归插件。

用法（插件侧）::

    from core.mcp_writes import is_mcp_call

    if is_mcp_call(request) and not tags:
        raise ValidationError("MCP 创建必须带 tags …")
"""
from __future__ import annotations

from typing import Any

#: 转发层在内部 HTTP 请求上打的来源标记头。
HEADER = "X-Alongside-Client"

#: ★ 改名兼容：旧品牌期的来源标记头，仍然认（否则升级瞬间 AI 来路会被误判成人类，
#: 待办的「必须带标签」护栏静默失效）。判定顺序：新头优先，旧头兜底。
LEGACY_HEADERS = ("X-LifeOS-Client",)

#: 标记取值：来自 MCP / AI 转发层。
CLIENT_MCP = "mcp"


def is_mcp_call(request: Any) -> bool:
    """该请求是否来自 MCP/AI 转发层。

    保守取值：拿不到 request、没有头、头值不匹配 —— 一律 **False（放行）**。
    宁可漏拦一次 AI，也不能误伤人自己的操作。
    """
    headers = getattr(request, "headers", None)
    if headers is None:
        return False
    try:
        for name in (HEADER, *LEGACY_HEADERS):
            if (headers.get(name) or "").strip().lower() == CLIENT_MCP:
                return True
        return False
    except Exception:  # noqa: BLE001 —— 任何取头异常都按「不是 MCP」处理
        return False
