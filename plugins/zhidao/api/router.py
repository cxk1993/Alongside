"""知到 内嵌窗口插件：没有服务端逻辑，只提供探针。

★ 这个插件的全部内容就是 web/dist/index.js —— 一个把原站放进 iframe 的组件。
  不做数据抓取、不注入脚本、不代操作；登录态完全由浏览器自己决定。
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    """内核据此判断插件是否可用。"""
    return {"ok": True}
