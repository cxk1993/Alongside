"""MCP server 核心：把 JSON-RPC 消息分发到 tools。

★ 传输形态说明（与官方 SDK 默认形态的差异备案）：
  任务卡建议直接用官方 SDK 的 StreamableHTTPServerTransport。实测它需要
  持有 app lifespan 控制权（`async with session_manager.run()`），而插件框架
  （ModuleRegistry.mount → include_router）不向插件提供 lifespan —— 结构性冲突。
  故本模块实现 MCP Streamable HTTP 规范允许的 **stateless request-response 子集**：
    - POST：JSON-RPC 请求 → application/json 单响应（客户端 Accept 本就含它）
    - notifications/* → 202 Accepted（无 body）
    - GET（SSE 推送流）/ DELETE（会话终止）→ 405（规范明文允许："server that
      does not offer SSE ... MUST return 405"）
  协议互操作由**官方 MCP SDK 客户端**在 verify 脚本里实测握手保证。
  本模块零业务词：工具映射全部经 registry_adapter 动态读出。

工具执行（tools/call）：
  1. registry_adapter.find_tool(name) —— 未知工具 → JSON-RPC error -32602
  2. ensure_scope(pat, tool.scope) —— 越权 → HTTP 403（内核 problem+json）
  3. forward(tool.method, tool.path, payload) —— 内部 HTTP，不 import 插件
  4. 写类工具 → audit_call / 拒绝 → audit_denied
"""
from __future__ import annotations

import json
from typing import Any

from core.errors import ForbiddenError

from . import audit
from .auth import PatContext, ensure_scope
from .external import call_external, external_tools, find_external
from .forward import forward
from .registry_adapter import build_tool_map, find_tool, is_write_tool

SERVER_NAME = "alongside-mcp"
SERVER_VERSION = "0.1.0"
# 我们支持的协议版本（核心三原语在各版本间一致；握手时回客户端所请求的版本）。
_SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
_DEFAULT_PROTOCOL_VERSION = "2025-06-18"

# JSON-RPC 错误码（标准）
_ERR_METHOD_NOT_FOUND = -32601
_ERR_INVALID_PARAMS = -32602
_ERR_INTERNAL = -32603


