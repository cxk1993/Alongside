"""hello-seam 的兄弟模块 —— 证明第三方插件能用相对导入共享代码（层3.6）。

★ 这个文件存在的唯一理由就是让 `router.py` 里那句 `from .models import SEAM_MESSAGE`
  有落点。旧内核按文件路径加载 router 且不给 submodule_search_locations，
  于是这句会抛 ImportError：**第三方多文件插件根本写不出来**。
  r5 之后它可用 —— 所以这个插件的安装成功本身就是一条判据。
"""

SEAM_MESSAGE = "hello from a sibling module (relative import works)"
