"""U校园 内嵌窗口插件：没有服务端逻辑，只提供探针。"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
