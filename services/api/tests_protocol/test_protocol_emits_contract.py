"""发布端（emits）契约的回归测试。

★ 这里锁两件事，缺一不可：
  ① **真实仓库**里 48 条声明 ↔ 48 种发布一一对应（没有空承诺，也没有隐形发布）；
  ② **注入失效**时判定必须报红 —— 一道永远绿的门禁等于没上线。

★ 为什么这件事值得单独一个文件：
  `emits` 的运行时校验只在「跑到的代码」上生效。声明了不发布的 topic
  永远不会在执行期报错（它只是没被执行），所以**静态对账是唯一能发现它的手段**。
  而静态对账写错了（判据反了、扫漏了目录）不会有任何症状 —— 它只会一直绿。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
GATE = REPO / "services" / "api" / "scripts" / "check_protocol_consumption.py"


def _load():
    spec = importlib.util.spec_from_file_location("_consumption_gate", GATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _entry(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "declared": [],
        "published": {},
        "undeclared": [],
        "unpublished": [],
        "sourceErrors": [],
        "dynamic": [],
    }
    base.update(over)
    return base


def _pubs(modules: dict[str, Any], scanned: int = 1) -> dict[str, Any]:
    return {"modules": modules, "scannedFiles": scanned}


def test_real_repo_publishers_are_all_declared() -> None:
    """真实数据：每个模块「声明的」与「发布的」必须一一对应。"""
    gate = _load()
    pubs = gate.scan_publishers()
    violations, warnings = gate._emit_problems(pubs)

    assert not violations, f"发布端契约不成立：{violations}"

    # 内置 29 份 + 第三方 2 份；只扫内置会把契约的一半留给自觉
    assert pubs["scannedFiles"] > 100, "扫到的模块文件太少，扫描根是不是漏了"
    assert "pi-agent" in pubs["modules"], "第三方插件没进扫描范围（emits 是协议，不是内置约定）"
    assert "countdown" in pubs["modules"]
    assert not warnings, f"不该有静态判不了的动态发布点：{warnings}"


def test_every_module_declares_exactly_what_it_publishes() -> None:
    """逐模块核对：`emits` 集合 == 实际发布集合（双向，不看顺序）。"""
    gate = _load()
    pubs = gate.scan_publishers()
    mismatched: dict[str, tuple[list[str], list[str]]] = {}
    for mid, entry in pubs["modules"].items():
        declared = set(entry["declared"])
        published = set(entry["published"])
        if declared != published:
            mismatched[mid] = (sorted(declared - published), sorted(published - declared))
    assert not mismatched, f"这些模块的声明与发布不一致（空承诺 / 隐形发布）：{mismatched}"


def test_undeclared_publication_is_flagged() -> None:
    gate = _load()
    v, _w = gate._emit_problems(
        _pubs({"demo": _entry(published={"demo.pong": ["x.py:1"]}, undeclared=["demo.pong"])})
    )
    assert any("demo.pong" in x for x in v)


def test_unpublished_declaration_is_flagged() -> None:
    gate = _load()
    v, _w = gate._emit_problems(
        _pubs({"demo": _entry(declared=["demo.ping"], unpublished=["demo.ping"])})
    )
    assert any("demo.ping" in x for x in v)


def test_wrong_source_is_flagged() -> None:
    """发布时的 `source` 不是模块 id ⇒ 运行时会按错误的键查声明。"""
    gate = _load()
    v, _w = gate._emit_problems(
        _pubs({"web": _entry(sourceErrors=["x.py:1 source='web_entry' ≠ 模块 id 'web'"])})
    )
    assert any("source" in x for x in v)


def test_dynamic_topic_downgrades_to_warning() -> None:
    """有动态构造的 topic 时，「声明了没发布」只能降级为警告 —— 静态判不了就别判。"""
    gate = _load()
    v, w = gate._emit_problems(
        _pubs({"demo": _entry(declared=["demo.x"], unpublished=["demo.x"], dynamic=["y.py:9 topic=build()"])})
    )
    assert not v and any("demo.x" in x for x in w)


def test_empty_scan_is_flagged() -> None:
    gate = _load()
    v, _w = gate._emit_problems({"modules": {}, "scannedFiles": 0})
    assert any("一个模块文件都没读到" in x for x in v)


def test_web_module_uses_module_id_as_event_source() -> None:
    """`web` 的事件源必须是模块 id，不是 catalog 展示标签 `web_entry`（实测踩过）。"""
    gate = _load()
    pubs = gate.scan_publishers()
    entry = pubs["modules"]["web"]
    assert entry["sourceErrors"] == []
    assert "web.entry.created" in entry["published"]
