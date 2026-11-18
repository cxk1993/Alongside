"""G20 皮肤插件门禁的回归测试。

判据（与 verify_plugin_skin.py 的 docstring 一一对应）：
  · 合法包必须绿（防判据写反）
  · 路径逃逸 / 绝对路径 / 非 .json 结尾 ⇒ 拦
  · 目标文件不存在 ⇒ 拦
  · 超过 64KB ⇒ 拦（与皮肤库单条上限同口径）
  · 皮肤文件里的令牌过不了运行时 validateSkinPack ⇒ 拦
  · 损坏 JSON ⇒ 拦
  · **零皮肤插件的空转 ⇒ 判红**（本门禁的核心一条）
  · --report 永远 0（含零皮肤与违规两种情形）

夹具全部造在 tmp_path 里，不依赖真实 `plugins/` 目录：
`PLUGINS_DIR` 被指到临时树，但 `WEB_DIR` 保持真仓库 —— 校验器必须是运行时的
那一份（Node 跑 apps/web/src/shared/styles/skin.ts），换成假的就等于没验。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_GATE = _REPO / "tools" / "gates" / "verify_plugin_skin.py"


def _load_gate():
    """importlib 加载脚本。

    ★ 必须先塞进 sys.modules 再 exec_module —— 脚本里有 `from __future__ import
      annotations` + dataclass，否则 dataclasses 在源码推断时抛 AttributeError
      （看着像门禁坏了）。同 test_icon_coverage_gate.py 的做法。
    """
    name = "verify_plugin_skin_under_test"
    spec = importlib.util.spec_from_file_location(name, _GATE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()

_HAVE_NODE = shutil.which("node") is not None
#: 需要真跑 Node 校验器的用例：环境里没 node 就跳过（CI 与开发机都有）。
needs_node = pytest.mark.skipif(not _HAVE_NODE, reason="环境里没有 node，跑不了运行时校验器")


def _good_skin() -> dict:
    """一份真能过 validateSkinPack 的皮肤（包络形态，与示例包同构）。"""
    return {
        "alongsideSkin": 1,
        "skin": {
            "version": 2,
            "base": "dark",
            "tokens": {
                "theme": {
                    "--bg": "#0e1116",
                    "--surface": "#171c26",
                    "--surface-2": "#1f2632",
                    "--surface-3": "#283042",
                    "--txt": "#e6edf7",
                    "--accent": "#5b8cff",
                    "--radius": "10px",
                }
            },
            "meta": {"name": "测试皮肤"},
        },
    }


def _add_plugin(
    root: Path,
    mod: str,
    skin: object = None,
    *,
    payload: dict | None = None,
    raw_manifest: str | None = None,
) -> Path:
    """在临时树里造一个插件目录，返回插件目录。"""
    d = root / "plugins" / mod
    d.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"id": mod, "name": mod, "version": "1.0.0"}
    if skin is not None:
        manifest["skin"] = skin
    text = raw_manifest if raw_manifest is not None else json.dumps(manifest, ensure_ascii=False)
    (d / "manifest.json").write_text(text, encoding="utf-8")
    return d


def _write_skin(plugin_dir: Path, name: str, data: object) -> Path:
    target = plugin_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
    target.write_text(text, encoding="utf-8")
    return target


def _point_gate_at(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(gate, "PLUGINS_DIR", root / "plugins")


def _run(root: Path, monkeypatch: pytest.MonkeyPatch, strict: bool = False):
    _point_gate_at(monkeypatch, root)
    report = gate.Report()
    gate.check(report, strict=strict)
    return report


# ─────────────────────── 正向 ───────────────────────


@needs_node
def test_valid_pack_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _add_plugin(tmp_path, "goodskin", "skin.json")
    _write_skin(d, "skin.json", _good_skin())
    report = _run(tmp_path, monkeypatch)
    assert report.findings == [], report.findings
    assert report.idle == ""
    assert report.ok
    assert report.with_skin == 1
    assert report.passed and "goodskin" in report.passed[0]


@needs_node
def test_nested_pointer_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`skins/default.json` 这种子目录指针是合法形态（schema pattern 允许）。"""
    d = _add_plugin(tmp_path, "nested", "skins/default.json")
    _write_skin(d, "skins/default.json", _good_skin())
    report = _run(tmp_path, monkeypatch)
    assert report.ok, report.findings


