"""内容摘要（content digest）。

★ 为什么需要它
--------------
插件是**本地目录**。同一个版本号下代码被改过是常态（开发、修补、试验）。
只记版本号的锁，会在这种情况下**给出「没变」的错误结论**——
而锁的全部价值就在于「变了能发现」（协议 §15.1）。

所以摘要必须**真算**，不能占位。

★ 算哪些文件
------------
算「插件的**内容**」，不算「插件目录里恰好有什么」：

- **算**：源码、配置、迁移、manifest —— 决定插件**是什么**的文件
- **不算**：`__pycache__/`、`*.pyc`、`.git/`、`.venv/`、`node_modules/`、
  编辑器临时文件、测试目录

★ 为什么排除这些
----------------
`__pycache__` 是**由源码推导出来的**——同一份源码在不同机器、不同 Python 版本下
生成的 .pyc 字节不同。把 .pyc 算进摘要，等于摘要会随「谁在哪台机器上跑过」变化。
⇒ 摘要会变成噪声：什么都没改，摘要却变了。
⇒ 用户会学会忽略摘要变化 ⇒ 摘要就废了。

★ 确定性
--------
摘要必须**可复现**：同样的内容在任何机器、任何时间、任何文件系统上，
算出来必须一样。

所以：
- 文件按**相对路径排序**后逐个算（不依赖目录遍历顺序）
- 内容用 **bytes** 算（不换行、不做文本归一化——那会掩盖真实改动）
- 路径分隔符统一成 `/`（Windows 与 Linux 算出来一样）
- 计入**文件路径**（改了文件名也是内容变了）

★ 与「声明式摘要」的关系
------------------------
插件 manifest 里可以写 `digest` 字段（声明「我期望这个摘要」），
但插件 schema 目前没有这个字段 ⇒ 声明路径暂时走不通。
本模块负责的是**实算**：算出「现在这个插件实际是什么」。

一次算完的代价
--------------
插件目录不大（几十 KB 到几 MB），一次全量哈希在毫秒级。
所以**不缓存**——缓存会引入「缓存过期 ⇒ 漏报变化」的新问题，
而漏报变化是这个机制存在的唯一理由被破坏。

★ 一律**流式**读（不把整个文件读进内存）：
  摘要的对象可能是大文件，一次性 `read_bytes()` 会让内存占用随文件大小浮动。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

# 不参与摘要的目录名（任何层级）。
_EXCLUDED_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        "dist",
        "build",
        ".next",
        ".cache",
    }
)

# 不参与摘要的文件名 / 后缀。
_EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd", ".log", ".tmp", ".swp", ".orig", ".rej"})
_EXCLUDED_NAMES = frozenset({".DS_Store", "Thumbs.db"})

#: 流式读取的块大小。
_CHUNK = 256 * 1024


def _is_excluded(path: Path, root: Path) -> bool:
    """这个文件/目录该不该被排除。"""
    rel = path.relative_to(root)
    for part in rel.parts:
        if part in _EXCLUDED_DIRS:
            return True
    if path.name in _EXCLUDED_NAMES:
        return True
    return path.suffix.lower() in _EXCLUDED_SUFFIXES


def _iter_files(root: Path) -> list[Path]:
    """按相对路径排序的、该参与摘要的文件列表。

    ★ 排序是**可复现性**的前提：不排序的话，摘要会随文件系统
      返回目录项的顺序而变（ext4 与 APFS 的顺序不一样）。
    """
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        # 就地裁剪目录名，避免走进不该走的地方
        dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_DIRS)
        for name in filenames:
            f = current / name
            if _is_excluded(f, root):
                continue
            try:
                if not f.is_file():
                    continue
            except OSError:
                # 读不到 stat（权限/断链）⇒ 跳过，但要让调用方知道「不算全」
                continue
            files.append(f)
    files.sort(key=lambda p: p.relative_to(root).as_posix())
    return files


def content_digest(root: Path, *, exclude_names: frozenset[str] = frozenset()) -> str:
    """算一个目录的**内容摘要**。

    返回 `sha256:<64 位小写十六进制>`。

    ★ 空目录也算得出摘要（对空内容哈希）——
      一个没有任何文件的插件仍然有确定的内容，不该返回「无法计算」。

    ★ 目录不存在时抛 `FileNotFoundError`：
      「算不出摘要」和「这个插件不存在」是不同的事，
      前者该被显式处理，后者该硬拒绝。混起来会掩盖根因。

    ★ **不设文件大小上限**。
      早先想按「大于 2 MiB 就不算」来避免拖慢摘要，但那是**制造盲区**：
      一个巨大的二进制文件被跳过后，改动它摘要也不变——
      而摘要的全部价值就是「变了能发现」。宁可慢，也不能漏。
      ⇒ 一律流式哈希，内存占用恒定。

    ★ `exclude_names`：**按文件名**排除（不分目录层级）。
      唯一的用途是排除「基线锁自己」——包的目录摘要要把 `pack.lock.json`
      排除掉，否则算基线锁摘要时它自己就在被算的文件里：
      每写一次锁，摘要就变一次 ⇒ 摘要永远与现状相等 ⇒ 这个字段没有意义。
      （这是「自指」的典型形态：不是算错，是**恒等**，恒等的摘要什么也不证明。）
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"插件目录不存在：{root}")

    h = hashlib.sha256()
    for f in _iter_files(root):
        if f.name in exclude_names:
            continue
        rel = f.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            with f.open("rb") as handle:
                while True:
                    chunk = handle.read(_CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
        except OSError:
            # 读不到内容 ⇒ 把它记成「不可读」，别静默跳过
            h.update(b"<unreadable>")
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"


def pack_digest(pack_dir: Path) -> str:
    """算一个**包目录**的内容摘要（基线锁里的「包级摘要」）。

    ★ 算的是什么：包目录里的全部内容——`pack.json` 原文、内嵌的 `plugins/`、
      `README.md`……即「这个包**本身**是什么」。

    ★ 为什么不算成员插件的目录：
      成员是**引用**的（§13「默认引用 + 显式自包含」），
      它们的摘要由**成员级**摘要负责（基线锁的 `members[].digest`）。
      包级摘要算的是**包自己的那部分内容**——否则包级摘要与成员摘要
      会重复表达同一件事，还多一个「同一个成员被两个包引用时算几遍」的坑。

    ★ 为什么排除 `pack.lock.json`：
      基线锁要**住在包目录里**（跟着包分发，§15.1），
      所以它必然落在被算的范围内。若把它算进去：
      写一次锁 ⇒ 摘要变一次 ⇒ 下次对账时「包摘要与基线不符」恒成立。
      ⇒ **自指的摘要恒等于现状，什么也不证明**，必须排除。
    """
    return content_digest(Path(pack_dir), exclude_names=frozenset({"pack.lock.json"}))
