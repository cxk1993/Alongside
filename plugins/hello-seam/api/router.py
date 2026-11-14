"""hello-seam 的后端路由 —— 最小示例（r5 热插拔验收用）。

★ 本文件同时是**层3.6（按包加载）的活证据**：它用 `from .models import ...`
  这种**相对导入**，而旧内核（按文件路径加载、不给 submodule_search_locations）
  会抛 `attempted relative import with no known parent package`。
  换句话说：这个插件装得上，就说明多文件第三方插件真的能写了
  （学长的 sdu-feeds 就是多文件的）。

★ 与内置插件不同，第三方插件**不能** import `modules.*`，也不能 join 别人的表。
  要别人的数据就调对方的 API，并在 manifest.requires 里声明能力。
"""
from __future__ import annotations

from fastapi import APIRouter

from .models import SEAM_MESSAGE

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    """子系统探针（内核按 manifest.api.health 调它）。"""
    return {"ok": True}


@router.get("/greeting")
def greeting() -> dict[str, str]:
    """示例资源端点：返回一句来自兄弟模块的话。

    ★ 值来自 `models.py`（相对导入的那个兄弟模块）——
      这不只是示例，它是「同一插件内多文件共享代码」这条路真的通了的判据。
    """
    return {"message": SEAM_MESSAGE, "plugin": "hello-seam"}