def test_plugin_without_skin_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_plugin(tmp_path, "plainskin")
    _add_plugin(tmp_path, "goodskin", "skin.json")
    _write_skin(tmp_path / "plugins" / "goodskin", "skin.json", _good_skin())
    report = _run(tmp_path, monkeypatch)
    assert "plainskin" in report.skipped
    assert report.with_skin == 1  # 无 skin 的插件不计入「带皮肤」
    assert report.idle == ""


# ─────────────────────── 路径安全 ───────────────────────


@pytest.mark.parametrize("pointer", ["../evil.json", "skins/../../evil.json", "..\\evil.json"])
def test_path_escape_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pointer: str
) -> None:
    _add_plugin(tmp_path, "escaper", pointer)
    # 逃逸目标真的存在 —— 证明拦的是「路径」而不是「文件缺失」
    _write_skin(tmp_path / "plugins", "evil.json", _good_skin())
    report = _run(tmp_path, monkeypatch)
    assert not report.ok
    assert report.findings and ".." in report.findings[0][1]


def test_absolute_pointer_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_plugin(tmp_path, "abs", "/etc/skin.json")
    report = _run(tmp_path, monkeypatch)
    assert not report.ok
    assert report.findings and "以 / 开头" in report.findings[0][1]


def test_non_json_pointer_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _add_plugin(tmp_path, "noj", "skin.txt")
    _write_skin(d, "skin.txt", _good_skin())
    report = _run(tmp_path, monkeypatch)
    assert not report.ok
    assert report.findings and ".json" in report.findings[0][1]


def test_empty_pointer_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_plugin(tmp_path, "empty", "")
    report = _run(tmp_path, monkeypatch)
    assert not report.ok
    assert report.findings


# ─────────────────────── 存在 / 大小 / 内容 ───────────────────────


def test_missing_file_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_plugin(tmp_path, "ghost", "skin.json")
    report = _run(tmp_path, monkeypatch)
    assert not report.ok
    assert report.findings and "文件不存在" in report.findings[0][1]


def test_oversized_file_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _add_plugin(tmp_path, "fat", "skin.json")
    good = _good_skin()
    good["skin"]["meta"]["name"] = "x" * (gate.MAX_SKIN_BYTES + 1024)
    target = _write_skin(d, "skin.json", good)
    assert target.stat().st_size > gate.MAX_SKIN_BYTES
    report = _run(tmp_path, monkeypatch)
    assert not report.ok
    assert report.findings and "超过" in report.findings[0][1]


def test_exactly_at_limit_is_not_blocked_by_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """边界：恰好 65536 字节不该因为「大小」被拦（判据是 ≤）。"""
    d = _add_plugin(tmp_path, "edge", "skin.json")
    good = _good_skin()
    target = _write_skin(d, "skin.json", good)
    pad = gate.MAX_SKIN_BYTES - target.stat().st_size
    assert pad > 16
    # 缩进与键名长度会吃掉几字节，按实测差值收敛到位（差 1 字节就再调 1）
    for _ in range(8):
        good["skin"]["meta"]["_pad"] = "p" * pad
        _write_skin(d, "skin.json", good)
        delta = target.stat().st_size - gate.MAX_SKIN_BYTES
        if delta == 0:
            break
        pad -= delta
    assert target.stat().st_size == gate.MAX_SKIN_BYTES
    report = _run(tmp_path, monkeypatch)
    assert not any("超过" in why for _, why in report.findings)


def test_broken_json_skin_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _add_plugin(tmp_path, "broken", "skin.json")
    _write_skin(d, "skin.json", "{not json")
    report = _run(tmp_path, monkeypatch)
    assert not report.ok
    assert report.findings and "JSON" in report.findings[0][1]


