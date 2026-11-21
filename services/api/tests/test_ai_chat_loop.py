"""ai-chat 三处断链的回归测试（M1 tool_call 格式 / M2 多轮 history / M4 过程事件）。

对应审计 `audit-ai-chat.md` 的 P0-2 / P0-3 / P1-2。四类断言，各自守一条**会静默失效**的契约：

  ① **序列化形状**：assistant 发起调用那条必须带 `tool_calls`，tool 那条的
     `tool_call_id` 必须与它**逐字相同**。两者任一错，严格 OpenAI 兼容网关 400；
     而假 LLM 的单元测试**看不出来**（它们不经过 gateway 序列化）——所以这里必须
     把真实 `ModelGateway.complete` 的请求体抓下来看。
  ② **多轮 history**：第二轮 llm 拿到的 turns 里必须能看到第一轮的 assistant+tool。
     不测这条，「_SESSIONS 只写不读」可以一直绿着上线。
  ③ **emits 双向一致**：manifest 声明的 = 代码里真实 publish 的（AST 静态对账），
     外加一条**负例**——未声明的 topic 发布必须被内核当场拒（fail-closed）。
     只测「能发出去」是不够的：那证明不了门禁还在。
  ④ **事件不含数据全文**：过程可见 ≠ 把用户健康记录/待办正文推给前端。
     摘要必须截断，数据行（items/events/records 的值）一律不出现在 payload 里。

模块目录名带连字符（`ai-chat`），必须 importlib 动态加载。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_MOD_DIR = Path(__file__).resolve().parents[1] / "modules" / "ai-chat"
_MANIFEST = json.loads((_MOD_DIR / "manifest.json").read_text(encoding="utf-8"))


def _load(mod_name: str, file_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, _MOD_DIR / file_name)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # ★ 必须先进 sys.modules 再 exec：@dataclass 解析 __module__ 时要能查到本模块
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_as_package():
    """把模块目录注册成包再按子模块加载。

    ★ 为什么需要：`gateway.py` 内部写的是 `from .chat_loop import ChatTurn`（包内相对导入）。
      裸 `spec_from_file_location("x", "gateway.py")` 没有父包 ⇒ `ImportError:
      attempted relative import with no known parent package`。造一个 __path__ 指向
      模块目录的合成包，就能在不改生产代码的前提下测真实 gateway。
    """
    pkg_name = "m1_ai_chat_pkg"
    if pkg_name in sys.modules:
        return sys.modules[pkg_name], sys.modules[f"{pkg_name}.gateway"]
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(_MOD_DIR)]  # type: ignore[attr-defined]
    sys.modules[pkg_name] = pkg
    for name in ("chat_loop", "gateway", "tools", "router"):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{name}", _MOD_DIR / f"{name}.py"
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{name}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, name, mod)
    return pkg, sys.modules[f"{pkg_name}.gateway"]


_pkg, gateway = _load_as_package()
chat_loop = _pkg.chat_loop

#: 把 ai-chat 自己的 emits 登记进全局总线（幂等）。
#: 生产路径由 `create_app` / `mount_plugin` 的发现层调 `declare_publisher` 完成；
#: 这里显式登记一次，避免「单跑本文件时没有别的测试建过 app」导致 publish 静默失败
#: （chat_loop 的 try/except 会吞掉 UndeclaredPublication —— 那会让事件断言红得莫名其妙）。
#: 注意这**不放宽** fail-closed：登记的是 manifest 自己的三条，发别的 topic 照样抛。
from core.events import event_bus  # noqa: E402

event_bus.declare_publisher(_MANIFEST)

ChatTurn = chat_loop.ChatTurn
run_tool_call_loop = chat_loop.run_tool_call_loop

#: 测试用敏感串：若它出现在事件 payload 里，说明数据行被整包推给了前端。
SENSITIVE = "复查血糖（敏感内容不该进事件）"


# ─────────────────────── ① tool_call 序列化形状（M1）───────────────────────


def _loop_one_tool_round(call_id: str | None = "call_abc123") -> Any:
    """跑一轮「要工具 → 综答」，返回 ChatSession（turns 里含 assistant+tool 配对）。"""

    def llm(turns):
        if not any(t.role == "tool" for t in turns):
            return ChatTurn(
                "assistant",
                tool_name="todo_item_read",
                tool_args={"status": "todo"},
                tool_call_id=call_id,
            )
        return ChatTurn("assistant", text="你有 1 条待办")

    tools_map = {
        "todo_item_read": lambda a: {
            "ok": True,
            "count": 1,
            "items": [{"text": SENSITIVE, "due_at": None, "done": False}],
        }
    }
    return run_tool_call_loop("我有什么待办", llm, tools_map, session_id="t-m1")


def _capture_model_request(monkeypatch, turns: list[ChatTurn]) -> dict[str, Any]:
    """用真实 ModelGateway 组请求体，但不发网络（monkeypatch httpx.post）。"""
    import httpx

    captured: dict[str, Any] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        captured["url"] = url
        captured["body"] = json
        return _Resp()

    # ModelGateway 在函数体内 import read_setting，故打 core.config 的符号即可
    import core.config as core_config

    monkeypatch.setattr(core_config, "read_setting", lambda k, d="": {
        "AI_CHAT_BASE_URL": "http://127.0.0.1:9999/v1",
        "AI_CHAT_API_KEY": "sk-test",
        "AI_CHAT_MODEL": "m",
    }.get(k, d))
    monkeypatch.setattr(httpx, "post", fake_post)

    gateway.ModelGateway().complete("SYS", turns, [])
    return captured


def test_assistant_tool_call_message_is_emitted(monkeypatch) -> None:
    """assistant 侧必须有 tool_calls 消息（缺它 ⇒ role:tool 无依附 ⇒ 严格网关 400）。"""
    s = _loop_one_tool_round()
    msgs = _capture_model_request(monkeypatch, s.turns)["body"]["messages"]

    call_msgs = [m for m in msgs if m["role"] == "assistant" and m.get("tool_calls")]
    assert len(call_msgs) == 1, f"assistant.tool_calls 消息缺失或重复：{msgs}"
    call = call_msgs[0]["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "todo_item_read"
    assert json.loads(call["function"]["arguments"]) == {"status": "todo"}


def test_tool_message_id_matches_assistant_tool_call(monkeypatch) -> None:
    """tool 侧 tool_call_id 必须等于 assistant 那条的 id，且**不是**工具名。"""
    s = _loop_one_tool_round(call_id="call_abc123")
    msgs = _capture_model_request(monkeypatch, s.turns)["body"]["messages"]

    call_id = next(
        m["tool_calls"][0]["id"] for m in msgs if m["role"] == "assistant" and m.get("tool_calls")
    )
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert call_id == "call_abc123"
    assert tool_msg["tool_call_id"] == call_id
    assert tool_msg["tool_call_id"] != "todo_item_read", "又退回用工具名当 id 了"


def test_tool_message_follows_its_assistant_call(monkeypatch) -> None:
    """顺序也要对：role:tool 必须紧跟在带 tool_calls 的 assistant 之后。"""
    s = _loop_one_tool_round()
    msgs = _capture_model_request(monkeypatch, s.turns)["body"]["messages"]
    idx_tool = next(i for i, m in enumerate(msgs) if m["role"] == "tool")
    prev = msgs[idx_tool - 1]
    assert prev["role"] == "assistant" and prev.get("tool_calls"), (
        f"tool 消息前面不是发起调用的 assistant：{prev}"
    )


def test_missing_tc_id_still_pairs_up(monkeypatch) -> None:
    """网关不给 tc.id（宽松实现）时，assistant/tool 两侧仍必须共用同一个兜底 id。"""
    s = _loop_one_tool_round(call_id=None)  # 显式传 None
    msgs = _capture_model_request(monkeypatch, s.turns)["body"]["messages"]
    call_id = next(
        m["tool_calls"][0]["id"] for m in msgs if m["role"] == "assistant" and m.get("tool_calls")
    )
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert call_id and tool_msg["tool_call_id"] == call_id


# ─────────────────────── ② 多轮 history（M2）───────────────────────


def test_second_round_sees_first_round_turns() -> None:
    """第二轮 llm 收到的 turns 必须含第一轮的 user/assistant(tool_calls)/tool。"""
    first = _loop_one_tool_round()
    seen: list[list[ChatTurn]] = []

    def llm(turns):
        seen.append(list(turns))
        return ChatTurn("assistant", text="第二轮回答")

    run_tool_call_loop(
        "那明天的呢", llm, {"todo_item_read": lambda a: {"ok": True}}, history=list(first.turns)
    )
    turns = seen[0]
    roles = [t.role for t in turns]
    # 第一轮：user / assistant(发起调用) / tool / assistant(综答)；第二轮：再追加 user
    assert roles == ["user", "assistant", "tool", "assistant", "user"], roles
    assert turns[1].tool_name == "todo_item_read" and turns[1].tool_call_id
    assert turns[2].tool_call_id == turns[1].tool_call_id
    assert turns[2].tool_result and turns[2].tool_result["count"] == 1
    assert turns[3].text == "你有 1 条待办"


def test_history_none_keeps_old_behaviour() -> None:
    """不传 history = 老行为（向后兼容，单轮）。"""
    seen: list[list[ChatTurn]] = []

    def llm(turns):
        seen.append(list(turns))
        return ChatTurn("assistant", text="hi")

    run_tool_call_loop("你好", llm, {})
    assert [t.role for t in seen[0]] == ["user"]


def test_history_is_not_mutated_in_place() -> None:
    """history 是快照：loop 不该往调用方的 list 里追加（router 的 _SESSIONS 靠这条不串味）。"""
    first = _loop_one_tool_round()
    snapshot = list(first.turns)
    before = len(snapshot)

    run_tool_call_loop("再来", lambda t: ChatTurn("assistant", text="ok"), {}, history=snapshot)
    assert len(snapshot) == before, "loop 把新 turn 写进了调用方传进来的 history 列表"


# ─────────────────────── ③ emits 声明 ↔ 发布 双向一致（M4）───────────────────────


def _published_topics() -> set[str]:
    """AST 扫 chat_loop.py 里的真实 publish 调用，解析出字面量 topic。"""
    tree = ast.parse((_MOD_DIR / "chat_loop.py").read_text(encoding="utf-8"))
    consts: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt, val = node.targets[0], node.value
            if (
                isinstance(tgt, ast.Name)
                and isinstance(val, ast.Constant)
                and isinstance(val.value, str)
            ):
                consts[tgt.id] = val.value
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "publish"):
            continue
        topic_node = node.args[0] if node.args else None
        for kw in node.keywords:
            if kw.arg == "topic":
                topic_node = kw.value
        assert topic_node is not None, "publish 调用没写 topic"
        if isinstance(topic_node, ast.Constant):
            out.add(str(topic_node.value))
        elif isinstance(topic_node, ast.Name):
            assert topic_node.id in consts, f"topic 变量 {topic_node.id} 不是模块级字面量常量"
            out.add(consts[topic_node.id])
        else:  # 动态 topic ⇒ 静态门禁判不了，这里直接判红
            raise AssertionError(f"publish 的 topic 是动态构造：{ast.unparse(topic_node)}")
    return out


def test_declared_emits_equals_published_topics() -> None:
    """双向：声明里有但代码不发的（空承诺）与代码发了但没声明的（隐形发布）都算错。"""
    declared = set(_MANIFEST["emits"])
    published = _published_topics()
    assert declared == published, {
        "声明了但从不发布": sorted(declared - published),
        "发布了但没声明": sorted(published - declared),
    }
    assert declared, "emits 不许是空列表——过程事件是这一轮的核心产出"


def test_publish_calls_use_module_id_as_source() -> None:
    """source 必须等于模块 id：运行时内核按 source 查 manifest.emits，写错就发不出去。"""
    tree = ast.parse((_MOD_DIR / "chat_loop.py").read_text(encoding="utf-8"))
    sources = [
        kw.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "publish"
        for kw in node.keywords
        if kw.arg == "source" and isinstance(kw.value, ast.Constant)
    ]
    assert sources, "没找到带 source 的 publish 调用"
    assert set(sources) == {_MANIFEST["id"]}, sources


def test_undeclared_topic_is_rejected_by_kernel() -> None:
    """★ 负例：未声明的 topic 必须被内核 fail-closed 拒绝（门禁还活着，不是摆设）。"""
    from core.events import EventBus, UndeclaredPublication

    bus = EventBus()
    bus.declare_publisher(_MANIFEST)
    # 声明过的三条都能发
    for topic in _MANIFEST["emits"]:
        assert bus.publish(topic, {}, source=_MANIFEST["id"])["topic"] == topic
    # 没声明的一条必须当场炸，且不进 history
    before = len(bus._history)
    with pytest.raises(UndeclaredPublication):
        bus.publish("ai-chat.tool.secret", {}, source=_MANIFEST["id"])
    assert len(bus._history) == before, "被拒的发布不该留下痕迹（fail-closed = 不发出去）"


def test_loop_publishes_three_phases() -> None:
    """一轮带工具的对话 → started / tool.called / completed 各一条，同一 correlation_id。"""
    from core.events import event_bus

    captured: list[dict[str, Any]] = []

    def hook(ev: dict[str, Any]) -> None:
        if ev.get("source") == "ai-chat":
            captured.append(ev)

    event_bus.add_kernel_hook(hook)
    try:
        _loop_one_tool_round()
    finally:
        event_bus.remove_kernel_hook(hook)

    topics = [e["topic"] for e in captured]
    assert topics == [
        "ai-chat.turn.started",
        "ai-chat.tool.called",
        "ai-chat.turn.completed",
    ], topics
    assert len({e["correlation_id"] for e in captured}) == 1, "三个事件没串在同一条 correlation 上"
    assert captured[0]["payload"]["session_id"] == "t-m1"
    assert captured[1]["payload"]["tool"] == "todo_item_read"
    assert captured[2]["payload"]["audit"] == [
        "call:todo_item_read",
        "final",
    ]


# ─────────────────────── ④ 事件 payload 不含数据全文（M4）───────────────────────


def _events_of_round() -> list[dict[str, Any]]:
    from core.events import event_bus

    captured: list[dict[str, Any]] = []

    def hook(ev: dict[str, Any]) -> None:
        if ev.get("source") == "ai-chat":
            captured.append(ev)

    event_bus.add_kernel_hook(hook)
    try:
        _loop_one_tool_round()
    finally:
        event_bus.remove_kernel_hook(hook)
    return captured


def test_event_payload_has_no_data_rows() -> None:
    """工具返回的数据行（这里是待办正文）绝不许出现在事件里。"""
    blob = json.dumps(_events_of_round(), ensure_ascii=False)
    assert SENSITIVE not in blob, "事件把工具返回的数据行整包推给前端了"


def test_event_payload_keeps_meta_only() -> None:
    """要保留「拿到了什么」这件事，但只到元信息粒度：ok / count / 字段名。"""
    tool_ev = next(e for e in _events_of_round() if e["topic"] == "ai-chat.tool.called")
    digest = tool_ev["payload"]["result_summary"]
    assert digest["ok"] is True and digest["count"] == 1
    assert "items" in digest["fields"]  # 字段名可以给，值不行
    assert "items" not in digest, "数据行本身不许进 payload"


def test_summaries_are_truncated() -> None:
    """入参摘要截到 200 字：prompt 里塞进来的长文本不许整段进事件。"""
    long_arg = "很长的参数" * 200

    def llm(turns):
        if not any(t.role == "tool" for t in turns):
            return ChatTurn(
                "assistant",
                tool_name="todo_item_read",
                tool_args={"status": long_arg},
                tool_call_id="call_long",
            )
        return ChatTurn("assistant", text="done")

    from core.events import event_bus

    captured: list[dict[str, Any]] = []

    def hook(ev: dict[str, Any]) -> None:
        if ev.get("source") == "ai-chat":
            captured.append(ev)

    event_bus.add_kernel_hook(hook)
    try:
        run_tool_call_loop("长参数", llm, {"todo_item_read": lambda a: {"ok": True}}, session_id="t")
    finally:
        event_bus.remove_kernel_hook(hook)

    tool_ev = next(e for e in captured if e["topic"] == "ai-chat.tool.called")
    summary = tool_ev["payload"]["args_summary"]
    assert len(summary) <= chat_loop.SUMMARY_LIMIT, len(summary)


def test_publish_failure_does_not_break_the_conversation() -> None:
    """观测失败不许拖垮对话：事件总线炸了，回复照常返回。"""
    from core.events import event_bus

    def boom(*a, **kw):
        raise RuntimeError("总线坏了")

    original = event_bus.publish
    event_bus.publish = boom  # type: ignore[method-assign]
    try:
        s = _loop_one_tool_round()
    finally:
        event_bus.publish = original  # type: ignore[method-assign]

    assert s.audit == ["call:todo_item_read", "final"]
    assert s.turns[-1].text == "你有 1 条待办"


# ─────────────────────── ⑤ 并行调用不再静默丢弃（M1 附带）───────────────────────


def test_dropped_parallel_tool_calls_are_audited() -> None:
    """网关一次给两个 tool_calls 时，只执行第一个，但丢掉的**必须留痕**。"""
    reply = ChatTurn(
        "assistant",
        tool_name="todo_item_read",
        tool_args={},
        tool_call_id="call_1",
        dropped_tool_calls=2,
    )

    def llm(turns):
        if not any(t.role == "tool" for t in turns):
            return reply
        return ChatTurn("assistant", text="ok")

    s = run_tool_call_loop("并行", llm, {"todo_item_read": lambda a: {"ok": True}})
    assert "dropped:2" in s.audit


# ─────────────────────── ⑥ 路由层：_SESSIONS 真的被读了（M2 的病灶点）───────────────


def test_router_feeds_previous_turns_into_second_request(monkeypatch) -> None:
    """★ 这是 M2 的原始 bug 位置：`_SESSIONS` 只写不读。

    直接调 `post_message`（跳过 HTTP 与鉴权），断言**第二次请求**里 llm 收到的
    turns 含第一次的 user + assistant，且 session_id 不同则互不串味。
    """
    router_mod = _pkg.router
    seen: list[list[Any]] = []

    class _Gw:
        def complete(self, system, turns, tools):
            seen.append(list(turns))
            return ChatTurn("assistant", text=f"第{len(seen)}次回答")

    monkeypatch.setattr(router_mod, "make_tools", lambda request: {})
    monkeypatch.setattr(router_mod, "get_gateway", lambda: _Gw())
    router_mod._SESSIONS.clear()

    class _Body:
        def __init__(self, text: str, sid: str) -> None:
            self.text = text
            self.session_id = sid

    first = router_mod.post_message(_Body("第一句", "s-m2"), None)
    second = router_mod.post_message(_Body("第二句", "s-m2"), None)

    assert first["reply"] == "第1次回答"
    assert second["reply"] == "第2次回答"
    assert [t.text for t in seen[1] if t.role == "user"] == ["第一句", "第二句"], (
        f"第二轮没看到第一轮：{[t.text for t in seen[1]]}"
    )

    # 另一个会话必须从空白开始
    router_mod.post_message(_Body("别的会话", "s-other"), None)
    assert [t.text for t in seen[2] if t.role == "user"] == ["别的会话"]
    router_mod._SESSIONS.clear()


def test_router_history_is_capped(monkeypatch) -> None:
    """history 有上限：会话攒到 100 轮也不会把 100 轮全塞给 LLM。"""
    router_mod = _pkg.router
    sizes: list[int] = []

    class _Gw:
        def complete(self, system, turns, tools):
            sizes.append(len(turns))
            return ChatTurn("assistant", text="ok")

    monkeypatch.setattr(router_mod, "make_tools", lambda request: {})
    monkeypatch.setattr(router_mod, "get_gateway", lambda: _Gw())
    router_mod._SESSIONS.clear()

    class _Body:
        def __init__(self, text: str, sid: str) -> None:
            self.text = text
            self.session_id = sid

    for i in range(60):
        router_mod.post_message(_Body(f"第{i}句", "s-cap"), None)

    assert max(sizes) <= router_mod._MAX_HISTORY_TURNS + 1, max(sizes)
    assert len(router_mod._SESSIONS["s-cap"].turns) == 120  # 会话本身不截断，只截回灌
    router_mod._SESSIONS.clear()
