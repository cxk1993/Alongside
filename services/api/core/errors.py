"""统一异常 -> RFC7807 problem+json。

所有业务异常都继承 AppError；create_app 注册异常处理器，
把异常转成带 trace_id 的 application/problem+json 响应。
失败必须明确（不许静默吞掉），错误信息要指向根因。
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

DEFAULT_TYPE = "about:blank"


class AppError(Exception):
    """内核统一异常基类。

    status_code: HTTP 状态码
    title: 人话标题（RFC7807 title）
    detail: 具体原因（可包含是哪个模块/哪个字段）
    """

    status_code: int = 500
    title: str = "Internal Server Error"

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        title: str | None = None,
        type_uri: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code
        if title is not None:
            self.title = title
        self.type_uri = type_uri or DEFAULT_TYPE
        self.extra = extra or {}
        super().__init__(detail)


class ValidationError(AppError):
    status_code = 422
    title = "Validation Error"


class NotFoundError(AppError):
    status_code = 404
    title = "Not Found"


class UnauthorizedError(AppError):
    status_code = 401
    title = "Unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    title = "Forbidden"


class ConflictError(AppError):
    status_code = 409
    title = "Conflict"


class RateLimitError(AppError):
    status_code = 429
    title = "Too Many Requests"


class ServiceUnavailableError(AppError):
    status_code = 503
    title = "Service Unavailable"


class ManifestError(AppError):
    """模块 manifest 校验失败：必须指出是哪个模块、哪个字段。"""

    status_code = 500
    title = "Module Manifest Invalid"


class MigrationError(AppError):
    """插件迁移执行失败：迁移脚本是插件自己的代码，它抛什么内核管不了，
    但对外必须是**一个**应用级错误 —— 否则调用方要区分「迁移写错了」
    和「内核坏了」，而后者根本不是。原始异常类型与信息带在消息里，不丢线索。
    """

    status_code = 500
    title = "Plugin Migration Failed"


def _trace_id(request: Request) -> str:
    return str(getattr(request.state, "trace_id", "") or "")


def problem(
    *,
    status_code: int,
    title: str,
    detail: str,
    request: Request,
    type_uri: str = DEFAULT_TYPE,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """构造 RFC7807 响应。必带 trace_id（响应头 + body）。"""
    trace_id = _trace_id(request)
    body: dict[str, Any] = {
        "type": type_uri,
        "title": title,
        "status": status_code,
        "detail": detail,
        "trace_id": trace_id,
    }
    if extra:
        body["extra"] = extra
    resp = JSONResponse(status_code=status_code, content=body)
    resp.headers["Content-Type"] = "application/problem+json"
    if trace_id:
        resp.headers["X-Trace-Id"] = trace_id
    return resp


def handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    # Starlette 的 add_exception_handler 签名要求异常参数为 Exception，
    # 故在此收窄一次类型（Starlette 只会用它注册的那个类调进来，行为不变）。
    if not isinstance(exc, AppError):
        return handle_unexpected(request, exc)
    return problem(
        status_code=exc.status_code,
        title=exc.title,
        detail=exc.detail,
        request=request,
        type_uri=exc.type_uri,
        extra=exc.extra or None,
    )


def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        return handle_unexpected(request, exc)
    # 404（路由不存在）也要返回 problem+json，而不是 Starlette 的 HTML
    return problem(
        status_code=exc.status_code,
        title=getattr(exc, "title", None) or "Error",
        detail=str(exc.detail),
        request=request,
    )


def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        return handle_unexpected(request, exc)
    # FastAPI 参数校验失败 -> 422 problem+json
    errors = exc.errors()
    detail = "请求参数校验失败"
    extra = {"errors": [_trim_err(e) for e in errors]}
    return problem(
        status_code=422,
        title="Validation Error",
        detail=detail,
        request=request,
        extra=extra,
    )


def _trim_err(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "loc": [str(p) for p in e.get("loc", [])],
        "msg": e.get("msg", ""),
        "type": e.get("type", ""),
    }


def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """兜底：未知异常 -> 500，但必须记录原始错误（不许静默）。"""
    from core.logging import get_logger

    log = get_logger("kernel.error")
    log.error(
        "未捕获异常",
        extra={"trace_id": _trace_id(request), "exc": repr(exc)},
    )
    return problem(
        status_code=500,
        title="Internal Server Error",
        detail="服务器内部错误，请凭 trace_id 排查",
        request=request,
    )