@needs_node
def test_pack_failing_runtime_validator_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`var()` 注入是运行时明令拒收的值 —— 门禁必须用同一份判据抓住它。"""
    d = _add_plugin(tmp_path, "injector", "skin.json")
    bad = _good_skin()
    bad["skin"]["tokens"]["theme"]["--accent"] = "var(--danger)"
    _write_skin(d, "skin.json", bad)
    report = _run(tmp_path, monkeypatch)
    assert not report.ok
    assert report.findings  # 理由来自 validateSkinPack，不是 Python 复刻


@needs_node
def test_pack_missing_required_tokens_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """皮肤包必须自带 --bg 与 --txt（validateSkinPack 的 requireBase 档）。"""
    d = _add_plugin(tmp_path, "bare", "skin.json")
    bare = {
        "alongsideSkin": 1,
        "skin": {"version": 2, "base": "dark", "tokens": {"theme": {"--radius": "8px"}}},
    }
    _write_skin(d, "skin.json", bare)
    report = _run(tmp_path, monkeypatch)
    assert not report.ok
    assert report.findings


def test_broken_manifest_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_plugin(tmp_path, "badmanifest", raw_manifest="{not json")
    report = _run(tmp_path, monkeypatch)
    assert not report.ok
    assert report.findings and "manifest 读不出来" in report.findings[0][1]


# ─────────────────────── 反空转 ───────────────────────


def test_zero_skin_plugins_is_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """零皮肤插件 ⇒ 门禁空转 ⇒ 判红（不是「无事可做就绿」）。"""
    _add_plugin(tmp_path, "a")
    _add_plugin(tmp_path, "b")
    report = _run(tmp_path, monkeypatch)
    assert report.scanned == 2
    assert report.with_skin == 0
    assert report.idle and "空转" in report.idle
    assert not report.ok


def test_empty_plugins_dir_is_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """连插件都没有 ⇒ 同样判红（扫零个插件是最典型的假绿）。"""
    (tmp_path / "plugins").mkdir()
    report = _run(tmp_path, monkeypatch)
    assert report.scanned == 0
    assert not report.ok
    assert report.idle


def test_real_repo_without_skins_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """真仓库当前还没有带皮肤的插件 —— 这道门禁此刻就该是红的（刻意如此）。"""
    report = gate.Report()
    gate.check(report)
    if report.with_skin == 0:
        assert not report.ok
        assert "空转" in report.idle


# ─────────────────────── 退出码 ───────────────────────


def test_main_returns_1_on_idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_gate_at(monkeypatch, tmp_path)
    _add_plugin(tmp_path, "a")
    assert gate.main([]) == 1


def test_report_mode_never_fails_on_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _point_gate_at(monkeypatch, tmp_path)
    _add_plugin(tmp_path, "a")
    assert gate.main(["--report"]) == 0
    out = capsys.readouterr().out
    assert "扫描 1 个插件，0 个带皮肤" in out


def test_report_mode_never_fails_on_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _point_gate_at(monkeypatch, tmp_path)
    _add_plugin(tmp_path, "escaper", "../evil.json")
    assert gate.main(["--report"]) == 0


@needs_node
def test_main_returns_0_on_valid_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _point_gate_at(monkeypatch, tmp_path)
    d = _add_plugin(tmp_path, "goodskin", "skin.json")
    _write_skin(d, "skin.json", _good_skin())
    assert gate.main([]) == 0
    out = capsys.readouterr().out
    assert "扫描 1 个插件，1 个带皮肤，0 项阻断" in out


@needs_node
def test_strict_turns_warnings_into_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺 meta.name 只是 warning：默认放过，--strict 下阻断。"""
    d = _add_plugin(tmp_path, "noname", "skin.json")
    good = _good_skin()
    del good["skin"]["meta"]
    _write_skin(d, "skin.json", good)

    lenient = _run(tmp_path, monkeypatch)
    assert lenient.ok, lenient.findings

    strict = _run(tmp_path, monkeypatch, strict=True)
    assert not strict.ok
    assert strict.findings and "--strict" in strict.findings[0][1]
