"""ai_chat · LLM 网关适配（可插拔）。

V1：stub 明确「未配置」不假成功；
接 model-gateway 时实现 complete()，带 tools 注入与 finish_reason 分支。
"""
from __future__ import annotations

from typing import Any, Protocol

# 注：ChatTurn 以鸭子类型协作（避免包内相对导入在独立加载时失败）


class LLMGateway(Protocol):
    def complete(self, system: str, turns: list[Any], tools: list[dict[str, Any]]) -> Any: ...


class StubGateway:
    def complete(self, system: str, turns: list[Any], tools: list[dict[str, Any]]) -> Any:
        from .chat_loop import ChatTurn

        return ChatTurn(
            "assistant",
            text="AI 网关未配置（model-gateway）。已就绪的 system 长度="
            + str(len(system))
            + "，白名单工具="
            + str(len(tools)),
        )


class EchoGateway:
    """联调用：不调真 LLM，回显最后一句（前端/循环可测）。"""

    def complete(self, system: str, turns: list[Any], tools: list[dict[str, Any]]) -> Any:
        from .chat_loop import ChatTurn

        last = next((t.text for t in reversed(turns) if getattr(t, "role", "") == "user"), "")
        return ChatTurn("assistant", text=f"echo:{last}")


class ModelGateway:
    """接 model-gateway（OpenAI 兼容 tool calling）。

    环境：AI_CHAT_BASE_URL / AI_CHAT_API_KEY / AI_CHAT_MODEL
    超时 30s；失败抛给循环侧（不假成功）。
    """

    def complete(self, system: str, turns: list[Any], tools: list[dict[str, Any]]) -> Any:
        import json

        import httpx

        from core.config import read_setting

        from .chat_loop import ChatTurn

        base = (read_setting("AI_CHAT_BASE_URL", "") or "").rstrip("/")
        key = read_setting("AI_CHAT_API_KEY", "") or ""
        model = read_setting("AI_CHAT_MODEL", "deepseek-v4-flash") or "deepseek-v4-flash"
        if not base:
            return ChatTurn("assistant", text="AI_CHAT_BASE_URL 未配置（model-gateway）")
        msgs: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for t in turns:
            role = getattr(t, "role", "")
            if role == "user":
                msgs.append({"role": "user", "content": t.text})
            elif role == "assistant" and getattr(t, "tool_name", None):
                # ★ M1：assistant **发起** tool_call 的那一条必须显式带 tool_calls，
                #   否则后面的 role:"tool" 消息无所依附 ⇒ 严格网关
                #   `messages with role 'tool' must be a response to a preceding
                #   message with 'tool_calls'` 400。
                #   content 用 None（OpenAI 规范：带 tool_calls 的 assistant 可为 null）。
                msgs.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": t.tool_call_id or t.tool_name,
                                "type": "function",
                                "function": {
                                    "name": t.tool_name,
                                    "arguments": json.dumps(
                                        t.tool_args or {}, ensure_ascii=False
                                    ),
                                },
                            }
                        ],
                    }
                )
            elif role == "assistant":
                msgs.append({"role": "assistant", "content": t.text})
            elif role == "tool":
                # ★ M1：tool_call_id 用模型返回的 tc.id（不再是工具名），
                #   且必须与上面 assistant 那条的 id 逐字相同。
                msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": t.tool_call_id or t.tool_name or "t",
                        "content": json.dumps(t.tool_result or {}, ensure_ascii=False)[:2000],
                    }
                )
        body: dict[str, Any] = {"model": model, "messages": msgs}
        if tools:
            body["tools"] = tools
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=30.0)
        r.raise_for_status()
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tcs = msg.get("tool_calls") or []
        if tcs:
            tc = tcs[0]
            fn = tc.get("function") or {}
            args = fn.get("arguments") or "{}"
            try:
                parsed = json.loads(args) if isinstance(args, str) else args
            except Exception:
                parsed = {}
            # ★ M1：带上模型返回的 tc.id —— 回填 tool 结果时必须用它，
            #   用工具名当 id 会被严格网关判为「对不上号的 tool 响应」。
            return ChatTurn(
                "assistant",
                tool_name=fn.get("name"),
                tool_args=parsed if isinstance(parsed, dict) else {},
                tool_call_id=tc.get("id"),
                # ★ 并行调用只执行第一个（V1 4 工具面不变）；剩下的**记数**，
                #   由循环写进 audit —— 静默丢调用是最难排查的那类缺陷。
                dropped_tool_calls=max(0, len(tcs) - 1),
            )
        return ChatTurn("assistant", text=str(msg.get("content") or ""))


def get_gateway() -> LLMGateway:
    from core.config import read_setting

    mode = (read_setting("AI_CHAT_GATEWAY", "stub") or "stub").strip().lower()
    if mode == "echo":
        return EchoGateway()
    if mode in ("model", "model-gateway", "openai"):
        return ModelGateway()
    return StubGateway()
