"""安全读文件：路径穿越校验 + 内容读取（处理 BOM / utf-8）。

★ 所有读都先过 `safe_resolve`，确认解析后的绝对路径仍在库根目录之内，
  否则一律拒绝（防 ../../../../etc/passwd 这类穿越）。
★ 库内路径统一用 POSIX 风格相对路径（如 复盘/2026-09.md），响应里只回这个，
  绝不回本机绝对路径（避免泄露主人目录结构）。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath

from .config import Lib

_TRAVERSAL_RE = re.compile(r"(^|/)\.\.(/|$)")


def is_traversal(posix_rel: str) -> bool:
    """相对路径里出现 .. 即视为穿越尝试（无论大小写、是否 URL 编码都由调用方先解码）。"""
    return bool(_TRAVERSAL_RE.search(posix_rel.replace("\\", "/")))


def safe_resolve(lib: Lib, posix_rel: str) -> Path:
    """把库内 POSIX 相对路径解析成本机绝对路径，并校验仍在库根内。

    返回解析后的绝对路径；越界抛 ValueError（由 main 转成 400）。
    """
    if not posix_rel:
        raise ValueError("路径为空")
    if is_traversal(posix_rel):
        raise ValueError(f"路径穿越被拒绝：{posix_rel}")

    root = lib.abs_path()
    # 用 PurePosixPath 归一化，再拼到库根；Windows 上 Path 会把 / 当成分隔符
    rel = PurePosixPath(posix_rel.lstrip("/"))
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    # 关键守卫：target 必须 == 库根 或 库根的子路径
    if target != root_resolved and root_resolved not in target.parents:
        raise ValueError(f"路径越界（不在库 {lib.id} 内）：{posix_rel}")
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在：{posix_rel}")
    return target


def read_note(lib: Lib, posix_rel: str) -> dict:
    """读一个笔记文件，返回 {rel_path, content, mtime, size, hash}。

    content 用 utf-8 读取并处理 BOM；hash 为内容 sha256（用于增量判定）。
    """
    target = safe_resolve(lib, posix_rel)
    raw = target.read_bytes()
    # 去 BOM（utf-8-sig 会吞掉 BOM）
    text = raw.decode("utf-8-sig", errors="replace")
    stat = target.stat()
    return {
        "rel_path": posix_rel.replace("\\", "/"),
        "content": text,
        "mtime": int(stat.st_mtime),
        "size": stat.st_size,
        "hash": hashlib.sha256(raw).hexdigest(),
    }


def read_binary(lib: Lib, posix_rel: str) -> tuple[bytes, str]:
    """★ 读一个**二进制附件**（图片等），返回 (bytes, mime)。

    用途（astrbot 下场 · 主人⑤「ob 笔记的附件、图片插入的这个 md 文件展示功能也要加上」）：
      Alongside 的 md 渲染层遇到 `![[xxx.png]]` / `![](attachments/xxx.png)` 时，
      经本端点取回原始字节，由后端透传、前端渲染。

    安全：与 read_note 同款 —— 先过 `safe_resolve`（路径穿越 + 越界双守卫）。
    """
    target = safe_resolve(lib, posix_rel)
    data = target.read_bytes()
    return data, _guess_mime(target.suffix)


_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}


def _guess_mime(suffix: str) -> str:
    """按扩展名猜 MIME（未知一律 application/octet-stream，防误判为可执行）。"""
    return _MIME_BY_EXT.get(suffix.lower(), "application/octet-stream")


def title_of(content: str) -> str:
    """取第一个非空行作为标题（Obsidian 习惯：首行 # 标题）。"""
    for line in content.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


def excerpt_of(content: str, limit: int = 200) -> str:
    """取正文前 limit 个字符作为摘要（去掉 Markdown 标记噪声）。"""
    plain = re.sub(r"[#>*_`~\-]+", " ", content)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:limit]
