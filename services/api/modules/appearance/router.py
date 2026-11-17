"""外观与皮肤模块：只有探针。

★ 为什么后端是空的：皮肤是**纯前端状态**（localStorage + 一组 CSS 变量），
  不进数据库、不出网、不需要同步。给它一个后端模块，只是为了让内核能把
  「窗口 + 图标 + 入口」按 manifest 机械地长出来（一切皆插件），
  而不是在桌面里硬编码一个设置窗。
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    """内核据此判断该模块是否可用。"""
    return {"ok": True}
