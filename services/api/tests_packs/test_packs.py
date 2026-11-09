"""包层自证失败测试（B1 / B2）+ 装配流程测试。

**不依赖 DB、不依赖 FastAPI** —— 内核能力全部用假实现注入。
这样包层的规则可以在模型层就被证伪，而不是等装进系统才发现。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from packs.assembler import PackAssembler, PackBusyError
from packs.digest import content_digest
from packs.model import AdjustmentAction, LoadStatus, PackError
from packs.state import PackStore
from packs.validate import (
    expand_includes,
    parse_manifest,
    validate_consistency,
    validate_structure,
)

# ─────────────────────────── 假内核 ───────────────────────────

PLUGINS: dict[str, dict] = {
    "finance": {
        "id": "finance", "kind": "builtin", "version": "1.2.0",
        "provides": ["finance.entry.read", "finance.entry.write"], "riskLevel": "L1",
    },
    "todo": {
        "id": "todo", "kind": "builtin", "version": "0.8.1",
        "provides": ["todo.item.read"], "riskLevel": "L1",
    },
    "weekly-report": {
        "id": "weekly-report", "kind": "third-party", "version": "0.3.2",
        "provides": ["todo.summary.read"], "riskLevel": "L1",
    },
}


class _Found:
    def __init__(self, manifests: list[dict]) -> None:
        self.plugins = [
            type("Info", (), {"id": m["id"], "manifest": m, "directory": m.get("_directory")})()
            for m in manifests
        ]
        self.errors: list = []


@dataclass
class FakeKernel:
    enabled: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    fail_on: set[str] = field(default_factory=set)

    def enable(self, plugin_id: str, registry) -> dict:
        if plugin_id in self.fail_on:
            raise RuntimeError(f"装载 {plugin_id} 时炸了")
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
            "required": [{"id": "finance", "version": "1.2.0"}],
            "optional": [],
            "excluded": [],
        },
    }
    raw.update(over)
    return raw


def write_pack(root: Path, raw: dict, dirname: str | None = None) -> Path:
    directory = root / (dirname or raw["id"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pack.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return directory


def make_assembler(tmp_path: Path, kernel: FakeKernel, plugins: dict | None = None) -> PackAssembler:
    index = plugins if plugins is not None else PLUGINS
    return PackAssembler(
        discover=lambda: _Found(list(index.values())),
        enable=kernel.enable,
        disable=kernel.disable,
        registry=None,
        data_dir=tmp_path / "data",
    )


def plugin_dir(tmp_path: Path, plugin_id: str, body: str = "v1") -> Path:
    """造一个**真实的插件目录**（带 manifest.json 与一个源文件）。

    ★ 为什么测试要造真目录：内容摘要是对**目录内容**算的。
      只给 manifest 不给目录，摘要就算不出来——
      那条路径该由另一个测试专门覆盖（`test_missing_directory_leaves_summary_unverified`）。
      本测试要验的是「摘要算得出来时，行为对不对」。
    """
    d = tmp_path / "plugin-src" / plugin_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps({"id": plugin_id, "version": "1.0.0"}, ensure_ascii=False), encoding="utf-8"
    )
    (d / "main.py").write_text(body, encoding="utf-8")
    return d


def plugins_with_dirs(tmp_path: Path, **bodies: str) -> dict:
    """在默认插件表上，给指定的插件配上真实目录。"""
    index = {k: dict(v) for k, v in PLUGINS.items()}
    for pid, body in bodies.items():
        if pid in index:
            index[pid]["_directory"] = str(plugin_dir(tmp_path, pid, body))
    return index


# ─────────────────────────── B1 ───────────────────────────


def test_pack_format_must_be_supported() -> None:
    raw = base_pack()
    raw.pop("packFormat")
    with pytest.raises(PackError) as exc:
        parse_manifest(raw)
    assert exc.value.code == "B1-1"


def test_unknown_top_field_rejected() -> None:
    """拼错的 `requiers` 不能被当成未声明悄悄放过去。"""
    raw = base_pack(requiers=["x"])
    with pytest.raises(PackError) as exc:
        parse_manifest(raw)
    assert exc.value.code == "B1-2"
    assert "requiers" in exc.value.message


def test_id_must_equal_dirname(tmp_path: Path) -> None:
    raw = base_pack()
    manifest = parse_manifest(raw, directory=write_pack(tmp_path, raw))
    with pytest.raises(PackError) as exc:
        validate_structure(manifest, dirname="别的目录")
    assert exc.value.code == "B1-4"


def test_official_prefix_requires_kind() -> None:
    raw = base_pack(kind="local")
    manifest = parse_manifest(raw)
    with pytest.raises(PackError) as exc:
        validate_structure(manifest, dirname="official.demo")
    assert exc.value.code == "B1-5"


def test_kernel_api_incompatible_rejects_whole_pack() -> None:
    raw = base_pack(kernelApi="^99")
    manifest = parse_manifest(raw)
    with pytest.raises(PackError) as exc:
        validate_structure(manifest, dirname="official.demo")
    assert exc.value.code == "B1-8"


# ─────────────────────────── B2 ───────────────────────────


def test_excluded_overlaps_required() -> None:
    raw = base_pack()
    raw["members"]["excluded"] = ["finance"]
    manifest = parse_manifest(raw)
    with pytest.raises(PackError) as exc:
        validate_consistency(manifest, PLUGINS)
    assert exc.value.code == "B2-1"


def test_duplicate_member_rejected() -> None:
    raw = base_pack()
    raw["members"]["optional"] = [{"id": "finance", "version": "1.2.0"}]
    manifest = parse_manifest(raw)
    with pytest.raises(PackError) as exc:
        validate_consistency(manifest, PLUGINS)
    assert exc.value.code == "B2-2"


def test_budget_widening_rejected() -> None:
    """预算只能是上限，不能凭空多出一项成员没声明过的能力。"""
    raw = base_pack(permissionBudget={"objectLevel": "L1", "capabilities": ["calendar.event.read"]})
    manifest = parse_manifest(raw)
    with pytest.raises(PackError) as exc:
        validate_consistency(manifest, PLUGINS)
    assert exc.value.code == "B2-3"


def test_budget_l3_rejected() -> None:
    raw = base_pack(permissionBudget={"objectLevel": "L3", "capabilities": ["finance.entry.read"]})
    manifest = parse_manifest(raw)
    with pytest.raises(PackError) as exc:
        validate_consistency(manifest, PLUGINS)
    assert exc.value.code == "B2-5"


def test_budget_above_member_level_rejected() -> None:
    raw = base_pack(permissionBudget={"objectLevel": "L2", "capabilities": ["finance.entry.read"]})
    manifest = parse_manifest(raw)
    with pytest.raises(PackError) as exc:
        validate_consistency(manifest, PLUGINS)
    assert exc.value.code == "B2-4"


def test_secret_for_unknown_member() -> None:
    raw = base_pack(secrets=[{"key": "TOKEN", "title": "令牌", "for": "不存在的成员"}])
    manifest = parse_manifest(raw)
    with pytest.raises(PackError) as exc:
        validate_consistency(manifest, PLUGINS)
    assert exc.value.code == "B2-6"


def test_remote_source_rejected() -> None:
    raw = base_pack(source={"type": "remote", "locator": "https://example.com/x.json"})
    manifest = parse_manifest(raw)
    with pytest.raises(PackError) as exc:
        validate_consistency(manifest, PLUGINS)
    assert exc.value.code == "B2-10"


def test_third_party_member_needs_license() -> None:
    raw = base_pack()
    raw.pop("license")
    raw["members"]["required"] = [{"id": "weekly-report", "version": "0.3.2"}]
    manifest = parse_manifest(raw)
    with pytest.raises(PackError) as exc:
        validate_consistency(manifest, PLUGINS)
    assert exc.value.code == "B2-11"


# ─────────────────────────── 嵌套包（B2-7 / 8 / 9） ───────────────────────────


def test_nested_pack_cycle() -> None:
    a = parse_manifest(base_pack(id="official.a", includes=[{"id": "official.b", "version": "1.0.0"}]))
    b = parse_manifest(base_pack(id="official.b", includes=[{"id": "official.a", "version": "1.0.0"}]))
    table = {"official.a": a, "official.b": b}
    with pytest.raises(PackError) as exc:
        expand_includes(a, table.get)
    assert exc.value.code == "B2-7"
    assert "成环" in exc.value.message


def test_nested_pack_depth_limit() -> None:
    packs = {}
    for depth in range(5):
        pack_id = f"official.p{depth}"
        adds = [{"id": f"official.p{depth + 1}", "version": "1.0.0"}] if depth < 4 else []
        packs[pack_id] = parse_manifest(base_pack(id=pack_id, includes=adds))
    with pytest.raises(PackError) as exc:
        expand_includes(packs["official.p0"], packs.get)
    assert exc.value.code == "B2-7"
    assert "嵌套深度" in exc.value.message


def test_config_double_source_rejected() -> None:
    top = parse_manifest(
        base_pack(
            id="official.top",
            includes=[{"id": "official.base", "version": "1.0.0"}],
            config={"finance": {"ledger": "top"}},
        )
    )
    base_with_config = parse_manifest(
        base_pack(id="official.base", config={"finance": {"ledger": "base"}})
    )
    with pytest.raises(PackError) as exc:
        expand_includes(top, {"official.base": base_with_config}.get)
    assert exc.value.code == "B2-8"


def test_member_version_conflict_rejected() -> None:
    base = parse_manifest(
        base_pack(
            id="official.base",
            members={"required": [{"id": "finance", "version": "1.0.0"}], "optional": [], "excluded": []},
        )
    )
    top = parse_manifest(
        base_pack(id="official.top", includes=[{"id": "official.base", "version": "1.0.0"}])
    )
    with pytest.raises(PackError) as exc:
        expand_includes(top, {"official.base": base}.get)
    assert exc.value.code == "B2-9"


# ─────────────────────────── 装配流程 ───────────────────────────


def test_load_happy_path_writes_snapshot_and_archive(tmp_path: Path) -> None:
    """装载成功 → 写**装配快照** + 留档原件；**不写基线锁**。

    ★ 为什么要断言「不写基线锁」：
      §15.2 规则 2 说「锁默认只读，只有显式命令才写」。
      装载若写了基线锁，§15.3 的「摘要不符 ⇒ 硬拒绝」就永远不会触发
      ——每次装载都把基线改成当前状态，基线永远等于现状。
      ⇒ 这条断言守的是「对账基准不能被对账动作本身改写」。
    """
    raw = base_pack()
    pack_dir = write_pack(tmp_path, raw)
    kernel = FakeKernel()
    report = make_assembler(tmp_path, kernel).load(pack_dir)

    assert report.ok
    assert kernel.enabled == ["finance"]
    store = PackStore(tmp_path / "data", "official.demo")
    assert store.snapshot_path.exists(), "装载成功必须写出装配快照"
    assert store.original_path.exists(), "装载成功必须留档 pack.json 原件"
    assert not (pack_dir / "pack.lock.json").exists(), (
        "装载不得写基线锁——基线只由显式命令 `pack lock` 写"
    )


def test_user_disable_survives_load(tmp_path: Path) -> None:
    """I13：用户停用的成员，装载不得把它重新启用。"""
    raw = base_pack()
    pack_dir = write_pack(tmp_path, raw)
    store = PackStore(tmp_path / "data", "official.demo")
    store.record_adjustment("finance", AdjustmentAction.DISABLE)

    kernel = FakeKernel()
    report = make_assembler(tmp_path, kernel).load(pack_dir)

    assert kernel.enabled == [], "用户已停用，装载不得启用它"
    assert report.members[0].status is LoadStatus.SKIPPED
    assert "finance" in report.user_adjusted


def test_optional_not_installed_without_asker(tmp_path: Path) -> None:
    """不传 ask_optional → 一律不装（保守默认，不替用户决定）。"""
    raw = base_pack()
    raw["members"]["optional"] = [{"id": "todo", "version": "0.8.1"}]
    pack_dir = write_pack(tmp_path, raw)
    kernel = FakeKernel()
    report = make_assembler(tmp_path, kernel).load(pack_dir)

    assert kernel.enabled == ["finance"]
    assert any(m.id == "todo" and m.status is LoadStatus.SKIPPED for m in report.members)


def test_optional_installed_when_user_says_yes(tmp_path: Path) -> None:
    raw = base_pack()
    raw["members"]["optional"] = [{"id": "todo", "version": "0.8.1"}]
    pack_dir = write_pack(tmp_path, raw)
    kernel = FakeKernel()
    report = make_assembler(tmp_path, kernel).load(pack_dir, ask_optional=lambda _id: True)

    assert kernel.enabled == ["finance", "todo"]
    assert any(m.id == "todo" and m.status is LoadStatus.LOADED for m in report.members)


def test_dry_run_touches_nothing(tmp_path: Path) -> None:
    raw = base_pack()
    pack_dir = write_pack(tmp_path, raw)
    kernel = FakeKernel()
    report = make_assembler(tmp_path, kernel).load(pack_dir, dry_run=True)

    assert report.dry_run
    assert kernel.enabled == []
    store = PackStore(tmp_path / "data", "official.demo")
    assert not store.snapshot_path.exists(), "试运行不得写快照"


def test_failure_compensates_in_reverse(tmp_path: Path) -> None:
    raw = base_pack()
    raw["members"]["required"] = [
        {"id": "finance", "version": "1.2.0"},
        {"id": "todo", "version": "0.8.1"},
    ]
    pack_dir = write_pack(tmp_path, raw)
    kernel = FakeKernel(fail_on={"todo"})
    report = make_assembler(tmp_path, kernel).load(pack_dir)

    assert report.status == "failed"
    assert kernel.enabled == ["finance"]
    assert kernel.disabled == ["finance"], "失败必须逆序回放已生效成员的逆操作"
    assert not PackStore(tmp_path / "data", "official.demo").snapshot_path.exists()


def test_concurrent_load_is_refused(tmp_path: Path) -> None:
    """独占的键是「装配目标」，不是名字——同一目标必须串行。"""
    raw = base_pack()
    raw["members"]["optional"] = [{"id": "todo", "version": "0.8.1"}]
    pack_dir = write_pack(tmp_path, raw)
    kernel = FakeKernel()
    assembler = make_assembler(tmp_path, kernel)

    def reenter(_member_id: str) -> bool:
        assembler.load(pack_dir)  # 第二次装载同一目标
        return False

    with pytest.raises(PackBusyError):
        assembler.load(pack_dir, ask_optional=reenter)


def test_required_member_missing_fails_pack(tmp_path: Path) -> None:
    raw = base_pack()
    raw["members"]["required"] = [{"id": "ghost", "version": "1.0.0"}]
    pack_dir = write_pack(tmp_path, raw)
    report = make_assembler(tmp_path, FakeKernel()).load(pack_dir)

    assert report.status == "failed"
    assert "ghost" in (report.error or "")


def test_member_with_unprovided_requirement_degrades_not_fails(tmp_path: Path) -> None:
    """所需能力谁都提供不了 → 该成员降级，不整包失败。"""
    plugins = dict(PLUGINS)
    plugins["finance"] = {**PLUGINS["finance"], "requires": ["nobody.provides.this"]}
    raw = base_pack()
    pack_dir = write_pack(tmp_path, raw)
    kernel = FakeKernel()
    report = make_assembler(tmp_path, kernel, plugins).load(pack_dir)

    assert report.status == "loaded", "成员降级不得让整包失败"
    assert kernel.enabled == []
    assert report.members[0].status is LoadStatus.DEGRADED


# ─────────────────────────── 契约 ↔ 校验器 一致性（B1-3 补漏） ───────────────────────────
# ★ 2026-10-28：包层契约 contracts/pack.schema.json 此前不存在（只以技术报告里的
#   JSON 块活着），与校验器分叉五处。落地契约时顺手补上了校验器的一个漏检：
#   8 个可选字段（description / author / homepage / tagline / icon / entry /
#   source / onboarding）的**类型**此前完全不管——`description: 123`、
#   `source: "x"` 这类脏值能一路过校验，落到界面与审计才炸。
#   报错点离出错点很远，排查成本高。形状查在前头，错在门口。


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("description", 1),
        ("author", 123),
        ("homepage", []),
        ("tagline", 5),
        ("icon", 7),
        ("entry", 9),
        ("source", "not-an-object"),
        ("onboarding", "not-an-array"),
    ],
)
def test_optional_field_type_rejected(field: str, bad_value: object) -> None:
    """可选字段的类型必须查——脏值不许过校验。"""
    raw = base_pack()
    raw[field] = bad_value
    with pytest.raises(PackError) as exc:
        parse_manifest(raw)
    assert exc.value.code == "B1-3"
    assert field in str(exc.value)


@pytest.mark.parametrize(
    ("field", "good_value"),
    [
        ("description", "把生活事务整理成一条流水线"),
        ("author", "Alongside 团队"),
        ("homepage", "https://alongside.local"),
        ("tagline", "一次装好，一整包生活事务"),
        ("icon", "https://alongside.local/icon.png"),
        ("entry", "welcome"),
        ("source", {"type": "local", "locator": "./pack"}),
        ("onboarding", ["打开设置", "授权邮箱"]),
    ],
)
def test_optional_field_type_accepted(field: str, good_value: object) -> None:
    """合法取值必须被接受——别把门禁做成假红。"""
    raw = base_pack()
    raw[field] = good_value
    manifest = parse_manifest(raw)
    assert manifest.raw[field] == good_value


def test_includes_accepted_with_valid_pack_ref() -> None:
    """includes 是包层的一等公民（嵌套包），合法的引用不许被拒。"""
    raw = base_pack()
    raw["includes"] = [{"id": "official.base", "version": "^1.0"}]
    manifest = parse_manifest(raw)
    assert len(manifest.includes) == 1
    assert manifest.includes[0].id == "official.base"


def test_kind_community_accepted() -> None:
    """kind=community 是包层约定的社区包分类（与插件层的 third-party 不是一回事）。"""
    raw = base_pack(id="community.demo", kind="community")
    manifest = parse_manifest(raw)
    assert manifest.kind == "community"


@pytest.mark.parametrize("bad_kind", ["third-party", "derived", "OFFICIAL", ""])
def test_kind_unknown_rejected(bad_kind: str) -> None:
    """插件层的 kind 取值（third-party 等）在包层不成立——两个 kind 不同维度。"""
    raw = base_pack(kind=bad_kind)
    with pytest.raises(PackError) as exc:
        parse_manifest(raw)
    assert exc.value.code == "B1-3"


# ─────────────────────────── 失败姿态：atomic vs partial ───────────────────────────
# ★ 2026-10-28：失败姿态是**产品判断**，做成配置项（packs_failure_mode）。
#   两种姿态的行为差异必须被钉住——不然「配置项骗人」：
#   界面显示 partial、实际跑的是 atomic，用户会觉得系统在说谎。


def _three_member_pack() -> dict:
    """三个必需成员：finance / todo / weekly-report。"""
    raw = base_pack()
    raw["members"] = {
        "required": [
            {"id": "finance", "version": "1.2.0"},
            {"id": "todo", "version": "0.8.1"},
            {"id": "weekly-report", "version": "0.3.2"},
        ],
        "optional": [],
        "excluded": [],
    }
    return raw


def test_atomic_rolls_back_all_on_member_failure(tmp_path: Path) -> None:
    """atomic：中间成员失败 ⇒ 前面成功的也要逆序回滚。"""
    kernel = FakeKernel(fail_on={"todo"})
    asm = make_assembler(tmp_path, kernel)
    pack_dir = write_pack(tmp_path, _three_member_pack())
    report = asm.load(pack_dir)

    assert report.status == "failed"
    assert not report.ok
    # finance 装过又必须被撤掉
    assert "finance" in kernel.enabled
    assert "finance" in kernel.disabled, "atomic 姿态下已启用的成员必须回滚"
    assert "todo" not in kernel.enabled
    assert "weekly-report" not in kernel.enabled, "失败点之后的成员不该继续装"


def test_partial_keeps_successes_and_continues(tmp_path: Path) -> None:
    """partial：失败点之前的保留，失败点之后的继续装。"""
    kernel = FakeKernel(fail_on={"todo"})
    asm = make_assembler(tmp_path, kernel)
    asm.failure_mode = "partial"
    pack_dir = write_pack(tmp_path, _three_member_pack())
    report = asm.load(pack_dir)

    # ★ partial 下「部分成功」是**合法状态**（status=loaded + error 非空）
    assert report.status == "loaded"
    assert report.ok
    assert report.error, "部分成功必须留下说明，不能悄悄当成功"
    # finance 保留（不回滚）
    assert "finance" in kernel.enabled
    assert "finance" not in kernel.disabled, "partial 姿态下不该回滚成功的成员"
    # 失败点之后的继续装
    assert "weekly-report" in kernel.enabled, "partial 姿态下失败点之后的成员应继续装"
    # 失败的成员明确留痕
    failed = [m for m in report.members if m.status is LoadStatus.FAILED]
    assert [m.id for m in failed] == ["todo"], failed


def test_partial_reports_every_failure(tmp_path: Path) -> None:
    """partial：多个成员都失败时，每个都要留痕（不能只报第一个）。"""
    kernel = FakeKernel(fail_on={"todo", "weekly-report"})
    asm = make_assembler(tmp_path, kernel)
    asm.failure_mode = "partial"
    pack_dir = write_pack(tmp_path, _three_member_pack())
    report = asm.load(pack_dir)

    failed = sorted(m.id for m in report.members if m.status is LoadStatus.FAILED)
    assert failed == ["todo", "weekly-report"], failed
    assert "finance" in kernel.enabled
    assert "finance" not in kernel.disabled


def test_atomic_is_the_default(tmp_path: Path) -> None:
    """默认必须是 atomic——「装到一半的包」比「没装的包」更难解释。"""
    asm = make_assembler(tmp_path, FakeKernel())
    assert asm.failure_mode == "atomic"


# ─────────────────────── 摘要与基线锁（§15） ───────────────────────
# ★ 本组测试守的是本轮修掉的三个真缺陷：
#   ① 摘要曾是**全零占位**（看着像算过了，其实什么都没说）
#   ② 复用只比版本号，注释却写「摘要一致」
#   ③ 摘要不符不硬拒绝（§15.3 明写要硬拒绝）
# ★ 每条都按协议 §26 的要求写：**构造违规输入，确认它被拒绝**。


def test_digest_is_really_computed(tmp_path: Path) -> None:
    """摘要必须是**真算**的：内容变了，摘要就得变。"""
    d = plugin_dir(tmp_path, "finance", "print('v1')")
    d1 = content_digest(d)
    (d / "main.py").write_text("print('v2')", encoding="utf-8")
    d2 = content_digest(d)

    assert d1 != d2, "内容改了摘要必须变——否则摘要什么也没说"
    assert d1.startswith("sha256:") and len(d1) == 71


def test_digest_ignores_pycache(tmp_path: Path) -> None:
    """`__pycache__` 不进摘要——它是源码推导出来的，算了就是噪声。"""
    d = plugin_dir(tmp_path, "finance", "x = 1")
    before = content_digest(d)
    cache = d / "__pycache__"
    cache.mkdir()
    (cache / "main.cpython-312.pyc").write_bytes(b"\x00\x01\x02")

    assert content_digest(d) == before, "编译缓存不该影响摘要"


def test_digest_is_order_independent(tmp_path: Path) -> None:
    """摘要必须**可复现**：同样内容，文件顺序不同也算出同一个。"""
    a = tmp_path / "a"
    b = tmp_path / "b"
    for root, names in ((a, ["x.py", "y.py"]), (b, ["y.py", "x.py"])):
        root.mkdir()
        for n in names:
            (root / n).write_text(n, encoding="utf-8")

    assert content_digest(a) == content_digest(b), "摘要不能依赖目录遍历顺序"


def test_digest_mismatch_hard_rejected(tmp_path: Path) -> None:
    """★ 摘要不符 ⇒ **硬拒绝**（§15.3 第 2 行）。

    ★ 构造违规输入：清单钉了摘要，但插件目录的内容与它对不上。
      期望：装载失败，报告里说清「期望什么、实际什么」。
    """
    raw = base_pack()
    raw["members"]["required"] = [
        {"id": "finance", "version": "1.2.0", "digest": "sha256:" + "0" * 64}
    ]
    pack_dir = write_pack(tmp_path, raw)
    index = plugins_with_dirs(tmp_path, finance="real content")
    kernel = FakeKernel()

    report = make_assembler(tmp_path, kernel, index).load(pack_dir)

    assert report.status == "failed", "摘要不符必须硬拒绝，不许静默通过"
    assert kernel.enabled == [], "拒绝时不得启用任何成员"
    assert "finance" in (report.error or "")
    assert "sha256:" + "0" * 64 in (report.error or ""), "报错必须指出期望摘要"


def test_digest_mismatch_leaves_nothing_behind(tmp_path: Path) -> None:
    """拒绝是**提交点之前**的事：不得留下快照、不得启用成员。"""
    raw = base_pack()
    raw["members"]["required"] = [
        {"id": "finance", "version": "1.2.0", "digest": "sha256:" + "1" * 64}
    ]
    pack_dir = write_pack(tmp_path, raw)
    index = plugins_with_dirs(tmp_path, finance="real content")
    kernel = FakeKernel()

    report = make_assembler(tmp_path, kernel, index).load(pack_dir)

    assert report.status == "failed"
    store = PackStore(tmp_path / "data", "official.demo")
    assert not store.snapshot_path.exists(), "拒绝时不得写快照"


def test_load_does_not_rewrite_baseline_lock(tmp_path: Path) -> None:
    """★ 装载**只读**基线锁（§15.2 规则 2）。

    ★ 这条是「对账基准不能被对账动作本身改写」的地基：
      若装载重写基线，§15.3 的「摘要不符 ⇒ 硬拒绝」永远不触发。

    ★ 关键：基线里的摘要要写成**与实际一致**的值。
      若写成不一致的，本次装载会被硬拒绝（那也「没改写」基线），
      这条断言就成了**靠错误理由通过**——证明不了「装载不写基线」。
      ⇒ 必须是「装载成功 + 基线一字未动」。
    """
    raw = base_pack()
    pack_dir = write_pack(tmp_path, raw)
    index = plugins_with_dirs(tmp_path, finance="v1")
    kernel = FakeKernel()
    asm = make_assembler(tmp_path, kernel, index)

    asm.load(pack_dir)

    # 手工写一份基线锁，摘要取**实际值**（模拟显式命令 `pack lock` 生成的）
    actual = content_digest(Path(index["finance"]["_directory"]))
    baseline = pack_dir / "pack.lock.json"
    baseline.write_text(
        json.dumps(
            {
                "packId": "official.demo",
                "packVersion": "1.0.0",
                "generatedAt": "2026-10-28T00:00:00+08:00",
                "members": [
                    {"id": "finance", "version": "1.2.0", "digest": actual}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = baseline.read_text(encoding="utf-8")

    report = asm.load(pack_dir)

    assert report.ok, "基线与实际一致时不该拒绝"
    assert baseline.read_text(encoding="utf-8") == before, "装载不得改写基线锁"


def test_pack_level_digest_mismatch_blocks_load(tmp_path: Path) -> None:
    """★ **包本身**被改过 ⇒ 硬拒绝（包级摘要对账）。

    ★ 与成员级对账是**两件不同的事**：
      成员级 = 「这个成员被改过」；包级 = 「这个包被改过」。
      包级先查（assembler 的 4a 步），这样报错能直接说「是这个包被改了」，
      而不是让用户逐个成员去猜是哪个。

    ★ 怎么造出「包被改过」：改 `pack.json` 里的一个非成员字段
      （比如 description）——成员没动，但包的内容变了。
    """
    raw = base_pack()
    pack_dir = write_pack(tmp_path, raw)
    index = plugins_with_dirs(tmp_path, finance="v1")
    kernel = FakeKernel()
    asm = make_assembler(tmp_path, kernel, index)

    # 先用真实的包级摘要钉一份基线。
    from packs.digest import pack_digest

    actual_pack_digest = pack_digest(pack_dir)
    baseline = pack_dir / "pack.lock.json"
    baseline.write_text(
        json.dumps(
            {
                "packId": "official.demo",
                "packVersion": "1.0.0",
                "generatedAt": "2026-10-28T00:00:00+08:00",
                "selfDigest": actual_pack_digest,
                "members": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # ★ 改包本身（成员不动）——包级摘要会变，成员级摘要不会。
    raw2 = dict(raw)
    raw2["description"] = "这个包被人动过"
    (pack_dir / "pack.json").write_text(
        json.dumps(raw2, ensure_ascii=False), encoding="utf-8"
    )

    report = asm.load(pack_dir)
    assert not report.ok, "包被改过却装载成功——包级对账没生效"
    assert "摘要与基线不符" in (report.error or ""), report.error


def test_pack_level_digest_match_allows_load(tmp_path: Path) -> None:
    """★ 包级摘要一致 ⇒ **正常装载**（不能把「一致」也拒了）。

    ★ 这条是上一条的**反向证明**：上一条可能因为别的原因失败
      （比如成员摘要也不符），这条保证它是**包级对账**在起作用。
    """
    raw = base_pack()
    pack_dir = write_pack(tmp_path, raw)
    index = plugins_with_dirs(tmp_path, finance="v1")
    kernel = FakeKernel()
    asm = make_assembler(tmp_path, kernel, index)

    from packs.digest import pack_digest

    baseline = pack_dir / "pack.lock.json"
    baseline.write_text(
        json.dumps(
            {
                "packId": "official.demo",
                "packVersion": "1.0.0",
                "generatedAt": "2026-10-28T00:00:00+08:00",
                "selfDigest": pack_digest(pack_dir),
                "members": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = asm.load(pack_dir)
    assert report.ok, f"包级摘要一致时不该拒绝：{report.error}"


def test_snapshot_records_pack_digest_and_ownership(tmp_path: Path) -> None:
    """★ 装配快照要记**包级摘要**与**归属**。

    ★ 归属是集合（§10.2 第 4 条）：一个成员可能被多个包引入。
    """
    raw = base_pack()
    pack_dir = write_pack(tmp_path, raw)
    index = plugins_with_dirs(tmp_path, finance="v1")
    kernel = FakeKernel()
    asm = make_assembler(tmp_path, kernel, index)

    asm.load(pack_dir)

    snap_path = tmp_path / "data" / "packs" / "official.demo" / "pack.assembly.json"
    assert snap_path.exists(), "装载成功后必须写装配快照"
    payload = json.loads(snap_path.read_text(encoding="utf-8"))
    assert payload["selfDigest"].startswith("sha256:"), "快照必须记包级摘要"
    assert payload["members"][0]["fromPacks"] == ["official.demo"], "快照必须记归属"


def test_baseline_lock_mismatch_blocks_load(tmp_path: Path) -> None:
    """基线锁里记的摘要与实际不符 ⇒ 硬拒绝（这正是锁存在的意义）。"""
    raw = base_pack()
    pack_dir = write_pack(tmp_path, raw)
    index = plugins_with_dirs(tmp_path, finance="v1")
    kernel = FakeKernel()
    asm = make_assembler(tmp_path, kernel, index)

    (pack_dir / "pack.lock.json").write_text(
        json.dumps(
            {
                "packId": "official.demo",
                "packVersion": "1.0.0",
                "generatedAt": "2026-10-28T00:00:00+08:00",
                "members": [
                    {"id": "finance", "version": "1.2.0", "digest": "sha256:" + "3" * 64}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = asm.load(pack_dir)

    assert report.status == "failed", "基线不符必须硬拒绝"
    assert "finance" in (report.error or "")


def test_summary_unverified_is_recorded(tmp_path: Path) -> None:
    """算不出摘要 ⇒ 留痕（`summaryNotVerified`），但**不拒绝**。

    ★ §15.4：开发态的豁免是「主动跳过并留痕」，
      而算不出摘要是「被动没有能力发现」——同样要留痕。
    """
    raw = base_pack()
    pack_dir = write_pack(tmp_path, raw)
    # 故意不给 _directory ⇒ 摘要算不出来
    kernel = FakeKernel()

    report = make_assembler(tmp_path, kernel).load(pack_dir)

    assert report.ok, "没有基线可比时不该拒绝——第一次装本来就没有基线"
    assert "finance" in report.summary_not_verified


def test_second_load_reuses_when_digest_unchanged(tmp_path: Path) -> None:
    """第二次装载：摘要一致 ⇒ 复用，不重复启用。

    ★ 这条守的是差量装配：内容没变就不该重装。
    """
    raw = base_pack()
    pack_dir = write_pack(tmp_path, raw)
    index = plugins_with_dirs(tmp_path, finance="stable")
    kernel = FakeKernel()
    asm = make_assembler(tmp_path, kernel, index)

    asm.load(pack_dir)
    assert kernel.enabled == ["finance"]

    report = asm.load(pack_dir)

    assert kernel.enabled == ["finance"], "摘要一致时不该再启用一次"
    reused = [m for m in report.members if m.status is LoadStatus.REUSED]
    assert [m.id for m in reused] == ["finance"], reused


def test_changed_content_is_not_reused(tmp_path: Path) -> None:
    """内容变了 ⇒ **不复用**（必须重新装）。

    ★ 这条是「只比版本号」那个缺陷的钉子：
      版本号一个字没改，内容却变了——旧实现会判「摘要一致」直接复用，
      于是改动永远装不上去。
    """
    raw = base_pack()
    pack_dir = write_pack(tmp_path, raw)
    index = plugins_with_dirs(tmp_path, finance="v1")
    kernel = FakeKernel()
    asm = make_assembler(tmp_path, kernel, index)

    asm.load(pack_dir)
    assert kernel.enabled == ["finance"]

    # 改内容（版本号不动）
    (Path(index["finance"]["_directory"]) / "main.py").write_text("v2", encoding="utf-8")
    kernel.enabled.clear()

    report = asm.load(pack_dir)

    assert kernel.enabled == ["finance"], "内容变了必须重装"
    assert not [m for m in report.members if m.status is LoadStatus.REUSED]
