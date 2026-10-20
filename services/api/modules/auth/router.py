"""auth 路由层（薄：只做参数校验与调用 service）。

★ 不写 prefix=：内核按 manifest.api.base 自动挂 /api/v1/auth。
★ 不自己捕获异常：内核统一转 RFC7807。
"""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Request, Response

from core.config import get_settings, read_setting
from core.deps import get_current_user
from core.errors import UnauthorizedError
from core.events import event_bus
from core.security import USER_SUB, User

from .schema import LoginIn, TokenOut, UserOut
from .service import AuthService

router = APIRouter()

REFRESH_COOKIE = "alongside_refresh"

#: ★ 改名兼容：旧品牌期的 cookie 名。升级瞬间浏览器手里还捏着旧 cookie，
#: 只认新名会把人当场踢下线 —— 读取时新名优先、旧名兜底；写入只写新名。
LEGACY_REFRESH_COOKIE = "lifos_refresh"


def _cookie_secure(request: Request) -> bool:
    """refresh cookie 是否带 Secure 属性（BUG-T30-1 修复，Zcode 2026-09-20 裁决方案 a）。

    背景：旧实现 `app_env == "production"` 一律 Secure —— 生产是明文 http 直连
    （http://IP:18080），浏览器在 http 源**拒存 Secure cookie**，refresh 链路永远
    拿不到凭证，access 过期（30 分钟）即被踢回登录页。

    配置项 AUTH_COOKIE_SECURE（经 read_setting，BEECOUNT_* 运行期开关同款先例，
    不进 pydantic Settings、不动 core/config.py）：

    - ``auto``（默认）：按请求实际 scheme 判定——https（含反代头
      ``X-Forwarded-Proto: https``）→ true；明文 http → false。
      上 HTTPS（T28/域名线）后无需改代码自动回到 Secure。
    - ``true`` / ``false``：显式覆盖（优先于 scheme 判定）。
      显式 true 时 http 下服务器仍会下发带 Secure 的 Set-Cookie（浏览器不存），
      这是显式配置者自己的选择，见 .env.example 说明。
    """
    raw = (read_setting("AUTH_COOKIE_SECURE", "auto") or "auto").strip().lower()
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    # auto：请求 scheme 优先；明文连接再看反代头（https 直连无需信任任何头）
    if request.url.scheme == "https":
        return True
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0]
    return forwarded.strip().lower() == "https"


@router.get("/health")
def health() -> dict[str, bool]:
    """插件健康探针（恒 200）。"""
    return {"ok": True}


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, response: Response) -> TokenOut:
    """登录：校验凭据 → 下发 access token；**refresh token 写进 httpOnly Cookie**（前端不接触）。"""
    tokens = AuthService().login(body.password, body.totp, body.username)
    # refresh 走 httpOnly Cookie（不进 JS，防 XSS 窃取）
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=tokens["refresh"],
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        path="/api/v1/auth",
        max_age=get_settings().jwt_refresh_days * 86400,
    )
    event_bus.publish("auth.login", {"sub": USER_SUB}, source="auth")
    return TokenOut(
        access_token=tokens["access"],
        expires_in=get_settings().jwt_access_minutes * 60,
        user={"sub": USER_SUB},
    )


@router.post("/refresh", response_model=TokenOut)
def refresh(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(
        default=None, alias=REFRESH_COOKIE, description="放在 httpOnly Cookie 里的 refresh token（浏览器自动带上）"
    ),
    legacy_refresh_token: str | None = Cookie(
        default=None,
        alias=LEGACY_REFRESH_COOKIE,
        include_in_schema=False,
        description="改名前的旧 cookie 名（兼容回退，过渡期后自然失效）",
    ),
) -> TokenOut:
    """用 refresh Cookie 换一枚新的 access token（并轮换 Cookie）。"""
    # ★ 新名优先、旧名兜底：老用户手里的旧 cookie 仍能换到新 access token。
    refresh_token = refresh_token or legacy_refresh_token
    if not refresh_token:
        raise UnauthorizedError("缺少 refresh token（Cookie）")
    access = AuthService().refresh(refresh_token)
    # 刷新后顺便续期 refresh cookie
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        path="/api/v1/auth",
        max_age=get_settings().jwt_refresh_days * 86400,
    )
    return TokenOut(
        access_token=access,
        expires_in=get_settings().jwt_access_minutes * 60,
        user={"sub": USER_SUB},
    )


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    """登出：清掉 refresh Cookie（**尽力而为** —— 后端不可达也要把本地退干净）。"""
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    # ★ 改名兼容：旧名 cookie 也一并清掉，别让它在过渡期一直挂着。
    response.delete_cookie(LEGACY_REFRESH_COOKIE, path="/api/v1/auth")
    event_bus.publish("auth.logout", {"sub": USER_SUB}, source="auth")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:  # noqa: B008
    """读当前登录者（`sub` + `scopes`），前端据此渲染与权限相关的界面。"""
    info = AuthService().user_info(user.sub)
    return UserOut(sub=info["sub"], scopes=info.get("scopes", []))
