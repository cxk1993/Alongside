"""注册表适配器（T18 ★ 核心）：把「能力字符串 + api.base」机械摊平成工具映射表。

★ 本文件是唯一判据的物理载体：
    加一个工具 = 某插件 manifest 里多一行 provides 字符串，本文件与 mcp 模块
    其他文件**一个字都不用改**。

零业务词保证：
  - 源码里没有 events/notes/finance 等任何业务字面量；
  - 只有「split('.') 取段 + 末段查动词表 + 简单加 s」这类结构逻辑；
  - 不 import 任何业务插件 —— 数据全部来自 T14 注册表读出的 manifest 字符串。

映射规则（任务卡 T18 §B，机械、零额外声明）：
  tool name = provides.replace('.', '_')     calendar.event.write → calendar_event_write
  method    = 末段动词查表                    write/create→POST  read/list/search/free/get→GET
                                              update→PUT  delete/remove→DELETE
  path      = {api.base}/{倒数第二段}s        calendar.event.write + /api/v1/calendar
                                              → POST /api/v1/calendar/events
  scope     = 首段:末段                       calendar:write
"""
from __future__ import annotations

from dataclasses import dataclass

from core.plugins.manager import get_plugin_manager

# 动词 → HTTP 方法（HTTP 语义，非业务词）。这是结构推导允许的唯一"懂一点"。
_METHOD_BY_VERB: dict[str, str] = {
    "read": "GET",
    "list": "GET",
    "search": "GET",
    "free": "GET",
    "get": "GET",
    "write": "POST",
    "create": "POST",
    "update": "PUT",
    # ★ 2026-09-27（主人「把挂路径参数的整类端点修通」）：
    #   此前**没有任何动词映射 PATCH** ⇒ 满地的 PATCH 端点（改待办 / 改文档 /
    #   改课 / 改日程…）在工具面上天然不可达。补一条 `patch`。
    "patch": "PATCH",
    # ★ bidiu 2026-11-21（B2 agents 闭环）：任务派发 / 回报是 POST 子路由
    #   （/tasks/{id}/dispatch、/tasks/{id}/report，路径经 manifest api.tools 显式映射）。
    #   此前动词表没有这两个词 ⇒ manifest 声明了 provides 却被 derive_tool 静默丢弃，
    #   AI 能建任务却没有任何工具让它跑起来。
    "dispatch": "POST",
    "report": "POST",
    # ★ bidiu 2026-11-21（B1 提案层）：签字/作废/撤销是 POST 子路由
    #   （/proposals/{id}/approve|reject|undo，路径经 manifest api.tools 显式映射）。
    "approve": "POST",
    "reject": "POST",
    "undo": "POST",
    "delete": "DELETE",
    "remove": "DELETE",
}

# 写类动词集合（审计用：落在集合里的 tool 调用要落 audit_log）。
WRITE_VERBS: frozenset[str] = frozenset(
    {"write", "create", "update", "patch", "delete", "remove", "dispatch", "report", "approve", "reject", "undo"}
)
#（ISSUE-008 方案 A，2026-09-20）不规则/语义命名路由的显式映射不在本文件——
# 在各插件 manifest 的 api.tools（resource → 路由段，声明则优先，未声明走机械
# 推导）。显式数据归插件声明、机制归本文件，源码保持零业务词；上一版的
# _PATH_OVERRIDES 例外表已迁入 dashboard/finance/web/review/calendar 的 manifest。


@dataclass(frozen=True)
class ToolMapping:
    """一条 provides 字符串摊平后的工具映射。"""

    name: str  # calendar_event_write
    verb: str  # write（末段动词原文）
    method: str  # POST
    path: str  # /api/v1/calendar/events
    scope: str  # calendar:write
    plugin_id: str  # 提供方插件 id
    description: str  # 机械生成的人类可读描述


