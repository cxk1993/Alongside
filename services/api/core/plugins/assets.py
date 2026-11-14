"""插件前端资产的运行时服务端点（r5 热插拔 · 层2.2）。

    GET /plugin-assets/{plugin_id}/{path}

★ 为什么是**薄 ASGI app**，而不是 `StaticFiles` 整目录挂载
--------------------------------------------------------
`StaticFiles(directory=data_dir/plugins)` 是**目录级**的：它按路径发文件，
不认识「插件」这个概念，于是

  · 停用（PAUSED）的插件，它的 JS 照样能下载；
  · 从未启用的插件、甚至校验失败的插件，产物照样能下载；
  · `manifest.json` / `api/*.py` / `.env` 这些**不该对外**的文件也一并暴露。

而「停用」的语义是**停止对外服务**（`phase.py` 的 PAUSED 定义）。
资产端点若绕过它，disable 就只拔掉了一半 —— 路由 404 了，界面代码还在被加载。
⇒ 必须逐请求判「这个插件此刻是不是 ACTIVE」，StaticFiles 做不到，故自己写。

★ 信任模型（v1，D4 安全门禁的前提，必须写明）
--------------------------------------------
第三方插件的 UI **就是用户会话里的任意 JS**：它与宿主同源、拿得到 access token、
能调任何该用户能调的 API。v1 **不提供沙箱**，安全性建立在两道闸上：

  1. **安装是 admin 闸 + 明示信任** —— 只有管理员能上传/安装插件
     （`POST /api/v1/plugins/upload` / `/install` 都要 Bearer）。
     装一个插件等价于「我信任这段代码以我的身份运行」，与装一个浏览器扩展同性质。
  2. **资产只服务已启用插件的构建产物** —— 未启用的代码进不了浏览器。

iframe 沙箱（方案 B）留二期：它会连带失去共享 React / 直接调 API 的能力，
是一次独立的架构取舍，不在本轮顺手做。

★ 本端点自己的防线（与信任模型无关，逐条都有测试守着）
------------------------------------------------------
  · 登录鉴权：无 / 无效 Bearer ⇒ 401（复用内核 `decode_token`，不另造一套）
  · 只读：非 GET/HEAD ⇒ 405
  · phase 闸：插件不存在 / 未启用 / 非 ACTIVE ⇒ 404（**不区分**，避免枚举探测）
  · 穿越防护：规范化后必须仍在 `<插件目录>/web/dist/` 内（含编码变体、绝对路径、
    Windows 反斜杠、符号链接逃逸）
  · MIME 白名单：按扩展名查表，未知扩展名 ⇒ 404（不猜、不回退 octet-stream）
  · `X-Content-Type-Options: nosniff`：禁止浏览器把资产嗅探成别的类型
  · `Cache-Control: no-cache`：插件更新后不能吃到旧产物
    （★ 前缀刻意避开 `/assets/`：前端 sw.js 对那个前缀是 cache-first，会钉死更新）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from core.logging import get_logger

log = get_logger("kernel.plugins.assets")

#: 资产端点前缀（与契约 v1 / nginx 反代 / 前端 entry 改写三处一致）。
ASSETS_PREFIX = "/plugin-assets"

#: 插件目录内的资产根（相对插件根）。源码在 `web/`，产物在 `web/dist/`。
ASSET_SUBDIR = ("web", "dist")

#: 扩展名 → Content-Type **白名单**。
#: ★ 为什么是白名单而不是 mimetypes.guess_type：
#:   后者认识几百种类型，其中 `.html` / `.svg` 是可执行文档面，
#:   而「猜不出来回退 application/octet-stream」等于把未知文件也发出去。
#:   插件资产只需要这几种；多一种都要显式加，加的时候人会想一想。
#: ★ 刻意**不含** text/html：插件 UI 是 ESM 组件，不是整页文档。
#:   发 HTML 等于给插件一个同源 phishing 面（能画一个假登录框）。
_MIME_WHITELIST: dict[str, str] = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".wasm": "application/wasm",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}

#: 单文件上限（16 MiB）。插件产物是 UI 代码，超过这个量级基本是误传了别的东西。
MAX_ASSET_BYTES = 16 * 1024 * 1024

_READ_METHODS = frozenset({"GET", "HEAD"})


def _is_within(path: Path, root: Path) -> bool:
    """`path` 规范化后是否仍在 `root` 内（含等于 root 本身）。

    ★ 用 `resolve()` 而不是字符串前缀比较：
      字符串比较挡不住符号链接（`web/dist/evil -> /etc`）与 Windows 的
      大小写/短路径变体；`resolve()` 会把符号链接一并展开成真实路径。
    ★ `is_relative_to` 是**按路径分量**比较，不是 startswith ——
      后者会把 `/data/plugins-evil` 判成 `/data/plugins` 的子路径。
    """
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        # resolve 失败（循环符号链接、路径过长）⇒ 一律当作越界
        return False


def resolve_asset_path(plugin_dir: Path, raw_path: str) -> Path | None:
    """把 URL 里的 `{path}` 解析成磁盘上的资产文件；不安全/不存在 ⇒ None。

    ★ 每一跳都规范化后再判归属，而不是「先拼再检查一次」：
      `%2e%2e%2f` 这类编码变体在 URL 层看不出问题，解码后才现形；
      而 Windows 下 `..\\` 与 `../` 等价，只替换一种就漏另一种。
    ★ 拒绝绝对路径与盘符：`/etc/passwd`、`C:\\Windows\\...` 都不是相对资产名。
    """
    if not raw_path:
        return None

    # ★ 先解码再判：调用方（ASGI）给的 path 已是 percent-decoded 一次，
    #   但双重编码（%252e%252e）会在这里再解一层 —— 两层都判，才挡得住。
    for candidate in (raw_path, unquote(raw_path)):
        # 反斜杠统一成正斜杠：Windows 下两者都是分隔符
        normalized = candidate.replace("\\", "/")
        if normalized.startswith("/") or normalized.startswith("//"):
            return None
        # 盘符（C:/…）与 UNC（//server/share）
        if len(normalized) >= 2 and normalized[1] == ":":
            return None
        if "\x00" in normalized:
            return None
        parts = normalized.split("/")
        if any(p in ("..",) for p in parts):
            return None

    root = plugin_dir.joinpath(*ASSET_SUBDIR)
    # ★ 根自身也要钉住（r5 · 独立审计发现）：
    #   若 `<插件>/web/dist` **本身**是指向插件目录之外的符号链接，
    #   那么 `_is_within(target, root)` 会因为 root 也被 resolve 到外部
    #   而「合法地」通过 —— 根已经在外面了，里面的一切当然都在「里面」。
    #   实测：把 dist 链到插件目录外，`leak.js` 会被照常服务。
    #   ⇒ 先要求「根的落点必须仍在插件目录内」，再判目标在根内。
    #   两道合起来才是完整判据；只做后者会漏掉「根被换掉」这一类。
    try:
        real_plugin = plugin_dir.resolve()
        real_root = root.resolve()
        if not real_root.is_relative_to(real_plugin):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    target = root.joinpath(*[p for p in raw_path.replace("\\", "/").split("/") if p not in ("", ".")])
    if not _is_within(target, root):
        return None
    try:
        if not target.is_file():
            return None
    except OSError:
        return None
    return target


def asset_content_type(path: Path) -> str | None:
    """按扩展名查 MIME 白名单；不在表内 ⇒ None（调用方回 404）。"""
    return _MIME_WHITELIST.get(path.suffix.lower())


class PluginAssetsApp:
    """薄 ASGI app：`GET /plugin-assets/{id}/{path}`。

    ★ 为什么直接写 ASGI 而不是 FastAPI 路由：
      这是一个**文件服务**，不需要参数校验/依赖注入/OpenAPI 文档；
      走 FastAPI 反而会把 `{path:path}` 的规范化交给框架，
      而穿越防护恰恰要求**我自己**控制解码与规范化的每一步。
      薄 ASGI 让「URL → 磁盘路径」这条链完整地在这一个文件里，可审计。

    ★ 依赖注入方式：构造时传两个窄回调（查插件目录、查是否 ACTIVE），
      不传整个 PluginManager —— 与包层 `boot.py` 同纪律：
      传窄动作，不把内核一大块知识塞进来。
    """

    def __init__(
        self,
        *,
        find_plugin_dir: Any,
        is_active: Any,
        decode_bearer: Any,
    ) -> None:
        #: (plugin_id) -> Path | None
        self._find_plugin_dir = find_plugin_dir
        #: (plugin_id) -> bool
        self._is_active = is_active
        #: (token) -> payload dict；无效时抛异常
        self._decode_bearer = decode_bearer

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            # lifespan / websocket 一律不管（本 app 只服务 http）
            return

        method = scope.get("method", "GET").upper()
        # ★ 只读端点：写方法明确 405，而不是「碰巧没实现所以 404」——
        #   405 告诉调用方「这个资源存在但你不能这么用」，语义诚实。
        if method not in _READ_METHODS:
            await _respond(
                send, 405, b'{"detail":"plugin assets are read-only"}',
                "application/json; charset=utf-8", extra_headers=(("allow", "GET, HEAD"),),
            )
            return

        # ── 鉴权（先于任何磁盘访问：未登录不该能探测插件是否存在）──
        if not self._authenticated(scope):
            await _respond(
                send, 401, b'{"detail":"missing or invalid bearer token"}',
                "application/json; charset=utf-8",
                extra_headers=(("www-authenticate", "Bearer"),),
            )
            return

        plugin_id, raw_path = self._split(scope)
        if not plugin_id or not raw_path:
            await _not_found(send)
            return

        # ── phase 闸：只有 ACTIVE 的插件才回文件 ──
        # ★ 不存在 / 未启用 / 非 ACTIVE **一律同一个 404**：
        #   区分它们等于给出一个「哪些插件装了但没开」的枚举探针。
        try:
            plugin_dir = self._find_plugin_dir(plugin_id)
            active = bool(plugin_dir) and self._is_active(plugin_id)
        except Exception as exc:  # noqa: BLE001 —— 查库失败不该 500 泄露内部
            log.warning("插件资产查询失败", extra={"plugin_id": plugin_id, "error": str(exc)})
            await _not_found(send)
            return
        if not plugin_dir or not active:
            await _not_found(send)
            return

        target = resolve_asset_path(Path(plugin_dir), raw_path)
        if target is None:
            await _not_found(send)
            return
        ctype = asset_content_type(target)
        if ctype is None:
            # 白名单外的扩展名：不猜类型、不回退 octet-stream
            await _not_found(send)
            return
        try:
            size = target.stat().st_size
            if size > MAX_ASSET_BYTES:
                await _not_found(send)
                return
            body = target.read_bytes()
        except OSError:
            await _not_found(send)
            return

        await _respond(
            send, 200, body if method == "GET" else b"", ctype,
            extra_headers=(("content-length", str(len(body))),),
        )

    # ── 内部 ────────────────────────────────────────────────────────
    def _authenticated(self, scope: Any) -> bool:
        """从 Authorization 头取 Bearer 并校验。

        ★ 复用内核 `decode_token(expected_type="access")`，不另造一套鉴权：
          两套验签逻辑迟早分叉，而分叉的那一套一定是弱的那一套。
        ★ 刻意**不接受** query 里的 token：URL 会进浏览器历史 / 代理日志 / Referer。
        """
        for key, value in scope.get("headers") or ():
            if key == b"authorization":
                raw = value.decode("latin-1")
                if not raw.lower().startswith("bearer "):
                    return False
                token = raw[7:].strip()
                if not token:
                    return False
                try:
                    self._decode_bearer(token)
                except Exception:  # noqa: BLE001 —— 任何校验失败都是 401
                    return False
                return True
        return False

    @staticmethod
    def _split(scope: Any) -> tuple[str, str]:
        """从 scope 拆出 (plugin_id, 资产相对路径)。

        ★ 用 `scope["path"]`（ASGI 服务器已 percent-decode 一次）而不是 raw_path：
          raw_path 未解码，`%2e%2e` 会原样进来，虽然 resolve_asset_path 也会再解一层，
          但两处解码口径必须一致 —— 统一以 decoded 为准，另一层作为纵深防御。
        ★ `root_path` 是挂载前缀（Starlette mount 会设它），要剥掉。
        """
        path = scope.get("path") or ""
        root_path = scope.get("root_path") or ""
        if root_path and path.startswith(root_path):
            path = path[len(root_path):]
        # 兜底：某些挂载方式下 path 仍带完整前缀
        if path.startswith(ASSETS_PREFIX):
            path = path[len(ASSETS_PREFIX):]
        path = path.lstrip("/")
        # 去掉 query（scope["path"] 本不含 query，这里防御性处理）
        path = urlsplit(path).path
        plugin_id, _, rest = path.partition("/")
        return plugin_id, rest


async def _not_found(send: Any) -> None:
    await _respond(
        send, 404, b'{"detail":"asset not found"}', "application/json; charset=utf-8"
    )


async def _respond(
    send: Any,
    status: int,
    body: bytes,
    content_type: str,
    *,
    extra_headers: tuple[tuple[str, str], ...] = (),
) -> None:
    """统一出口：安全头在这里加一次，所有分支都带上。

    ★ 为什么集中在出口而不是各分支自己加：
      「每个 return 都记得加 nosniff」是靠不住的 —— 漏一个分支就是一个缺口，
      而漏掉的那个往往是错误分支（没人测）。集中一处，结构上不可能漏。
    """
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", content_type.encode("latin-1")),
        (b"content-length", str(len(body)).encode("latin-1")),
        # 禁止 MIME 嗅探：把 .js 当 HTML 渲染是经典 XSS 路径
        (b"x-content-type-options", b"nosniff"),
        # 插件更新后必须能拿到新产物（配合前端 sw.js 对本前缀不接管）
        (b"cache-control", b"no-cache"),
        # 资产不参与跨源共享：只给同源前端用
        (b"referrer-policy", b"no-referrer"),
    ]
    seen = {k for k, _ in headers}
    for key, value in extra_headers:
        kb = key.lower().encode("latin-1")
        if kb in seen:
            headers = [(k, v) for k, v in headers if k != kb]
        headers.append((kb, value.encode("latin-1")))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


__all__ = [
    "ASSETS_PREFIX",
    "ASSET_SUBDIR",
    "MAX_ASSET_BYTES",
    "PluginAssetsApp",
    "asset_content_type",
    "resolve_asset_path",
]