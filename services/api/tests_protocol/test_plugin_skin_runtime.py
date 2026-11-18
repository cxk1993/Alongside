"""真实链路核对：`skin` 指针 → `skinPack` 载荷（皮肤插件 v1）。

★ 为什么测这两个函数而不是 `list_plugins()`
----------------------------------------
`list_plugins()` 要真 DB（`core.deps.set_engine`），在这层测等于把「皮肤解析」
和「数据库就绪」两件事绑死 —— 皮肤坏了会被误报成 DB 问题，反之亦然。
皮肤解析本身是**纯函数**：给一个插件目录与一份清单，返回载荷或错误。
把这两块（解析 + 组装）分别测透，再接一次端到端人工核对，比塞进 DB fixture 更诚实。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# tests_protocol/<file> → parents[0]=tests_protocol, [1]=api, [2]=services, [3]=仓库根
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "api"))

from core.plugins.discover import PluginInfo  # noqa: E402
from core.plugins.manager import PluginManager  # noqa: E402

SKIN = {
    "alongsideSkin": 1,
    "skin": {
        "version": 2,
        "base": "dark",
        "tokens": {"theme": {"--bg": "#0f0d16", "--txt": "#e9e5f5", "--accent": "#a78bfa"}},
        "meta": {"name": "夜紫"},
    },
}


def _plugin(tmp: Path, *, skin: str | None = "skin.json", body: dict | None = None,
            raw: str | None = None) -> PluginInfo:
    manifest: dict = {
        "id": "skin-demo",
        "name": "示例皮肤",
        "version": "0.1.0",
        "kind": "third-party",
        "kernelApi": "^1",
        "entry": "",
        "provides": [],
        "requires": [],
        "slots": [],
        "emits": [],
        "consumes": [],
        "permissions": [],
    }
    if skin is not None:
        manifest["skin"] = skin
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if raw is not None:
        (tmp / "skin.json").write_text(raw, encoding="utf-8")
    elif body is not None:
        (tmp / "skin.json").write_text(json.dumps(body), encoding="utf-8")
    return PluginInfo(
        id="skin-demo",
        kind="third-party",
        source="third-party",
        manifest=manifest,
        directory=tmp,
    )


# ─────────────────────── 解析：skin 指针 → 载荷 ───────────────────────


def test_pointer_resolves_to_payload(tmp_path):
    pack, err = PluginManager._resolve_skin(_plugin(tmp_path, body=SKIN))
    assert err is None
    assert pack is not None
    assert pack["alongsideSkin"] == 1
    assert pack["skin"]["tokens"]["theme"]["--accent"] == "#a78bfa"


def test_no_skin_field_means_nothing(tmp_path):
    pack, err = PluginManager._resolve_skin(_plugin(tmp_path, skin=None))
    assert pack is None and err is None


def test_missing_file_is_reported_not_raised(tmp_path):
    pack, err = PluginManager._resolve_skin(_plugin(tmp_path))
    assert pack is None
    assert err is not None and "不存在" in err


def test_path_escape_is_refused(tmp_path):
    (tmp_path / "outside.json").write_text(json.dumps(SKIN), encoding="utf-8")
    plug = _plugin(tmp_path / "plug", skin="../outside.json")
    pack, err = PluginManager._resolve_skin(plug)
    assert pack is None
    assert err is not None and "不安全" in err


def test_absolute_path_is_refused(tmp_path):
    pack, err = PluginManager._resolve_skin(_plugin(tmp_path, skin="/etc/passwd.json"))
    assert pack is None
    assert err is not None and "不安全" in err


def test_non_json_target_is_refused(tmp_path):
    pack, err = PluginManager._resolve_skin(_plugin(tmp_path, skin="skin.txt"))
    assert pack is None
    assert err is not None and ".json" in err


def test_oversize_file_is_refused(tmp_path):
    big = dict(SKIN)
    big["pad"] = "x" * (64 * 1024)
    pack, err = PluginManager._resolve_skin(_plugin(tmp_path, body=big))
    assert pack is None
    assert err is not None and "上限" in err


def test_broken_json_is_reported(tmp_path):
    pack, err = PluginManager._resolve_skin(_plugin(tmp_path, raw="{不是 JSON"))
    assert pack is None
    assert err is not None and "JSON" in err


def test_missing_envelope_is_refused(tmp_path):
    pack, err = PluginManager._resolve_skin(_plugin(tmp_path, body={"skin": SKIN["skin"]}))
    assert pack is None
    assert err is not None and "alongsideSkin" in err


# ─────────────────────── 组装：载荷随清单下发 ───────────────────────


def test_manifest_for_client_carries_skin_pack(tmp_path):
    plug = _plugin(tmp_path, body=SKIN)
    out = PluginManager._manifest_for_client(plug, skin_pack=SKIN, skin_error=None)
    assert out["skinPack"]["alongsideSkin"] == 1
    # ★ 不改磁盘清单本体（发现层产物被多处共用，就地改会污染别的消费者）
    assert "skinPack" not in plug.manifest


def test_manifest_for_client_carries_skin_error(tmp_path):
    plug = _plugin(tmp_path, body=SKIN)
    out = PluginManager._manifest_for_client(plug, skin_pack=None, skin_error="坏了")
    assert out["skinError"] == "坏了"
    assert "skinPack" not in out


def test_manifest_for_client_unchanged_when_no_skin(tmp_path):
    plug = _plugin(tmp_path, skin=None)
    out = PluginManager._manifest_for_client(plug)
    assert out is plug.manifest, "没有皮肤时不该多拷贝一份清单"


def test_repo_example_plugin_passes_resolver():
    """仓库里那个参考皮肤插件必须真能解析 —— 示例坏了比没有示例更坏。"""
    example = REPO / "plugins" / "skin-night-violet"
    if not example.is_dir():
        import pytest

        pytest.skip("仓库无参考皮肤插件")
    manifest = json.loads((example / "manifest.json").read_text(encoding="utf-8"))
    plug = PluginInfo(
        id="skin-night-violet",
        kind="third-party",
        source="third-party",
        manifest=manifest,
        directory=example,
    )
    pack, err = PluginManager._resolve_skin(plug)
    assert err is None, err
    assert pack is not None
    assert pack["skin"]["tokens"]["theme"]["--accent"] == "#a78bfa"