def derive_tool(
    provides: str,
    api_base: str,
    plugin_id: str,
    tool_routes: dict[str, str] | None = None,
) -> ToolMapping | None:
    """单条能力字符串 → 工具映射。不合法则返回 None（跳过，不猜）。

    纯函数：不碰注册表，便于表驱动单测。
    tool_routes：manifest api.tools 的显式映射（resource → 路由段），
    命中则优先（ISSUE-008 方案 A）；未声明 resource 走机械「resource+s」推导。
    """
    parts = provides.split(".")
    # 契约 pattern 保证 >=3 段（domain.resource.verb）；防御性再查一次。
    if len(parts) < 3:
        return None
    domain, resource, verb = parts[0], parts[-2], parts[-1]
    method = _METHOD_BY_VERB.get(verb)
    if method is None:
        # 未知动词：跳过而不是瞎猜方法 —— 宁可少暴露，不可错转发。
        return None
    name = "_".join(parts)
    scope = f"{domain}:{verb}"
    # ★ 2026-09-27：路由键**先查细粒度 `resource.verb`，再回落 `resource`**。
    #   为什么：同一个 resource 上常挂多个端点（docs 的 node 有 read/write + patch/delete，
    #   路径还不一样），只按 resource 查会撞车。旧 manifest 一律只有 resource 键
    #   ⇒ 回落路径完全不变，零影响（test_derive_tool_* 表驱动守着）。
    explicit = (tool_routes or {}).get(f"{resource}.{verb}")
    if explicit is None:
        explicit = (tool_routes or {}).get(resource)
    base = api_base.rstrip("/")
    if explicit is None:
        path = f"{base}/{resource}s"
    elif explicit.startswith(base):
        path = explicit  # 完整路径（脚本与实现兼容两种形态）
    else:
        path = f"{base}{explicit}"  # 相对 router 段（规范形态，如 /entries）
    description = (
        f"MCP 工具：调用 {api_base} 的 {resource} {verb} 能力（域 {domain}，"
        f"来源插件 {plugin_id}）。入参 payload 为请求体（GET 时转查询参数）。"
    )
    return ToolMapping(
        name=name, verb=verb, method=method, path=path, scope=scope,
        plugin_id=plugin_id, description=description,
    )


def build_tool_map() -> list[ToolMapping]:
    """读 T14 注册表 → 所有 enabled 插件的 provides 全部摊平。

    每次调用即时重新发现（PluginManager 的口径）：磁盘上 manifest 多一行，
    下一次 tools/list 就能看到 —— 不需要本模块做任何登记。
    """
    manager = get_plugin_manager()
    # list_plugins 给 enabled / valid；discover 给完整 manifest（含 api.base）。
    states: dict[str, dict] = {}
    for item in manager.list_plugins():
        if item.get("valid") and item.get("id"):
            states[item["id"]] = item

    out: list[ToolMapping] = []
    for info in manager.discover().plugins:
        st = states.get(info.id)
        # 口径统一（2026-09-27 · 主人件①）：无状态行 = 非 third-party 默认启用；
        # third-party 从未动过 = 未启用，不暴露工具——防「工具看得见、路由 404」
        # 的幽灵工具（countdown 404 根因之一）。与 PluginManager.list_plugins 及
        # core/app.py 启动恢复三方同一规则（原注释「与 PluginManager 口径一致」
        # 在旧行为下并不成立，属注释自证式口径漂移，随本刀纠正）。
        enabled = st.get("enabled") if st is not None else info.source != "third-party"
        if not enabled:
            continue
        api = info.manifest.get("api") or {}
        api_base = api.get("base", "")
        if not api_base:
            continue  # 没有 REST 基址的插件，工具无处转发
        tool_routes = api.get("tools") or {}
        for cap in info.manifest.get("provides", []) or []:
            tool = derive_tool(cap, api_base, info.id, tool_routes)
            if tool is not None:
                out.append(tool)
    # 稳定排序：客户端展示与测试断言都受益。
    out.sort(key=lambda t: t.name)
    return out


def find_tool(name: str) -> ToolMapping | None:
    """按工具名查映射（tools/call 用）。"""
    for tool in build_tool_map():
        if tool.name == name:
            return tool
    return None


def is_write_tool(tool: ToolMapping) -> bool:
    """是否写类工具（动词落在写类集合 → 调用后要落审计）。"""
    return tool.verb in WRITE_VERBS