def _rpc_error(code: int, message: str, msg_id: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _rpc_result(result: dict[str, Any], msg_id: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _fallback_schema() -> dict[str, Any]:
    """降级 schema：单个 payload 对象（GET 时转查询参数）。"""
    return {
        "type": "object",
        "properties": {
            "payload": {
                "type": "object",
                "additionalProperties": True,
                "description": (
                    "请求内容。写类工具=JSON 请求体；读类工具=查询参数（如 filter/from/to）。"
                ),
            }
        },
    }


def _tool_description(tool: Any = None) -> str:
    """工具描述：**端点的作者注释优先**（★ 2026-09-28 · 主人）。

    旧描述是机械句「MCP 工具：调用 /api/v1/x 的 y write 能力（域 y…）」——
    AI 由此**看不出这工具干什么、参数什么意思**，44 个工具描述长得几乎一样，
    只好靠猜（主人的原话：**"所有东西的参数全部都一样而抓瞎"**）。

    而各模块的 route 函数**本来都写了中文 docstring**（实测 250/250 端点都有），
    只是从来没被工具面用起来。本函数把它接上；**拉不到注释就回落机械句**（绝不报错）。
    """
    if tool is None:
        return ""
    try:
        from .openapi_params import params_for

        info = params_for(tool.plugin_id, tool.method, tool.path)
        doc = (info.get("doc") or "").strip()
        if doc:
            return f"【{tool.method} {tool.path}】{doc}"
    except Exception:  # noqa: BLE001 —— 任何异常都回落，绝不阻断 tools/list
        pass
    return getattr(tool, "description", "") or ""


def _tool_schema(tool: Any = None) -> dict[str, Any]:
    """★ 2026-09-26 改进（astrbot · 主人）：**按工具给出精确 inputSchema**。

    原先所有工具共用"一个通用 payload" → AI 不知道能传什么字段，只能猜
    （实证：pi 查倒计时时"试了三种方式"）。
    现从**模块级 openapi**（内核乙案 · `/api/{module_id}/openapi.json`）提取该端点的
    具名参数（`tag` / `status` / `limit` …），生成"看得懂"的 schema；
    **拉不到则原样降级**为通用 payload（绝不报错、绝不阻断 tools/list）。
    """
    if tool is not None:
        try:
            from .openapi_params import params_for

            info = params_for(tool.plugin_id, tool.method, tool.path)
            props = info.get("properties") or {}
            if props:
                schema: dict[str, Any] = {
                    "type": "object",
                    "properties": dict(props),
                    "additionalProperties": True,
                    "description": (
                        f"调用 {tool.path}（{tool.method}）。"
                        + (
                            "写类工具：字段作为 JSON 请求体；"
                            if tool.method != "GET"
                            else "读类工具：字段作为查询参数；"
                        )
                        + "也可整体放在 payload 对象里。"
                    ),
                }
                if info.get("required"):
                    schema["x-required-hint"] = info["required"]
                # 兼容"payload 包裹"用法
                schema["properties"]["payload"] = {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "上述字段的包裹形态（与顶层字段二选一）。",
                }
                return schema
        except Exception:  # noqa: BLE001 —— 任何异常都降级
            pass
    return _fallback_schema()


def _handle_initialize(msg: dict[str, Any], msg_id: Any) -> dict[str, Any]:
    params = msg.get("params") or {}
    client_version = params.get("protocolVersion")
    version = (
        client_version
        if client_version in _SUPPORTED_PROTOCOL_VERSIONS
        else _DEFAULT_PROTOCOL_VERSION
    )
    return _rpc_result(
        {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        },
        msg_id,
    )


def _handle_tools_list(msg_id: Any) -> dict[str, Any]:
    tools = [
        {
            "name": t.name,
            "description": _tool_description(t),
            "inputSchema": _tool_schema(t),
        }
        for t in build_tool_map()
    ]
    # 外部 MCP 源（BeeCount 等）：未配置则空，不挡本地工具。
    for t in external_tools():
        tools.append(
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": _tool_schema(),
            }
        )
    return _rpc_result({"tools": tools}, msg_id)


def _handle_tools_call(msg: dict[str, Any], msg_id: Any, pat: PatContext) -> dict[str, Any]:
    params = msg.get("params") or {}
    name = str(params.get("name", ""))
    arguments = params.get("arguments") or {}
    ext = find_external(name)
    if ext is not None:
        try:
            ensure_scope(pat, ext.scope)
        except ForbiddenError:
            audit.audit_denied(
                token_prefix=pat.token_prefix, tool_name=name, reason=f"缺 scope {ext.scope}"
            )
            raise
        payload_ext = arguments.get("payload") if isinstance(arguments, dict) else None
        if not isinstance(payload_ext, dict):
            payload_ext = {}
        ok, body = call_external(ext, payload_ext)
        if ext.is_write:
            audit.audit_call(
                token_prefix=pat.token_prefix,
                tool_name=ext.name,
                method="MCP",
                path=ext.upstream,
                payload=payload_ext,
            )
        text = json.dumps(body, ensure_ascii=False, default=str)
        return _rpc_result(
            {"content": [{"type": "text", "text": text}], "isError": not ok},
            msg_id,
        )
    tool = find_tool(name)
    if tool is None:
        # 未知工具也记一条拒绝（是谁在试探工具边界，可追溯）。
        audit.audit_denied(
            token_prefix=pat.token_prefix, tool_name=name or "（空）", reason="未知工具"
        )
        return _rpc_error(_ERR_INVALID_PARAMS, f"未知工具：{name}", msg_id)

    # scope 越权 → HTTP 403 problem+json（任务卡 §D.5 明确口径），同时落审计。
    try:
        ensure_scope(pat, tool.scope)
    except ForbiddenError:
        audit.audit_denied(
            token_prefix=pat.token_prefix, tool_name=name, reason=f"缺 scope {tool.scope}"
        )
        raise

    payload = arguments.get("payload") if isinstance(arguments, dict) else None
    if not isinstance(payload, dict):
        payload = None
    # ★ 2026-09-28（主人「每一个工具都实际测验过」，实测抓到的真 bug）：
    #   工具 schema 里声明的是**具名参数**（from / to / tag / limit …），但本函数历史上
    #   **只读 `payload`** ⇒ AI 照 schema 把字段传在顶层时，会被**静默丢弃**：
    #   实测 `calendar_event_read` 传 {"from":…, "to":…} → 422「query from Field required」。
    #   **描述说能传、传了没用**，比没描述更害 AI。这里把顶层具名参数**并进 payload**，
    #   于是「具名直传」与「payload 包裹」两种写法都成立（旧写法零影响）。
    if isinstance(arguments, dict):
        extra = {k: v for k, v in arguments.items() if k != "payload"}
        if extra:
            payload = {**(payload or {}), **extra}

    status, body = forward(tool.method, tool.path, payload)
    ok = 200 <= status < 300

    if is_write_tool(tool):
        audit.audit_call(
            token_prefix=pat.token_prefix,
            tool_name=tool.name,
            method=tool.method,
            path=tool.path,
            payload=payload,
        )

    text = json.dumps(
        {"status": status, "body": body},
        ensure_ascii=False,
        default=str,
    )
    return _rpc_result(
        {
            "content": [{"type": "text", "text": text}],
            "isError": not ok,
        },
        msg_id,
    )


def handle_message(msg: dict[str, Any], pat: PatContext) -> dict[str, Any] | None:
    """处理一条 JSON-RPC 消息。

    返回响应 dict（请求）；notification 返回 None（→ 202）。
    """
    if msg.get("jsonrpc") != "2.0":
        return _rpc_error(_ERR_INVALID_PARAMS, "jsonrpc 字段必须为 \"2.0\"", msg.get("id"))
    method = str(msg.get("method", ""))
    msg_id = msg.get("id")

    # notification（无 id）按规范不回响应；notifications/* 一律 202。
    if msg_id is None or method.startswith("notifications/"):
        return None

    try:
        if method == "initialize":
            return _handle_initialize(msg, msg_id)
        if method == "ping":
            return _rpc_result({}, msg_id)
        if method == "tools/list":
            return _handle_tools_list(msg_id)
        if method == "tools/call":
            return _handle_tools_call(msg, msg_id, pat)
        return _rpc_error(_ERR_METHOD_NOT_FOUND, f"未知方法：{method}", msg_id)
    except ForbiddenError:
        raise  # 越权要走 HTTP 403，不在 JSON-RPC body 里掩饰
    except Exception as exc:  # noqa: BLE001 —— 转成 JSON-RPC 内部错误，不静默
        return _rpc_error(_ERR_INTERNAL, f"工具执行失败：{exc}", msg_id)
