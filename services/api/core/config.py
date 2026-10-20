"""内核配置（pydantic-settings 读取 .env）。

★ 铁律：任何真实密钥都不许写进代码，一律从这里读。
  必填项缺失时，启动要报**清晰**错误（指明缺哪个），不许静默回退默认值。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, overload

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（services/api/core/config.py -> parents[3] == 项目根 life/）。
# 用来在 cwd 之外也能定位 .env（uvicorn / pytest 都在 services/api 下运行）。
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def _dotenv_values() -> dict[str, str]:
    """解析项目 .env（cwd/.env 与项目根 .env），返回去引号/去注释的 KEY->VALUE。

    背景：本项目的 .env 仅由 pydantic-settings 的 Settings 加载，**不会**注入进程
    os.environ。部分模块直接读 os.environ 会拿不到值（见 finance 模块 BeeCount 同步故障）。
    此函数把 .env 解析成字典，供 read_setting 在 os.environ 缺失时回退使用。
    与 Settings.env_file 同源（顺序：cwd/.env 优先于项目根 .env）。
    """
    values: dict[str, str] = {}
    for path in (Path.cwd() / ".env", _PROJECT_ROOT / ".env"):
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    return values


@overload
def read_setting(key: str, default: str) -> str: ...


@overload
def read_setting(key: str, default: None = None) -> str | None: ...


def read_setting(key: str, default: str | None = None) -> str | None:
    """读取配置项：os.environ 优先，缺失时回退解析项目根 .env。

    用于那些 pydantic-settings 未声明的运行期开关（如 BEECOUNT_* / DASHBOARD_* /
    REVIEW_*）。语义与 Settings 的 env_file 一致：真实环境变量覆盖 .env 文件值。

    注意：返回 None 仅当未提供 default 且 env/.env 都没有该键；传 "" 等字符串
    default 时永远返回 str（见上面的 @overload，调用处因此不必写 `or ""`）。
    """
    val = os.environ.get(key)
    if val:
        return val
    return _dotenv_values().get(key, default)


class Settings(BaseSettings):
    """所有运行时配置。

    必填（无默认值）：SECRET_KEY / ADMIN_PASSWORD_HASH / TOTP_SECRET。
    缺任何一个，pydantic 会在构造时抛 ValidationError，错误信息直接指出字段名。
    """

    model_config = SettingsConfigDict(
        # 优先 cwd 下的 .env，其次项目根的 .env（与 docker-compose 的 ../.env 一致）。
        env_file=[str(Path.cwd() / ".env"), str(_PROJECT_ROOT / ".env")],
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── 运行环境 ──
    app_env: str = Field(default="development")
    tz: str = Field(default="Asia/Shanghai")

    # ── 安全（必填）──
    secret_key: str = Field(
        ...,  # 必填：缺失即启动失败
        description="JWT 签名密钥，部署用 openssl rand -hex 32 生成",
    )
    jwt_access_minutes: int = Field(default=30)
    jwt_refresh_days: int = Field(default=14)
    admin_password_hash: str = Field(
        ...,  # 必填：单用户管理员密码的 argon2 哈希
        description="管理员密码 argon2 哈希，不存明文",
    )
    totp_secret: str = Field(
        ...,  # 必填：TOTP 二步密钥（base32）
        description="TOTP 二步验证密钥（base32）",
    )
    # ★ 主人 2026-09-20 指示：两步验证暂时关闭（未来按需启用）——服务器 .env 设 TOTP_REQUIRED=false
    totp_required: bool = Field(default=True, description="登录是否强制校验 TOTP；false = 仅密码登录")

    # ── 数据 ──
    db_path: str = Field(default="./data/alongside.db")
    backup_dir: str = Field(default="./data/backups")
    backup_keep_days: int = Field(default=30)

    # ── CORS ──
    cors_allow_origins: str = Field(default="*")

    # ── 日志 ──
    log_level: str = Field(default="INFO")
    log_dir: str = Field(default="./data/logs")

    # ── 限流 ──
    rate_limit_login_per_min: int = Field(default=5)
    rate_limit_general_per_min: int = Field(default=300)

    # ── 幂等缓存时长（小时，默认 24h）──
    idempotency_ttl_hours: int = Field(default=24)

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_allow_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def cors_allow_credentials(self) -> bool:
        # CORS 规范：通配符 `*` 不得与凭据并用 —— 浏览器会直接拒绝，而 Starlette 会退化成
        # "回显任意 Origin 并放行凭据"，等于向所有站点开放 cookie 级请求。本项目鉴权走
        # Authorization 头（非 cookie），无需凭据型 CORS；确需凭据请显式列出来源。
        return self.cors_allow_origins.strip() != "*"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局唯一配置实例（带缓存）。

    构造失败（缺必填项）会向上抛出 pydantic ValidationError，
    由 create_app 捕获并转成清晰启动错误。
    """
    # 说明：三个安全字段声明为"必填"（`Field(...)`），但它们的值由 pydantic-settings
    # 在**运行时**从 .env / 环境变量注入，mypy 看不到这一层，因此这里对 call-arg 做一次
    # 定向忽略。这不是掩盖缺陷：真缺值时 pydantic 会在运行时报 ValidationError，
    # 由 create_app 捕获并转成清晰的启动错误。
    return Settings()  # type: ignore[call-arg]


def settings_as_dict() -> dict[str, Any]:
    """脱敏后的配置快照（用于 /readyz，绝不外泄密钥）。"""
    s = get_settings()
    return {
        "app_env": s.app_env,
        "tz": s.tz,
        "jwt_access_minutes": s.jwt_access_minutes,
        "jwt_refresh_days": s.jwt_refresh_days,
        "rate_limit_login_per_min": s.rate_limit_login_per_min,
        "rate_limit_general_per_min": s.rate_limit_general_per_min,
    }
