"""BeeCount MCP 客户端（T08B · 本轮只读）。

## 为什么必须走 MCP（不是 REST）

实测档案《对接档案-外部系统实测.md》§1.0.1：
    GET /api/v1/read/ledgers  with Bearer bcmcp_...
    → HTTP 403 {"message":"PAT can only be used for MCP endpoints"}

BeeCount 的 PAT（`bcmcp_*`，scope `mcp:read` + `mcp:write`）**只能打 MCP 端点**。
ADR-0003 §2.6 已据此修正：对 BeeCount 必须走 MCP，不能设计成 REST 客户端。

## 端点与鉴权（档案 §1.0.2）

    POST {BEECOUNT_BASE_URL}/api/v1/mcp
    Authorization: Bearer $BEECOUNT_MCP_TOKEN
    Content-Type: application/json
    Accept: application/json, text/event-stream   ← Streamable HTTP

## 本轮铁律

- 只允许**读工具**（白名单 10 个）；delete / 写工具一律无入口
- `create_transaction` 留接口桩，默认禁用（T08B 不做写路径）
- **绝不碰 `/api/v1/sync/*`**（官方 App 多端协议，介入会与主人手机打架）
- 凭据只从环境变量读：`FINANCE_UPSTREAM` / `BEECOUNT_BASE_URL` / `BEECOUNT_MCP_TOKEN`
- Token 只驻留内存，不写日志、不出现在错误详情里

生产/本地同机约定：BeeCount 恒在 `127.0.0.1:8870`（manifest 权限也声明
`net:out:127.0.0.1`）。换环境只改 `BEECOUNT_BASE_URL` 一个变量。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from core.config import read_setting
from core.errors import ForbiddenError, ServiceUnavailableError

# ── 环境变量名（凭据只从这里读）──
ENV_UPSTREAM = "FINANCE_UPSTREAM"
ENV_BASE_URL = "BEECOUNT_BASE_URL"
ENV_MCP_TOKEN = "BEECOUNT_MCP_TOKEN"

# ── 环境变量读取 ──
# 凭据/开关（BEECOUNT_* / DASHBOARD_* / REVIEW_* 等）只声明在 .env，
# 而 pydantic-settings 不会把 .env 注入进程 os.environ。
# 统一走 core.config.read_setting（os.environ 优先，缺失回退项目根 .env），
# 该 helper 的定义集中在 core/config.py，避免每个模块各抄一份解析逻辑。

UPSTREAM_MOCK = "mock"
UPSTREAM_MCP = "mcp"

# MCP 端点相对路径（档案 §1.0.2：来自 BeeCount /app/src/main.py:175）
MCP_PATH = "/api/v1/mcp"

# ★ 白名单：本轮 Alongside 侧唯一允许调用的 BeeCount 工具（档案 §1.0.2 读工具 10 个）
READ_TOOLS: frozenset[str] = frozenset(
    {
        "list_ledgers",
        "get_active_ledger",
        "list_transactions",
        "get_transaction",
        "list_categories",
        "list_accounts",
        "list_tags",
        "list_budgets",
        "get_ledger_stats",
        "get_analytics_summary",
    }
)

# 写工具 / 其它工具：本轮一律不暴露。
# create_transaction 留有接口桩（默认禁用），其余无任何入口。
FORBIDDEN_TOOLS: frozenset[str] = frozenset(
    {
        "create_transaction",
        "create_transactions",
        "update_transaction",
        "delete_transaction",
        "create_category",
        "update_budget",
        "parse_and_create_from_text",
        "search",
    }
)

# 本轮同步用到的两个读工具
TOOL_LEDGER_STATS = "get_ledger_stats"
TOOL_ANALYTICS = "get_analytics_summary"

_CLIENT_INFO = {"name": "lifeos-finance", "version": "0.2.0"}
_PROTOCOL_VERSION = "2024-11-05"
_DEFAULT_TIMEOUT = 10.0
# meta / SSE 解析时防止异常大响应拖垮进程
_MAX_PARSE_BYTES = 2_000_000


class BeeCountMCPError(Exception):
    """MCP 通道错误基类。detail 不得包含 token。"""

    def __init__(self, detail: str, *, status_code: int = 503) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class BeeCountNotConfiguredError(BeeCountMCPError):
    """缺环境变量 / upstream 模式非法。调用方应转成 503 且明确报错。"""


class BeeCountToolForbiddenError(BeeCountMCPError):
    """工具不在只读白名单内（含 delete / 写工具 / 本轮禁用的 create_transaction）。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail, status_code=403)


