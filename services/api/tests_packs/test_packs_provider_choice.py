"""`providerChoice` 的自证失败测试 + 能力路由裁决测试。

★ 每条规则都按「构造违规输入 → 确认它被拒 / 被登记」写（不是「正常路径能过」）。

覆盖的规则：
  B1-8  · providerChoice 的形状（键=能力名，值=成员插件 id，形状不同要分别查）
  B2-12 · 四项一致性（键∈本包成员 provides 并集 / 值∈该能力提供者 / 不指向包外）
  层序   · 基包 ← 派生包 ← 用户层，最后写入者胜
  裁决   · 已选定 ⇒ 不算冲突；未选定 ⇒ 登记为待裁决（不阻断）
  报告   · 装载报告带 selectedProviders 与 providerConflicts
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packs.assembler import PackAssembler
from packs.model import PackError
from packs.routing import resolve_provider_choice, route_capabilities
from packs.validate import parse_manifest, validate_consistency

# ─────────────────────────── 假内核 ───────────────────────────

PLUGINS: dict[str, dict] = {
    "finance": {
        "id": "finance", "kind": "builtin", "version": "1.2.0",
        "provides": ["finance.entry.read", "finance.entry.write"], "riskLevel": "L1",
    },
    "finance-bank-b": {
        "id": "finance-bank-b", "kind": "third-party", "version": "1.0.0",
        "provides": ["finance.entry.read"], "riskLevel": "L2",
    },
    "todo": {
        "id": "todo", "kind": "builtin", "version": "0.8.1",
        "provides": ["todo.item.read"], "riskLevel": "L1",
    },
}


class _Found:
    def __init__(self, manifests: list[dict]) -> None:
        self.plugins = [
            type("Info", (), {"id": m["id"], "manifest": m, "directory": None})()
            for m in manifests
        ]
        self.errors: list = []


class FakeKernel:
    def __init__(self) -> None:
        self.enabled: list[str] = []
        self.disabled: list[str] = []

    def enable(self, plugin_id: str, registry) -> dict:
        self.enabled.append(plugin_id)
        return {}

    def disable(self, plugin_id: str, registry) -> dict:
        self.disabled.append(plugin_id)
        return {}


def base_pack(**over) -> dict:
    raw = {
        "packFormat": 1,
        "id": "official.demo",
        "name": "演示包",
        "version": "1.0.0",
        "kind": "official",
        "kernelApi": "^1",
        "license": "Apache-2.0",
        "members": {
            "required": [
                {"id": "finance", "version": "1.2.0"},
                {"id": "finance-bank-b", "version": "1.0.0"},
            ],
            "optional": [],
            "excluded": [],
        },
    }
    raw.update(over)
    return raw


def write_pack(root: Path, raw: dict, dirname: str | None = None) -> Path:
    directory = root / (dirname or raw["id"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pack.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return directory


# ═══════════════ B1-8 · 形状 ═══════════════


def test_b1_8_provider_choice_must_be_object() -> None:
    """★ providerChoice 写成数组 ⇒ 硬拒绝（形状错不能放过去）。"""
    with pytest.raises(PackError) as ei:
        parse_manifest(base_pack(providerChoice=["finance"]))
    assert ei.value.code == "B1-8"
    assert "providerChoice" in str(ei.value)


def test_b1_8_key_must_be_capability_name() -> None:
    """★ 键写成插件 id（单段）⇒ 拒绝。键是能力名，必须带点。"""
    with pytest.raises(PackError) as ei:
        parse_manifest(base_pack(providerChoice={"finance": "finance"}))
    assert ei.value.code == "B1-8"
    assert "能力名" in str(ei.value)


def test_b1_8_value_must_be_plugin_id() -> None:
    """★ 值写成命名空间（带点）⇒ 拒绝。值是成员插件 id，单段。"""
    with pytest.raises(PackError) as ei:
        parse_manifest(base_pack(providerChoice={"finance.entry.read": "official.finance"}))
    assert ei.value.code == "B1-8"
    assert "插件 id" in str(ei.value)


def test_b1_8_valid_provider_choice_parses() -> None:
    """★ 正常形态能过，并落到模型里。"""
    m = parse_manifest(base_pack(providerChoice={"finance.entry.read": "finance-bank-b"}))
    assert m.provider_choice == {"finance.entry.read": "finance-bank-b"}


# ═══════════════ B2-12 · 一致性 ═══════════════


def test_b2_12_rejects_provider_outside_pack() -> None:
    """★ 值指向**包外**插件 ⇒ 硬拒绝（包不能替外部世界做选择）。"""
    m = parse_manifest(
        base_pack(providerChoice={"finance.entry.read": "not-in-pack"})
    )
    with pytest.raises(PackError) as ei:
        validate_consistency(m, PLUGINS)
    assert ei.value.code == "B2-12"


def test_b2_12_rejects_capability_no_member_provides() -> None:
    """★ 键不在本包成员的 provides 并集内 ⇒ 硬拒绝。"""
    m = parse_manifest(base_pack(providerChoice={"todo.item.write": "finance"}))
    with pytest.raises(PackError) as ei:
        validate_consistency(m, PLUGINS)
    assert ei.value.code == "B2-12"
    assert "provides" in str(ei.value)


def test_b2_12_rejects_value_not_a_provider_of_that_capability() -> None:
    """★ 值不是该能力在本包内的提供者 ⇒ 硬拒绝。"""
    # todo provides todo.item.read，不提供 finance.entry.read
    m = parse_manifest(base_pack(providerChoice={"finance.entry.read": "todo"}))
    with pytest.raises(PackError) as ei:
        validate_consistency(m, PLUGINS)
    assert ei.value.code == "B2-12"
    assert "提供者是" in str(ei.value)


def test_b2_12_accepts_valid_selection() -> None:
    """★ 合法选定通过校验。"""
    m = parse_manifest(base_pack(providerChoice={"finance.entry.read": "finance-bank-b"}))
    validate_consistency(m, PLUGINS)  # 不抛


def test_b2_12_only_provider_selection_is_warning_not_error() -> None:
    """★ 只有一个提供者时显式指定 ⇒ 警告，不阻断。

    ★ 用一个**成员只有一个提供者**的能力来试：
      `finance.entry.write` 只有 finance 提供（finance-bank-b 只 provides read）。
    """
    m = parse_manifest(base_pack(providerChoice={"finance.entry.write": "finance"}))
    validate_consistency(m, PLUGINS)  # 不抛


# ═══════════════ 层序 ═══════════════


def test_layer_order_last_writer_wins() -> None:
    """★ 层序：基包 ← 派生包 ← 用户层，最后写入者胜。"""
    base = parse_manifest(
        base_pack(id="official.base", providerChoice={"finance.entry.read": "finance"})
    )
    derived = parse_manifest(
        base_pack(id="official.derived", providerChoice={"finance.entry.read": "finance-bank-b"})
    )
    chosen, layer_of = resolve_provider_choice([base, derived])
    assert chosen["finance.entry.read"] == "finance-bank-b"
    assert layer_of["finance.entry.read"] == "official.derived"


def test_layer_order_user_layer_wins() -> None:
    """★ 用户覆盖层优先级最高。"""
    base = parse_manifest(
        base_pack(id="official.base", providerChoice={"finance.entry.read": "finance"})
    )
    chosen, layer_of = resolve_provider_choice(
        [base], user_choice={"finance.entry.read": "finance-bank-b"}
    )
    assert chosen["finance.entry.read"] == "finance-bank-b"
    assert layer_of["finance.entry.read"] == "user"


# ═══════════════ 裁决 ═══════════════


def test_route_selected_provider_is_recorded() -> None:
    """★ 已选定 ⇒ 记进 selectedProviders。"""
    base = parse_manifest(
        base_pack(providerChoice={"finance.entry.read": "finance-bank-b"})
    )
    selected, conflicts = route_capabilities([base], PLUGINS)
    assert selected == [
        {"capability": "finance.entry.read", "provider": "finance-bank-b", "layer": "official.demo"}
    ]
    assert conflicts == []


def test_route_unselected_multi_provider_is_conflict_not_failure() -> None:
    """★ 两个提供者、没写 providerChoice ⇒ **登记为待裁决**，不报错。"""
    base = parse_manifest(base_pack())
    selected, conflicts = route_capabilities([base], PLUGINS)
    assert selected == []
    assert conflicts == [
        {"capability": "finance.entry.read", "candidates": ["finance", "finance-bank-b"]}
    ]


def test_route_only_looks_at_pack_members() -> None:
    """★ 判据对象是**本包成员子集**，不是全量插件。

    ★ 这条是口径判据：全量冲突可能是别的包的成员，包层无权代裁。
    """
    # 另一个包外的插件也 provides 同一能力 —— 不该影响本包
    plugins = dict(PLUGINS)
    plugins["outsider"] = {
        "id": "outsider", "kind": "third-party", "version": "1.0.0",
        "provides": ["finance.entry.read"], "riskLevel": "L2",
    }
    base = parse_manifest(base_pack())
    selected, conflicts = route_capabilities([base], plugins)
    assert conflicts == [
        {"capability": "finance.entry.read", "candidates": ["finance", "finance-bank-b"]}
    ]
    assert "outsider" not in str(conflicts)


# ═══════════════ 装载报告 ═══════════════


def test_load_report_carries_selected_providers(tmp_path: Path) -> None:
    """★ 装载报告带 selectedProviders（能力路由留痕）。"""
    raw = base_pack(providerChoice={"finance.entry.read": "finance-bank-b"})
    d = write_pack(tmp_path, raw)
    kernel = FakeKernel()
    asm = PackAssembler(
        data_dir=tmp_path / "data",
        registry=None,
        enable=kernel.enable,
        disable=kernel.disable,
        discover=lambda: _Found(list(PLUGINS.values())),
    )
    report = asm.load(d)
    assert report.status == "loaded"
    assert report.selected_providers == (
        {"capability": "finance.entry.read", "provider": "finance-bank-b", "layer": "official.demo"},
    )
    assert report.provider_conflicts == ()


def test_load_report_carries_unresolved_conflicts(tmp_path: Path) -> None:
    """★ 未裁决的冲突进报告（不阻断装载）。"""
    d = write_pack(tmp_path, base_pack())
    kernel = FakeKernel()
    asm = PackAssembler(
        data_dir=tmp_path / "data",
        registry=None,
        enable=kernel.enable,
        disable=kernel.disable,
        discover=lambda: _Found(list(PLUGINS.values())),
    )
    report = asm.load(d)
    assert report.status == "loaded"  # ★ 不因冲突失败
    assert report.provider_conflicts == (
        {"capability": "finance.entry.read", "candidates": ["finance", "finance-bank-b"]},
    )
