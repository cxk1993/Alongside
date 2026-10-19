"""★ 从模块 OpenAPI 提取"精确工具参数"（astrbot 2026-09-26 · 主人「改进可改进点」）

**为什么**：原先 `tools/list` 的 `inputSchema` 只有个通用 `payload` 对象
（`additionalProperties: true`）—— AI **不知道能传什么字段**，只能"猜"
（实证：pi 查倒计时时"试了三种方式"）。

**怎么做**：内核已提供**模块级 openapi**（`/api/{module_id}/openapi.json`，
见 core/app.py 乙案）。本模块据此提取每个端点的参数，
生成**具名属性**的 inputSchema；**拉不到则降级回通用 payload**（绝不报错）。

★ 2026-09-27 补刀（云昔 · 主人「一口气做到最好」）：
  上一版只解决了**一半** —— 查询参数（`parameters`）正常，**请求体全部退化**。
  根因：FastAPI 生成的 `requestBody.schema` 不是内联对象，而是
  `{"$ref": "#/components/schemas/X"}`（Pydantic 继承会再套 `allOf`，
  `Optional[T]` 会套 `anyOf`）；旧实现直接读 `.properties` → 恒为空 dict
  → 该端点无 props → 降级成通用 payload。
  **实证**：15 个「写」工具**全部**退化（`todo_item_write` / `calendar_event_write`
  / `push_send_write` …），读工具正常（它们用查询参数）—— 等于"所有 AI 写
  Alongside 时都在盲猜字段名"，正是当初要治的那个病，只治了一半。
  本版新增 `_deref()`：解析本地 `$ref`（含 `allOf` 合并、`anyOf`/`oneOf` 取非空分支、
  环形引用防护），并把 `enum` / `default` / 数组 `items` 一并带出，让 AI 看得懂合法取值。

设计纪律：
- **进程内缓存**（按 module_id）：MCP 工具列表会被频繁拉取，不能每次都打 HTTP；
- **短超时 + 全兜底**：任何异常 → 返回 {} → 调用方降级；
- **不 import 业务插件**（与 registry_adapter 同纪律，只走 HTTP）；
- **schema 解析绝不外联**：只解析 `#/` 文档内引用，外部文件/URL 引用一律不追。

★ 2026-09-27 二次补刀（主人「把挂路径参数的整类端点修通」）：
  **路径参数改为暴露且标必填**。旧版遇 `in: path` 直接 `continue` 跳过 —— 因为
  当时桥接层不做替换，暴露了也没用。现在 `forward._substitute_path_params()`
  会把它代入 URL，于是这里必须让 AI 看得见这个坑（否则调用必然 422）。
  **两半合起来**，路径参数型端点（改文档/改待办/打卡…）才真正可达。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .forward import internal_base

log = logging.getLogger("mcp.openapi_params")

_TIMEOUT = 3.0
# 解 $ref 的最大层数（正常 1~2 层；设上限只为防环形引用把递归打穿）
_MAX_REF_DEPTH = 8
# module_id -> {(METHOD, path): params_schema}
_CACHE: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}


def _type_map(t: str | None) -> dict[str, Any]:
    """OpenAPI 类型 → JSON Schema 片段（保守，未知按 string）。"""
    if t == "integer":
        return {"type": "integer"}
    if t == "number":
        return {"type": "number"}
    if t == "boolean":
        return {"type": "boolean"}
    if t == "array":
        return {"type": "array", "items": {"type": "string"}}
    if t == "object":
        return {"type": "object"}
    return {"type": "string"}


def _lookup_ref(spec: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """解析**文档内**引用（`#/components/schemas/X`）；解析不到返回 None。

    ★ 只认 `#/` 开头 —— 外部文件/URL 引用在本项目不出现，硬去追反而可能
      产生外部网络请求（安全纪律：schema 解析绝不外联）。
    JSON Pointer 的 `~1` / `~0` 转义按规范还原。
    """
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node: Any = spec
    for raw in ref[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return None
    return node if isinstance(node, dict) else None


def _is_null_branch(spec: dict[str, Any], node: Any, depth: int = 0) -> bool:
    """分支是否表示"空"（Pydantic 的 `Optional[T]` → `anyOf[T, {"type": "null"}]`）。"""
    if not isinstance(node, dict):
        return True
    return _deref(spec, node, depth).get("type") == "null"


def _deref(spec: dict[str, Any], node: Any, depth: int = 0) -> dict[str, Any]:
    """解析 `$ref` / `allOf` / `anyOf` / `oneOf`，返回**可以直接读属性**的 schema。

    ★ 这是 2026-09-27 的补刀核心：FastAPI 生成的 `requestBody.schema` 通常不是
      内联对象，而是 `{"$ref": "#/components/schemas/X"}`（Pydantic 继承时还会套
      `allOf`，`Optional[T]` 会套 `anyOf`）。旧实现直接读 `.properties` 拿到空 dict，
      于是**所有写工具**的 inputSchema 都退化成通用 payload —— AI 只能猜字段名。
      本函数把这几层都拆开；**解析不到就原样返回，绝不抛**（调用方靠"没有
      properties"自然降级回通用 payload）。
    """
    if not isinstance(node, dict):
        return {}
    if depth > _MAX_REF_DEPTH:
        return node

    ref = node.get("$ref")
    if isinstance(ref, str):
        target = _lookup_ref(spec, ref)
        if target is None:
            return {k: v for k, v in node.items() if k != "$ref"}
        merged = _deref(spec, target, depth + 1)
        # OpenAPI 3.1：$ref 的**同级字段**合法且优先（如覆盖 description）
        for k, v in node.items():
            if k != "$ref":
                merged[k] = v
        return merged

    if isinstance(node.get("allOf"), list):
        out: dict[str, Any] = {k: v for k, v in node.items() if k != "allOf"}
        for sub in node["allOf"]:
            for k, v in _deref(spec, sub, depth + 1).items():
                if k == "properties" and isinstance(v, dict):
                    merged_props = dict(out.get("properties") or {})
                    merged_props.update(v)
                    out["properties"] = merged_props
                elif k == "required" and isinstance(v, list):
                    out["required"] = list(
                        dict.fromkeys(list(out.get("required") or []) + v)
                    )
                else:
                    out.setdefault(k, v)
        return out

    for key in ("anyOf", "oneOf"):
        branches = node.get(key)
        if not isinstance(branches, list) or not branches:
            continue
        real = [b for b in branches if not _is_null_branch(spec, b, depth + 1)]
        if not real:
            continue  # 全是 null 分支 → 保持原样
        out = {k: v for k, v in node.items() if k != key}
        for k, v in _deref(spec, real[0], depth + 1).items():
            out.setdefault(k, v)
        return out

    return node


def _frag(spec: dict[str, Any], raw: Any) -> dict[str, Any]:
    """单个属性 → JSON Schema 片段（带类型 / 说明 / 可选值 / 默认值 / 数组元素）。"""
    node = _deref(spec, raw)
    t = node.get("type")
    if not t:
        if "properties" in node:
            t = "object"
        elif "items" in node:
            t = "array"
    frag = _type_map(t)
    desc = node.get("description")
    if desc:
        frag["description"] = desc
    enum = node.get("enum")
    if isinstance(enum, list) and enum:
        frag["enum"] = enum  # 让 AI 看见合法取值，而不是猜
    if node.get("default") is not None:
        frag["default"] = node["default"]
    if frag.get("type") == "array":
        frag["items"] = _type_map(_deref(spec, node.get("items") or {}).get("type"))
    return frag


def _props_from_operation(spec: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    """单个 operation → `{"properties": {...}, "required": [...]}`（纯函数，便于直测）。"""
    props: dict[str, Any] = {}
    required: list[str] = []
    # ① 查询 / 路径参数
    for prm in op.get("parameters") or []:
        prm = _deref(spec, prm)  # 参数本身也可能是 $ref
        name = prm.get("name")
        if not name:
            continue
        frag = _frag(spec, prm.get("schema") or {})
        if prm.get("in") == "path":
            # ★ 2026-09-27：路径参数**要暴露**。旧版直接跳过（当时桥接层不做替换），
            #   结果 AI 压根不知道 {id} 这个坑拿什么填。现在桥接层会把它代入 URL
            #（见 forward._substitute_path_params），于是带进 schema 并标必填 ——
            #   两半合起来，路径参数型端点才真正可达。
            frag["description"] = (
                (frag.get("description") or "") + "（路径参数，会代入 URL）"
            )
            props[name] = frag
            required.append(name)  # 路径参数恒必填
            continue
        props[name] = frag
        if prm.get("required"):
            required.append(name)
    # ② 请求体（JSON）—— 必须先解 $ref
    body = ((op.get("requestBody") or {}).get("content") or {}).get("application/json")
    if isinstance(body, dict):
        sch = _deref(spec, body.get("schema") or {})
        for k, v in (sch.get("properties") or {}).items():
            props[k] = _frag(spec, v)
        required.extend(k for k in (sch.get("required") or []) if k in props)
    entry: dict[str, Any] = {"properties": props}
    if props and required:
        entry["required"] = sorted(set(required))
    # ★ 2026-09-28（主人「所有 MCP 工具 / API 接口都带上注释和解释，防 AI 抓瞎」）：
    #   把该端点的**作者注释**一并带出。来源就是 route 函数的 docstring ——
    #   FastAPI 把它暴露成 `summary`（首行）与 `description`（全文）。
    #   实测：全平台 **250/250 个端点都有**中文 docstring，但工具面**从来没读过**它。
    #   `description` 通常已包含首行，故优先取它，避免重复。
    # ⚠️ 别信 `summary`：FastAPI 在**没有 docstring** 时会用函数名自动生成英文标题
    #（`def update_item` → "Update Item"）—— 那不是解释，是标题。
    #   实测踩过：内核首版优先 summary，于是 todo_item_patch 的工具描述变成了
    #   "Update Item"，比原来的机械句**更误导**。
    #   故：只认 `description`（docstring 派生）；退一步只认**含中文**的 summary；
    #   都不满足就留空 → 工具面回落机械句（宁可朴素，不可冒充）。
    doc = (op.get("description") or "").strip()
    if not doc:
        _s = (op.get("summary") or "").strip()
        doc = _s if any("\u4e00" <= ch <= "\u9fff" for ch in _s) else ""
    if doc:
        entry["doc"] = doc[:600] + ("…" if len(doc) > 600 else "")
    return entry


def _load_module(module_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    """拉某模块的 openapi，摊平成 {(METHOD, path): {字段名: schema}}。失败返回 {}。"""
    if module_id in _CACHE:
        return _CACHE[module_id]

    out: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        url = f"{internal_base()}/api/{module_id}/openapi.json"
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url)
        if resp.status_code == 200:
            spec = resp.json()
            for path, ops in (spec.get("paths") or {}).items():
                if not isinstance(ops, dict):
                    continue
                for method, op in ops.items():
                    if not isinstance(op, dict):
                        continue
                    entry = _props_from_operation(spec, op)
                    # ★ 即便**没有任何参数**（如 GET /tags），只要端点有注释也要入表 ——
                    #   否则"无参端点"的工具描述就拿不到解释（正好是最需要解释的一类）。
                    if entry.get("properties") or entry.get("doc"):
                        out[(method.upper(), path)] = entry
    except Exception as exc:  # noqa: BLE001 —— 拉不到就降级，绝不阻断
        log.info("模块 %s 的 openapi 拉取失败（降级为通用 payload）：%s", module_id, exc)

    _CACHE[module_id] = out
    return out


def params_for(module_id: str, method: str, path: str) -> dict[str, Any]:
    """取某端点的参数描述。返回 {} 表示"无信息，请降级"。"""
    try:
        return _load_module(module_id).get((method.upper(), path), {})
    except Exception:  # noqa: BLE001
        return {}


def clear_cache() -> None:
    """清缓存（测试用）。"""
    _CACHE.clear()