class BeeCountProtocolError(BeeCountMCPError):
    """MCP 响应不是预期的 JSON-RPC / 工具结果结构。"""


class SupportsReadToolCall(Protocol):
    """sync 层依赖的最小读接口（真实 MCP client 与 mock 都实现它）。"""

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any: ...


def load_upstream_config() -> dict[str, Any]:
    """从环境变量读取 upstream 配置。不抛错，供 /beecount/source 汇报状态用。

    返回：
      upstream: "mock" | "mcp" | 其它原始串（非法值在 get_client 时再拒）
      base_url: BEECOUNT_BASE_URL（可能为空串）
      configured: mcp 模式下 base_url 与 token 是否都已配置
      token_present: token 是否非空（**不返回 token 本身**）
    """
    upstream = read_setting(ENV_UPSTREAM, UPSTREAM_MOCK).strip().lower() or UPSTREAM_MOCK
    base_url = read_setting(ENV_BASE_URL, "").strip()
    token = read_setting(ENV_MCP_TOKEN, "").strip()
    token_present = bool(token)
    # mock 离线可跑视为已配置；mcp 需 base_url + token 齐全
    configured = (bool(base_url) and token_present) if upstream == UPSTREAM_MCP else True
    return {
        "upstream": upstream,
        "base_url": base_url,
        "configured": configured,
        "token_present": token_present,
    }


def _require_mcp_credentials() -> tuple[str, str]:
    """mcp 模式取 (base_url, token)；缺任何一个都**明确报错**，不静默空数据。"""
    base_url = read_setting(ENV_BASE_URL, "").strip()
    token = read_setting(ENV_MCP_TOKEN, "").strip()
    if not base_url:
        raise BeeCountNotConfiguredError(
            f"FINANCE_UPSTREAM=mcp 但未配置 {ENV_BASE_URL}（形如 http://127.0.0.1:8870）"
        )
    if not token:
        raise BeeCountNotConfiguredError(
            f"FINANCE_UPSTREAM=mcp 但未配置 {ENV_MCP_TOKEN}（BeeCount PAT，scope 含 mcp:read）。"
            "请写入项目 .env，禁止写进代码/前端/git。"
        )
    return base_url, token


