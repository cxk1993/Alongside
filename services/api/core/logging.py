"""JSON 结构化日志 + 按天轮转 + 脱敏。

字段固定：ts / level / logger / trace_id / module / msg / extra。
★ 铁律：token、密码、cookie、Authorization 头自动打码，绝不落盘明文。
"""
from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

# 当前请求 trace_id（由 middleware 注入），日志里跟着走。
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

# 敏感字段名：命中即整值打码。
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)^(password|passwd|pwd|secret|token|access_token|refresh_token|"
    # purity-ok: beecount_pass 是上游服务的密码字段名，脱敏必需，不能删
    r"authorization|cookie|set_cookie|psk|api_key|apikey|beecount_pass|"
    r"cf_api_token)$"
)
# 自由文本里可能出现的令牌形态：
#   - `Bearer <jwt>`：保留 "Bearer"，丢掉令牌
#   - `password=xxx` / `token: xxx` / `psk = xxx`：保留字段名与分隔符，丢掉值
# 用命名分组是为了**精确地只替换值**——早期版本用 `m.group(0).split()[0]` 想"保留字段名"，
# 但对 `password=hunter2` 这种无空格形态，`split()[0]` 就是整串，导致密钥原样落盘。
# 这类 bug 被 tests/test_kernel.py 的脱敏测试抓出来，别再退回那种写法。
_TOKEN_TEXT_RE = re.compile(
    r"(?i)(?P<bearer>Bearer\s+)[A-Za-z0-9\-._~+/]+=*"
    r"|(?P<key>password|pwd|secret|token|api_key|psk)(?P<sep>\s*[=:]\s*)\S+"
)


def _mask_token(m: re.Match[str]) -> str:
    bearer = m.group("bearer")
    if bearer is not None:
        return f"{bearer}***REDACTED***"
    return f"{m.group('key')}{m.group('sep')}***REDACTED***"


def redact_text(text: str) -> str:
    """把自由文本里的令牌形态打码（保留字段名，只抹掉值）。"""
    if not text:
        return text
    return _TOKEN_TEXT_RE.sub(_mask_token, text)


def redact_obj(obj: Any) -> Any:
    """递归打码：敏感 key 整值替换；字符串值扫描令牌。"""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SENSITIVE_KEY_RE.match(k):
                out[k] = "***REDACTED***"
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(redact_obj(x) for x in obj)
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


class JsonFormatter(logging.Formatter):
    def __init__(self, app_name: str = "alongside") -> None:
        super().__init__()
        self.app_name = app_name

    def format(self, record: logging.LogRecord) -> str:
        extra = getattr(record, "extra_fields", None) or {}
        extra = redact_obj(extra)
        msg = redact_text(str(record.getMessage()))
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": trace_id_var.get(),
            "module": getattr(record, "module_field", "") or record.name,
            "msg": msg,
            "extra": extra,
        }
        if record.exc_info:
            payload["extra"]["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ExtraAdapter(logging.LoggerAdapter):
    """允许 log.info("msg", extra={...}) 把额外字段带进 JSON。"""

    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        extra = kwargs.get("extra") or {}
        # 不和 logging 内建冲突的字段收集到 extra_fields
        record_extra = dict(extra)
        kwargs["extra"] = {"extra_fields": record_extra}
        return msg, kwargs


_loggers: dict[str, ExtraAdapter] = {}


def get_logger(name: str) -> ExtraAdapter:
    if name in _loggers:
        return _loggers[name]
    logger = logging.getLogger(name)
    adapter = ExtraAdapter(logger, {})

    class _ModuleFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if not hasattr(record, "module_field"):
                record.module_field = name
            return True

    logger.addFilter(_ModuleFilter())
    _loggers[name] = adapter
    return adapter


_configured = False


def configure_logging(
    *,
    level: str = "INFO",
    log_dir: str = "./data/logs",
    app_name: str = "alongside",
) -> None:
    """配置根日志：文件（按天轮转，保留 14 天）+ 标准错误。

    幂等：重复调用只配置一次。
    """
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(level.upper())
    # 清掉可能重复的 handler
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = JsonFormatter(app_name)

    # 标准错误（开发期直接看）
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(fmt)
    root.addHandler(stderr)

    # 文件：按天轮转，保留 14 天
    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            filename=str(Path(log_dir) / "alongside.log"),
            when="midnight",
            interval=1,
            backupCount=14,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:  # 日志写不了绝不能拖垮启动
        get_logger("kernel").warning(
            "日志文件初始化失败，仅输出到 stderr", extra={"reason": str(exc)}
        )
