"""生命周期钩子的**声明契约**（2026-11-10 裁决 b：`lifecycle` 降级为枚举）。

★ 守什么：
  ① `lifecycle: "hooks"` 是**声明**——说了有钩子就得真有，否则写进 last_error；
  ② 反过来，写了钩子却不声明（隐形钩子）同样要被抓出来；
  ③ 声明错**不拦启停**：钩子是尽力而为的，为一句声明把插件挡在门外不划算。
     所以 `run_lifecycle_hook` 仍然执行，只是把问题串回传给 manager 落 last_error。

★ 为什么这些测试值钱：旧实现里 `lifecycle` 是一份「钩子名映射对象」，
  内核根本不读它 —— 写什么都不会有任何后果（纯装饰）。
  现在它要么与实现一致、要么被看见。这类「字段从装饰变成契约」的改动，
  必须由测试锁住，否则下一次重构会把校验悄悄摘掉。
"""
from __future__ import annotations

from types import SimpleNamespace

from core.plugins import lifecycle

_HOOKS_BODY = """
calls = []


def on_enable(db=None):
    calls.append("enable")


def on_disable(db=None):
    calls.append("disable")
"""


def _plugin(tmp_path, *, plugin_id: str = "demo", body: str | None, declares: str | None):
    """造一个最小插件：可选 api/lifecycle.py + manifest.lifecycle 声明。"""
    root = tmp_path / plugin_id
    (root / "api").mkdir(parents=True)
    if body is not None:
        (root / "api" / "lifecycle.py").write_text(body, encoding="utf-8")
    manifest: dict[str, object] = {"id": plugin_id, "emits": [], "consumes": []}
    if declares is not None:
        manifest["lifecycle"] = declares
    return SimpleNamespace(id=plugin_id, directory=root, manifest=manifest)


def _reset() -> None:
    lifecycle.invalidate_lifecycle_cache()


def test_declared_hooks_with_file_is_consistent(tmp_path) -> None:
    _reset()
    info = _plugin(tmp_path, body=_HOOKS_BODY, declares="hooks")
    assert lifecycle.declaration_problem(info) is None


def test_declared_hooks_without_file_is_reported(tmp_path) -> None:
    _reset()
    info = _plugin(tmp_path, body=None, declares="hooks")
    problem = lifecycle.declaration_problem(info)
    assert problem is not None
    assert "不存在" in problem


def test_empty_hook_file_is_reported(tmp_path) -> None:
    """声明了 hooks，但文件里一个固定名钩子都没有 ⇒ 假承诺。"""
    _reset()
    info = _plugin(tmp_path, body="VERSION = 1\n", declares="hooks")
    problem = lifecycle.declaration_problem(info)
    assert problem is not None
    assert "没有可调用" in problem


def test_undeclared_hooks_are_reported(tmp_path) -> None:
    """写了钩子却没声明 ⇒ 隐形钩子（没人知道它会执行）。"""
    _reset()
    info = _plugin(tmp_path, body=_HOOKS_BODY, declares=None)
    problem = lifecycle.declaration_problem(info)
    assert problem is not None
    assert "未声明" in problem


def test_undeclared_and_no_file_is_fine(tmp_path) -> None:
    _reset()
    info = _plugin(tmp_path, body=None, declares=None)
    assert lifecycle.declaration_problem(info) is None


def test_illegal_value_is_reported(tmp_path) -> None:
    _reset()
    info = _plugin(tmp_path, body=_HOOKS_BODY, declares="custom")
    problem = lifecycle.declaration_problem(info)
    assert problem is not None
    assert "非法" in problem


def test_hook_still_runs_when_declaration_is_wrong(tmp_path) -> None:
    """软失败：声明写了也不拦执行，只是把问题串回传给 last_error。"""
    _reset()
    info = _plugin(tmp_path, body=_HOOKS_BODY, declares=None)
    err = lifecycle.run_lifecycle_hook(info, "enable")
    assert err is not None and "未声明" in err
    mod = lifecycle._load_lifecycle_module(info)
    assert mod is not None and mod.calls == ["enable"]


def test_pi_agent_declaration_matches_its_implementation() -> None:
    """真实插件：pi-agent 声明 hooks，且它的 api/lifecycle.py 有固定名钩子。"""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "plugins" / "pi-agent"
    raw = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert raw.get("lifecycle") == "hooks", "pi-agent 的 lifecycle 声明应降级为枚举 \"hooks\""
    info = SimpleNamespace(id="pi-agent", directory=root, manifest=raw)
    _reset()
    assert lifecycle.declaration_problem(info) is None
