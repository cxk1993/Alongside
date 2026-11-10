"""容器吸收关系门禁（`tools/check_manifest_absorbs.py`）的自检测试。

★ 为什么门禁要有自己的测试：
  一道一直报绿的门禁，看起来和「没有问题」完全一样。
  所以这里两条判据缺一不可：
    ① 真实仓库数据必须通过（门禁能跑、判据没写反）；
    ② 注入四种失效，门禁必须各自报红（门禁抓得住）。

★ 测试不跑子进程、不碰真实文件：直接喂构造的 manifest 数据给 `evaluate()`。
  门禁的判据是纯函数，测试与门禁共用它 —— 但期望值是**人写的**，
  不是拿门禁输出回填的（后者是自证，永远绿）。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
GATE = REPO / "tools" / "check_manifest_absorbs.py"
MODULES = REPO / "services" / "api" / "modules"
PLUGINS = REPO / "plugins"


def _load():
    spec = importlib.util.spec_from_file_location("_manifest_absorbs_gate", GATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _codes(errors: list[str]) -> set[str]:
    """从错误串里取判据编号（R1/R2/…），只看编号，不看文案。"""
    return {e.split(" ", 1)[0] for e in errors}


def test_real_repo_manifests_are_consistent() -> None:
    """真实数据必须过：吸收关系自洽（幽灵页 / 双容器争抢 / 重复声明都不该有）。"""
    gate = _load()
    builtin, err_a = gate._read_manifests(MODULES)
    third, err_b = gate._read_manifests(PLUGINS)
    assert not err_a and not err_b, f"manifest 读不出：{err_a + err_b}"
    assert len(builtin) >= 25, f"内置 manifest 只读到 {len(builtin)} 份，门禁扫少了"

    result = gate.evaluate({**third, **builtin})
    assert result["ok"], f"吸收关系不自洽：{result['errors']}"

    # 19 条 DOCK_MERGE 的关系必须都能在 manifest 里找到落点
    containers = set(result["containers"])
    assert {"system", "knowledge", "growth", "schedule"} <= containers
    absorbed_system = set(result["absorbed"]["system"])
    assert {"catalog", "mcp", "push", "auth", "export", "query", "plugins", "countdown"} <= absorbed_system


def test_ghost_absorption_is_rejected() -> None:
    gate = _load()
    data: dict[str, Any] = {
        "a": {"id": "a", "kind": "container", "absorbs": ["nope"]},
    }
    assert "R3" in _codes(gate.evaluate(data)["errors"])


def test_double_container_claim_is_rejected() -> None:
    gate = _load()
    data: dict[str, Any] = {
        "b": {"id": "b", "kind": "builtin", "entry": "@/apps/b"},
        "a": {"id": "a", "kind": "container", "tabs": [{"key": "b", "label": "B", "entry": "b"}]},
        "c": {"id": "c", "kind": "container", "absorbs": ["b"]},
    }
    assert "R2" in _codes(gate.evaluate(data)["errors"])


def test_duplicate_declaration_is_rejected() -> None:
    gate = _load()
    data: dict[str, Any] = {
        "a": {
            "id": "a",
            "kind": "container",
            "tabs": [{"key": "b", "label": "B", "entry": "b"}],
            "absorbs": ["b"],
        },
        "b": {"id": "b", "kind": "builtin", "entry": "@/apps/b"},
    }
    assert "R1" in _codes(gate.evaluate(data)["errors"])


def test_nested_container_absorption_is_rejected() -> None:
    gate = _load()
    data: dict[str, Any] = {
        "a": {"id": "a", "kind": "container", "absorbs": ["b"]},
        "b": {"id": "b", "kind": "container", "tabs": [{"key": "b1", "label": "B1", "entry": "b1"}]},
        "b1": {"id": "b1", "kind": "builtin", "entry": "@/apps/b1"},
    }
    assert "R4" in _codes(gate.evaluate(data)["errors"])


def test_non_container_tabs_are_not_treated_as_absorption() -> None:
    """`finance` 的 tabs 是自渲染子页（key 不是模块 id）——不许被判成幽灵页。

    这是对**误报**的回归测试：第一版门禁把非容器的 tabs 当吸收声明，
    于是 finance 的 `beecount` / `local` 两个页被判成「指向不存在的模块」。
    误报比漏报更伤门禁的信用：报一次假红，人就开始绕过它。
    """
    gate = _load()
    data: dict[str, Any] = {
        "finance": {
            "id": "finance",
            "kind": "builtin",
            "entry": "@/apps/finance",
            "tabs": [{"key": "beecount", "label": "账本"}, {"key": "local", "label": "本地流水"}],
        }
    }
    assert gate.evaluate(data)["errors"] == []


def test_manifests_without_ui_but_absorbed_are_fine() -> None:
    """无前端入口的模块被 absorbs 归口是**正解**（countdown 就属于这种）。"""
    gate = _load()
    data: dict[str, Any] = {
        "system": {"id": "system", "kind": "container", "absorbs": ["countdown"]},
        "countdown": {"id": "countdown", "kind": "third-party", "entry": ""},
    }
    result = gate.evaluate(data)
    assert result["errors"] == []
    assert not result["warnings"], f"无 UI 归口不该告警：{result['warnings']}"


def test_manifest_on_disk_declares_system_absorbs_countdown() -> None:
    """system 的 countdown 归口必须写在 manifest 里（前端补表已删，这是唯一来源）。"""
    raw = json.loads((MODULES / "system" / "manifest.json").read_text(encoding="utf-8"))
    assert "countdown" in (raw.get("absorbs") or [])
