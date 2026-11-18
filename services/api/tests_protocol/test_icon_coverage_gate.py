"""G19 图标契约门禁的回归测试。

判据（与 verify_icon_coverage.py 的 docstring 一一对应）：
  · 干净树必须绿（防判据写反）
  · 注入非法 icon 必须红（防抓不住）
  · 三种合法形态：语义名 / 别名（globe→web）/ 单字符
  · 图标表解析不到 ⇒ 判红（表没了比值错了更严重）
  · --report 永远 0（供巡检）
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_GATE = _REPO / "tools" / "gates" / "verify_icon_coverage.py"


def _load_gate():
    """importlib 加载脚本。

    ★ 必须先塞进 sys.modules 再 exec_module —— 脚本里有 `from __future__ import annotations`
      + dataclass，否则 dataclasses 在源码推断时抛 AttributeError（看着像门禁坏了）。
    """
    name = "verify_icon_coverage_under_test"
    spec = importlib.util.spec_from_file_location(name, _GATE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()


def _write_manifest(root: Path, mod: str, icon: object) -> None:
    d = root / "services" / "api" / "modules" / mod
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"id": mod, "icon": icon}), encoding="utf-8")


def _point_gate_at(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(gate, "MODULES_DIR", root / "services" / "api" / "modules")
    monkeypatch.setattr(gate, "PLUGINS_DIR", root / "plugins")


def test_real_repo_is_green() -> None:
    """真仓库必须绿（这条同时证明：图标表解析得到的集合不是空的）。"""
    report = gate.Report()
    gate.check(report)
    assert report.semantic_names, "图标表一个名字都没解析到 —— 门禁会静默放过一切"
    assert report.ok, f"真仓库有非法 icon：{report.missing}"


def test_unknown_icon_is_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_gate_at(monkeypatch, tmp_path)
    _write_manifest(tmp_path, "badmod", "rocket")
    report = gate.Report()
    gate.check(report)
    assert not report.ok
    assert report.missing and report.missing[0][2] == "rocket"


def test_alias_and_single_glyph_are_green(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_gate_at(monkeypatch, tmp_path)
    _write_manifest(tmp_path, "webmod", "globe")  # 别名 → web
    _write_manifest(tmp_path, "coursemod", "课")  # 单字兜底
    report = gate.Report()
    gate.check(report)
    assert report.ok
    assert report.glyph_ok == ["coursemod=课"]


def test_manifest_without_icon_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_gate_at(monkeypatch, tmp_path)
    _write_manifest(tmp_path, "plain", None)
    report = gate.Report()
    gate.check(report)
    assert report.ok


def test_broken_json_is_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_gate_at(monkeypatch, tmp_path)
    d = tmp_path / "services" / "api" / "modules" / "broken"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text("{not json", encoding="utf-8")
    report = gate.Report()
    gate.check(report)
    assert not report.ok
    assert report.bad_manifests


def test_missing_icon_table_is_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "DEFAULT_SET", tmp_path / "nope.tsx")
    with pytest.raises(SystemExit):
        gate.parse_icon_table(gate.DEFAULT_SET, gate.REGISTRY)


def test_dead_alias_is_red(tmp_path: Path) -> None:
    """别名指向不存在的语义名 ⇒ 别名是死的，必须报错。"""
    set_file = tmp_path / "default-set.tsx"
    set_file.write_text('export const DEFAULT_ICON_SET: Record<string, ReactElement> = {\n  web: (\n', encoding="utf-8")
    reg_file = tmp_path / "registry.ts"
    reg_file.write_text("const ALIASES: Record<string, string> = {\n  globe: \"nope\",\n};\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        gate.parse_icon_table(set_file, reg_file)


def test_report_mode_never_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_gate_at(monkeypatch, tmp_path)
    _write_manifest(tmp_path, "badmod", "rocket")
    assert gate.main(["--report", "--json"]) == 0
