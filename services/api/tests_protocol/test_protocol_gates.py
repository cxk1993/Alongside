# ── 规则的存活证明 ──
"""协议门禁回归测试（Alongside 插件协议 v1.0 · 规则表 §20）。

★ 意义：协议报告里的每条规则都必须有对应的机器测试。
  没有测试的规则 = 下次重构就会被改掉。

★ 分两部分：
  第一部分 · 单插件（①②层）—— core/plugins/validate.py
  第二部分 · 跨插件（③层）—— core/plugins/crosscheck.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

# ── 直接加载内核模块（绕开 core.plugins.__init__ 的 sqlmodel 依赖）──
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))


def _load(module_name: str, relpath: str) -> Any:
    """按文件加载内核模块（不触发包 __init__ 的重依赖）。

    ★ 注意：这里**不能**把假的 core.errors 永久留在 sys.modules 里。
      同一 pytest 进程里其它测试会 import core.errors（要 AppError 等真类），
      留一个只有 ManifestError 的假模块进去会污染它们，导致
      「cannot import name 'AppError'」这类与本文件无关的失败。
      所以：加载完立刻把 sys.modules 恢复原状。
    """
    err_mod = types.ModuleType("core.errors")

    class ManifestError(Exception):
        pass

    # ★ 动态挂属性：给 ModuleType 挂 ManifestError 是测试夹具的常规做法。
    #   mypy 报「ModuleType 没有该属性」——它不知道 ModuleType 允许动态属性。
    #   这里用 type: ignore 精确豁免这一处，而不是改 ruff（ruff 反而反对 setattr）。
    err_mod.ManifestError = ManifestError  # type: ignore[attr-defined]
    real_errors = sys.modules.get("core.errors")
    sys.modules["core.errors"] = err_mod
    try:
        spec = importlib.util.spec_from_file_location(module_name, relpath)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        # 恢复现场：装了真的就还回去，没装过就删掉
        if real_errors is None:
            sys.modules.pop("core.errors", None)
        else:
            sys.modules["core.errors"] = real_errors
    return mod


_validate = _load("gate_validate", str(_API_ROOT / "core/plugins/validate.py"))
_crosscheck = _load("gate_crosscheck", str(_API_ROOT / "core/plugins/crosscheck.py"))

validate_manifest = _validate.validate_manifest
crosscheck_manifests = _crosscheck.crosscheck_manifests
provider_conflicts = _crosscheck.provider_conflicts


BASE: dict[str, Any] = {
    "id": "demo",
    "name": "演示插件",
    "version": "1.0.0",
    "kind": "builtin",
    "kernelApi": "^1",
    "api": {"base": "/api/v1/demo"},
    "provides": [],
    "requires": [],
    "slots": [],
    "emits": [],
    "consumes": [],
    "permissions": [],
}


def _mf(**mutate: Any) -> dict[str, Any]:
    m = copy.deepcopy(BASE)
    m.update(mutate)
    return m


# ═══════════════ 第一部分 · 单插件规则（①②层）═══════════════════════


def test_baseline_manifest_passes() -> None:
    """基线：最小合法 manifest 必须通过。"""
    validate_manifest(_mf())


# ── 规则 2：requires ∩ optionalDependencies = ∅ ──
def test_rule2_hard_and_soft_must_be_disjoint() -> None:
    with pytest.raises(Exception, match="重复声明"):
        validate_manifest(_mf(requires=["a.b.c"], optionalDependencies=["a.b.c"]))


# ── 规则 3：kind != core 时 approval != never ──
def test_rule3_non_core_cannot_use_approval_never() -> None:
    with pytest.raises(Exception, match="approval"):
        validate_manifest(_mf(execution={"approval": "never"}))


def test_rule3_core_may_use_approval_never() -> None:
    validate_manifest(_mf(kind="core", execution={"approval": "never"}))


# ── 规则 4：third-party 不得声明 riskLevel L0 ──
def test_rule4_third_party_cannot_claim_l0() -> None:
    with pytest.raises(Exception, match="riskLevel"):
        validate_manifest(_mf(kind="third-party", riskLevel="L0"))


def test_rule4_builtin_may_lower_risk_level() -> None:
    """推断值可以显式降低（builtin 推断 L2 → 声明 L0 允许）。"""
    validate_manifest(_mf(kind="builtin", riskLevel="L0"))


# ── 规则 5 / 6：origin 完整性 ──
def test_rule5_signed_requires_signature() -> None:
    with pytest.raises(Exception, match="signature"):
        validate_manifest(_mf(origin={"signed": True, "installedBy": "human.user"}))


def test_rule6_origin_requires_installed_by() -> None:
    with pytest.raises(Exception, match="installedBy"):
        validate_manifest(_mf(origin={"source": "local"}))


def test_rule6_complete_origin_passes() -> None:
    validate_manifest(
        _mf(origin={"source": "local", "signed": False, "installedBy": "agent.tester"})
    )


# ── 规则 7 / 8：migrations ⟹ exclusive / track=persistent ──
def test_rule7_migrations_must_be_exclusive() -> None:
    with pytest.raises(Exception, match="exclusive"):
        validate_manifest(
            _mf(migrations="api/migrations", concurrency={"exclusive": False})
        )


def test_rule7_migrations_without_concurrency_is_backward_compatible() -> None:
    """★ 存量插件没有 concurrency 字段 —— 不许因此起不来。

    「字段缺失」按内核推断的保守默认值处理；「显式声明错」才启动失败。
    """
    validate_manifest(_mf(migrations="api/migrations"))


def test_rule8_ephemeral_must_not_declare_migrations() -> None:
    with pytest.raises(Exception, match="persistent"):
        validate_manifest(_mf(migrations="api/migrations", track="ephemeral"))


def test_rule7_and_8_correct_declaration_passes() -> None:
    validate_manifest(
        _mf(
            migrations="api/migrations",
            concurrency={"exclusive": True},
            track="persistent",
        )
    )


# ── 规则 9：sideEffects.external ⟹ checkpoint ──
def test_rule9_external_requires_checkpoint() -> None:
    with pytest.raises(Exception, match="checkpoint"):
        validate_manifest(_mf(sideEffects={"external": True}))


def test_rule9_external_with_checkpoint_passes() -> None:
    validate_manifest(
        _mf(sideEffects={"external": True, "checkpoint": "before-side-effect"})
    )


# ── 规则 10 / 11：并发策略 ──
def test_rule10_parallel_safe_only_read_operations() -> None:
    with pytest.raises(Exception, match="parallelSafe"):
        validate_manifest(_mf(concurrency={"parallelSafe": ["read", "write"]}))


def test_rule11_cannot_disable_optimistic_lock_unless_exclusive() -> None:
    with pytest.raises(Exception, match="optimisticLock"):
        validate_manifest(_mf(concurrency={"optimisticLock": False}))


def test_rule11_exclusive_may_disable_optimistic_lock() -> None:
    validate_manifest(
        _mf(concurrency={"optimisticLock": False, "exclusive": True})
    )


# ── 规则 14：schedule 选择器互斥 ──
def test_rule14_schedule_selectors_are_mutually_exclusive() -> None:
    with pytest.raises(Exception, match="选择器"):
        validate_manifest(
            _mf(
                schedule={
                    "at": "2026-11-01T09:00:00+08:00",
                    "daily": {"time": "09:00:00", "timeZone": "Asia/Shanghai"},
                    "title": "日报",
                }
            )
        )


def test_rule14_every_86400_cannot_replace_daily() -> None:
    """固定间隔对齐创建时刻，daily 对齐墙上时间 —— 是两种语义。"""
    with pytest.raises(Exception, match="86400"):
        validate_manifest(_mf(schedule={"every_seconds": 86400, "title": "日报"}))


def test_rule14_single_schedule_selector_passes() -> None:
    validate_manifest(
        _mf(
            schedule={
                "daily": {"time": "23:00:00", "timeZone": "Asia/Shanghai"},
                "title": "日报",
            }
        )
    )


# ── 结构层：未知字段必须报错（字段名拼错 = 当作没写）──
def test_unknown_field_is_rejected() -> None:
    """★ 协议报告 §21 说明一：requires 拼成 requiers 必须当场报错。

    旧行为是「当作未声明」—— 而「未声明」是合法的，于是没有任何提示，
    排查方向会被误导到权限配置上。
    """
    with pytest.raises(Exception, match="未知字段"):
        validate_manifest(_mf(requiers=["a.b.c"]))


def test_x_prefixed_extension_is_allowed() -> None:
    """扩展位放行（不报不拒）。"""
    validate_manifest(_mf(x_custom="anything"))


def test_container_kind_may_omit_api() -> None:
    """容器型（纯前端编排）可无 api。"""
    m = _mf(kind="container")
    m.pop("api")
    validate_manifest(m)


def test_non_container_without_api_is_rejected() -> None:
    """非 container 型必须声明 api（缺则拒，防静默漏配）。"""
    m = _mf()
    m.pop("api")
    with pytest.raises(Exception, match="api"):
        validate_manifest(m)


# ═══════════════ 第二部分 · 跨插件规则（③层）═════════════════════════


def test_rule12_provides_must_have_definer_in_strict_mode() -> None:
    """一旦有任一插件写了 defines，全系统切严格模式。"""
    manifests = [
        {"id": "a", "defines": ["x.y.z"], "provides": ["x.y.z"]},
        {"id": "b", "provides": ["q.r.s"]},
    ]
    violations = crosscheck_manifests(manifests)
    assert any("defines" in x for x in violations)


def test_rule13_capability_provided_twice_is_registered_not_blocking() -> None:
    """★ 同一能力被两个插件 provides → **登记为待裁决冲突，不阻断启动**。

    ★ 为什么不再阻断（2026-10-28 修订）：
      旧口径是硬报错、进程起不来。但「谁提供这个能力、选谁」这个决定
      **只有包层能做**（包是唯一知道「包里所有插件」的实体），
      而包层的装配发生在**插件发现之后**。
      ⇒ 旧口径等于让内核在包层说话之前就把系统判死，
        报错还指向一个「尚未被读到」的字段。
    ⇒ 新口径：内核登记冲突、不阻断；包层用 `providerChoice` 裁决；
      没裁决 ⇒ 该能力标 unavailable，系统照常起来。
    """
    manifests = [
        {"id": "a", "defines": ["x.y.z"], "provides": ["x.y.z"]},
        {"id": "b", "defines": ["q.r.s"], "provides": ["x.y.z"]},
    ]
    # 不再是启动失败的理由
    assert crosscheck_manifests(manifests) == []
    # 但必须被登记下来（不许静默）
    conflicts = provider_conflicts(manifests)
    assert conflicts == {"x.y.z": ["a", "b"]}


def test_rule13_selection_removes_the_conflict() -> None:
    """★ 已被显式选定（`providerChoice`）⇒ 不再算冲突。

    选定由**包层**提供（包是唯一知道「包里所有插件」的实体）。
    """
    manifests = [
        {"id": "a", "defines": ["x.y.z"], "provides": ["x.y.z"]},
        {"id": "b", "defines": ["q.r.s"], "provides": ["x.y.z"]},
    ]
    conflicts = provider_conflicts(manifests, selection={"x.y.z": "b"})
    assert conflicts == {}


def test_capability_cannot_be_defined_twice() -> None:
    """★ 两个插件 defines 同一能力 → **仍然阻断**。

    能力缝二义，没有任何一层能裁决 ⇒ 必须停。
    """
    manifests = [
        {"id": "a", "defines": ["x.y.z"], "provides": ["x.y.z"]},
        {"id": "b", "defines": ["x.y.z"], "provides": ["x.y.z"]},
    ]
    violations = crosscheck_manifests(manifests)
    assert any("defines" in x for x in violations)


def test_compatibility_mode_when_nobody_defines() -> None:
    """全系统无人写 defines → 兼容模式（provides 隐含 defines），不报错。"""
    manifests = [
        {"id": "a", "provides": ["x.y.z"]},
        {"id": "b", "provides": ["q.r.s"]},
    ]
    assert crosscheck_manifests(manifests) == []


def test_strict_mode_all_defines_and_provides_match() -> None:
    manifests = [
        {"id": "a", "defines": ["x.y.z"], "provides": ["x.y.z"]},
        {"id": "b", "defines": ["q.r.s"], "provides": ["q.r.s"]},
    ]
    assert crosscheck_manifests(manifests) == []


# ═══════════════ 第三部分 · 存量不破（回归硬判据）══════════════════════


def test_all_real_manifests_still_pass() -> None:
    """★ 硬判据：全部真实插件必须在新协议下通过（不误杀存量）。"""
    root = Path(__file__).resolve().parents[1]
    paths = sorted(root.glob("modules/*/manifest.json")) + sorted(
        (root.parent.parent / "plugins").glob("*/manifest.json")
    )
    assert len(paths) >= 18, f"真实插件数异常减少：{len(paths)}"
    for p in paths:
        raw = json.loads(p.read_text(encoding="utf-8"))
        validate_manifest(raw)  # 不抛异常即通过