class BeeCountMCPClient:
    """BeeCount Streamable HTTP MCP 客户端（只读白名单）。

    协议：JSON-RPC 2.0 over POST {base}/api/v1/mcp。
    响应兼容 application/json 与 text/event-stream（SSE，取 data: 行）。
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url:
            raise BeeCountNotConfiguredError(f"缺少 {ENV_BASE_URL}")
        if not token:
            raise BeeCountNotConfiguredError(f"缺少 {ENV_MCP_TOKEN}")
        self._base_url = base_url.rstrip("/")
        # token 只驻留内存；禁止 repr / 日志输出
        self._token = token
        self._timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout, transport=transport)
        self._rpc_id = 0

    @property
    def mcp_url(self) -> str:
        return self._base_url + MCP_PATH

    @property
    def base_url(self) -> str:
        return self._base_url

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BeeCountMCPClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            # Streamable HTTP：同时接受 JSON 与 SSE
            "Accept": "application/json, text/event-stream",
        }

    @staticmethod
    def _parse_body(content_type: str, text: str) -> Any:
        """解析 MCP 响应体：纯 JSON 或 SSE（data: 行拼接）。"""
        raw = text
        if "text/event-stream" in content_type or text.lstrip().startswith("event:") or (
            "data:" in text[:200] and "\n\n" in text
        ):
            chunks: list[str] = []
            for line in text.splitlines():
                if line.startswith("data:"):
                    chunks.append(line[5:].strip())
            raw = "\n".join(chunks) if chunks else text
        if len(raw.encode("utf-8", "replace")) > _MAX_PARSE_BYTES:
            raise BeeCountProtocolError("MCP 响应过大，拒绝解析")
        raw = raw.strip()
        if not raw:
            raise BeeCountProtocolError("MCP 返回空响应体")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            # SSE 可能一个 data 帧一条 JSON；逐帧再试
            for piece in raw.splitlines():
                piece = piece.strip()
                if piece.startswith("{"):
                    try:
                        return json.loads(piece)
                    except json.JSONDecodeError:
                        continue
            raise BeeCountProtocolError(f"MCP 响应不是合法 JSON：{exc}") from exc

    @staticmethod
    def _unwrap_tool_result(result: Any) -> Any:
        """把 tools/call 的 result 解包成业务数据。

        BeeCount / 通用 MCP 常见形态：
          {content:[{type:"text",text:"<json>"}], structuredContent:{...}, isError:bool}
        兼容：result 本身已是 dict/list → 原样返回。
        """
        if result is None:
            return None
        if isinstance(result, list | str | int | float | bool):
            return result
        if not isinstance(result, dict):
            return result
        if result.get("isError"):
            msg = ""
            content = result.get("content")
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, dict):
                    msg = str(first.get("text") or first.get("content") or "")
            raise BeeCountProtocolError(msg or "BeeCount 工具返回 isError=true")
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                text = first.get("text")
                if isinstance(text, str):
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"text": text}
            # 多段 content：尽量拼 text
            texts = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
            if texts:
                joined = "\n".join(texts)
                try:
                    return json.loads(joined)
                except json.JSONDecodeError:
                    return {"text": joined}
        # 已经是业务形状
        return result

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """发一次 JSON-RPC 请求，返回 result（已解包 tools/call）。"""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or {},
        }
        try:
            resp = self._client.post(
                self.mcp_url,
                headers=self._headers(),
                json=payload,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            # 不要把 URL 上的潜在敏感信息放大；只报主机与异常类型
            raise BeeCountMCPError(
                f"BeeCount MCP 不可达（{self._base_url}）：{type(exc).__name__}"
            ) from exc

        if resp.status_code == 401:
            raise BeeCountMCPError(
                f"BeeCount MCP 鉴权失败（401）：请检查 {ENV_MCP_TOKEN} 是否有效/已吊销",
                status_code=502,
            )
        if resp.status_code == 403:
            raise BeeCountMCPError(
                "BeeCount MCP 拒绝访问（403）。注意：PAT 只能打 MCP 端点，不能打 REST。",
                status_code=502,
            )
        if resp.status_code >= 400:
            body_text = resp.text[:200]
            snippet = body_text.replace(self._token, "***") if self._token else body_text
            raise BeeCountMCPError(
                f"BeeCount MCP 返回 HTTP {resp.status_code}：{snippet}",
                status_code=502,
            )

        data = self._parse_body(resp.headers.get("content-type", ""), resp.text)
        if not isinstance(data, dict):
            raise BeeCountProtocolError("MCP JSON-RPC 响应根必须是对象")
        if "error" in data and data["error"]:
            err = data["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise BeeCountProtocolError(f"MCP JSON-RPC error：{msg}")
        result = data.get("result", data)
        if method == "tools/call":
            return self._unwrap_tool_result(result)
        return result

    # ── MCP 会话 ──
    def initialize(self) -> Any:
        """initialize 握手。返回 serverInfo / capabilities 等 result 内容。"""
        return self.rpc(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": dict(_CLIENT_INFO),
            },
        )

    def tools_list(self) -> list[str]:
        """tools/list → 工具名列表（测试用：应能看到档案记录的 18 个工具名）。"""
        result = self.rpc("tools/list", {})
        if not isinstance(result, dict):
            return []
        tools = result.get("tools")
        if not isinstance(tools, list):
            return []
        names: list[str] = []
        for t in tools:
            if isinstance(t, dict) and isinstance(t.get("name"), str):
                names.append(t["name"])
        return names

    # ── 只读工具（白名单）──
    @staticmethod
    def assert_tool_allowed(name: str) -> None:
        """不在只读白名单 → 明确 403 语义拒绝，绝不放行 delete/写。"""
        if name in READ_TOOLS:
            return
        if name in FORBIDDEN_TOOLS:
            raise BeeCountToolForbiddenError(
                f"BeeCount 工具 {name!r} 本轮禁用（T08B 只读：无 delete、无写路径、禁 sync/*）"
            )
        raise BeeCountToolForbiddenError(
            f"BeeCount 工具 {name!r} 不在只读白名单内，拒绝调用"
        )

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.assert_tool_allowed(name)
        return self.rpc("tools/call", {"name": name, "arguments": arguments or {}})

    def list_ledgers(self, **kwargs: Any) -> Any:
        return self.call_tool("list_ledgers", kwargs)

    def get_active_ledger(self, **kwargs: Any) -> Any:
        return self.call_tool("get_active_ledger", kwargs)

    def list_transactions(self, **kwargs: Any) -> Any:
        return self.call_tool("list_transactions", kwargs)

    def get_transaction(self, **kwargs: Any) -> Any:
        return self.call_tool("get_transaction", kwargs)

    def list_categories(self, **kwargs: Any) -> Any:
        return self.call_tool("list_categories", kwargs)

    def list_accounts(self, **kwargs: Any) -> Any:
        return self.call_tool("list_accounts", kwargs)

    def list_tags(self, **kwargs: Any) -> Any:
        return self.call_tool("list_tags", kwargs)

    def list_budgets(self, **kwargs: Any) -> Any:
        return self.call_tool("list_budgets", kwargs)

    def get_ledger_stats(self, **kwargs: Any) -> Any:
        """账本统计 → finance_snapshot.total_asset 等字段的主来源。"""
        return self.call_tool(TOOL_LEDGER_STATS, kwargs)

    def get_analytics_summary(self, **kwargs: Any) -> Any:
        """收支汇总 + Top10 支出分类 → meta_json 主要内容。"""
        return self.call_tool(TOOL_ANALYTICS, kwargs)

    # ── 写路径：本轮禁用（接口桩）──
    def create_transaction(self, **_kwargs: Any) -> None:
        """接口桩：T08B **不实现** create_transaction 写路径，默认禁用。

        未来若开放，必须同时满足：白名单 + Idempotency-Key + audit_log；
        且仍禁止 delete_* / sync/*。当前调用一律 403 语义拒绝。
        """
        raise BeeCountToolForbiddenError(
            "create_transaction 本轮禁用（T08B 只读增补，未实现写路径）"
        )


class MockBeeCountClient:
    """离线 mock 上游：不访问网络。FINANCE_UPSTREAM=mock（默认）时使用。

    数据形状对齐档案里 BeeCount 读工具的语义（金额用整数分），
    保证 mock 模式测试全绿且不依赖真实外网。
    """

    def __init__(
        self,
        *,
        stats: dict[str, Any] | None = None,
        analytics: dict[str, Any] | None = None,
    ) -> None:
        self._stats = stats
        self._analytics = analytics
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def mcp_url(self) -> str:
        return "mock://beecount/api/v1/mcp"

    @property
    def base_url(self) -> str:
        return "mock://beecount"

    def close(self) -> None:
        return None

    def initialize(self) -> dict[str, Any]:
        return {
            "protocolVersion": _PROTOCOL_VERSION,
            "serverInfo": {"name": "BeeCount Cloud MCP (mock)", "version": "0.0.0-mock"},
            "capabilities": {},
        }

    def tools_list(self) -> list[str]:
        return sorted(READ_TOOLS | FORBIDDEN_TOOLS)

    @staticmethod
    def assert_tool_allowed(name: str) -> None:
        BeeCountMCPClient.assert_tool_allowed(name)

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.assert_tool_allowed(name)
        args = arguments or {}
        self.calls.append((name, dict(args)))
        if name == TOOL_LEDGER_STATS:
            return self.get_ledger_stats(**args)
        if name == TOOL_ANALYTICS:
            return self.get_analytics_summary(**args)
        if name == "list_ledgers":
            return [{"id": "mock-ledger-1", "name": "生活账本", "currency": "CNY"}]
        if name == "get_active_ledger":
            return {"id": "mock-ledger-1", "name": "生活账本", "currency": "CNY"}
        raise BeeCountToolForbiddenError(f"mock 未实现读工具 {name!r}")

    def get_ledger_stats(self, **_kwargs: Any) -> dict[str, Any]:
        if self._stats is not None:
            return dict(self._stats)
        return {
            "ledger_id": "mock-ledger-1",
            "name": "生活账本",
            "currency": "CNY",
            "balance_cents": 1_234_567,
            "income_cents": 200_000,
            "expense_cents": 86_543,
            "account_count": 3,
            "category_count": 12,
            "tag_count": 4,
        }

    def get_analytics_summary(self, **_kwargs: Any) -> dict[str, Any]:
        if self._analytics is not None:
            return dict(self._analytics)
        return {
            "period": "month",
            "income_cents": 200_000,
            "expense_cents": 86_543,
            "net_cents": 113_457,
            "top_expense_categories": [
                {"category": "餐饮", "expense_cents": 32_000},
                {"category": "交通", "expense_cents": 12_000},
                {"category": "居住", "expense_cents": 30_000},
            ],
        }

    def create_transaction(self, **_kwargs: Any) -> None:
        raise BeeCountToolForbiddenError(
            "create_transaction 本轮禁用（T08B 只读增补，未实现写路径）"
        )


def create_client(
    *,
    transport: httpx.BaseTransport | None = None,
    client: httpx.Client | None = None,
    stats: dict[str, Any] | None = None,
    analytics: dict[str, Any] | None = None,
) -> BeeCountMCPClient | MockBeeCountClient:
    """按 FINANCE_UPSTREAM 构造客户端。

    - mock（默认）→ MockBeeCountClient，离线可跑
    - mcp → BeeCountMCPClient；缺 BEECOUNT_BASE_URL / BEECOUNT_MCP_TOKEN **明确报错**
    """
    upstream = read_setting(ENV_UPSTREAM, UPSTREAM_MOCK).strip().lower() or UPSTREAM_MOCK
    if upstream == UPSTREAM_MOCK:
        return MockBeeCountClient(stats=stats, analytics=analytics)
    if upstream == UPSTREAM_MCP:
        base_url, token = _require_mcp_credentials()
        return BeeCountMCPClient(base_url, token, transport=transport, client=client)
    raise BeeCountNotConfiguredError(
        f"{ENV_UPSTREAM}={upstream!r} 非法，仅支持 {UPSTREAM_MOCK!r} | {UPSTREAM_MCP!r}"
    )


def to_app_error(exc: BeeCountMCPError) -> Exception:
    """把 MCP 客户端异常映射成内核 AppError（RFC7807）。"""
    if isinstance(exc, BeeCountToolForbiddenError):
        return ForbiddenError(exc.detail)
    return ServiceUnavailableError(exc.detail)


def call_with_client(
    fn: Callable[[SupportsReadToolCall], Any],
    *,
    client: SupportsReadToolCall | None = None,
) -> Any:
    """在指定 client 上执行 fn；未指定则按环境变量创建。

    异常统一映射成 AppError，供 router 直接抛出。
    """
    if client is not None:
        return fn(client)
    try:
        auto = create_client()
    except BeeCountMCPError as exc:
        raise to_app_error(exc) from exc
    try:
        return fn(auto)
    except BeeCountMCPError as exc:
        raise to_app_error(exc) from exc
    finally:
        close = getattr(auto, "close", None)
        if callable(close):
            close()
