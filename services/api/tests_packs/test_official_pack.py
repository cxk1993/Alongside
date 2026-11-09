"""首发官方包「生活事务整理」的集成自证。

★ 这个文件测的不是「规则对不对」——那由 `test_packs.py` 的 B1/B2 测。
  它测的是**发行版里真的躺着这个包，而且它能被装配**。

★ 为什么要有这一层
-----------------
`packs/official.life-tasks/` 是仓库里**唯一**一个真包。
协议规则可以被假包测绿，但真包躺在仓库里没人碰的话，
「协议写得对」和「包真能用」之间就有一段没人走过的路。

★ 关键：这里用**真插件**（`services/api/modules/` 下真的 manifest），
  不用假数据——因为真包声明的成员版本必须与真插件**对得上**。
  用假插件表的话，包写 `finance 0.2.0` 而假表里是 `1.2.0` 也照样能过，
  那就等于什么都没验。
"""

from __future__ import annotations

import json
from pathlib import Path

from packs.assembler import PackAssembler
from packs.author import check_pack, lock_pack
from packs.roots import bundled_root, discover_pack_dirs, resolve_pack_dir
from packs.validate import validate_consistency, validate_structure

#: 仓库根（本文件在 services/api/tests_packs/ 下，往上三层到项目根）。
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODULES_DIR = _REPO_ROOT / "services" / "api" / "modules"

#: 首发官方包的 id。
PACK_ID = "official.life-tasks"

#: 包声明的成员 → 真插件的 manifest 所在模块目录名。
#: ★ 这里**不写死版本**——版本从真 manifest 读，
#:   再断言「包声明的版本 == 真插件的版本」。
#:   写死版本的话，插件升版后这个测试要么红（版本漂了）要么被人改掉（漂了就没人管了）。
#:   读真值 + 断言相等 = 插件升版时这个测试**必须**被更新，
#:   而更新的动作本身就是「有人看过这次升版」。
MEMBER_MODULES = ("finance", "todo", "calendar", "review", "push")


def _real_plugin_index() -> dict[str, dict]:
    """从**真插件目录**读出插件表（manifest + 目录）。

    ★ 刻意不用 `discover_plugins()`：那会连带跑跨插件一致性校验，
      那是另一件事（由 `test_packs_roots.py` 之类的文件负责）。
      这里只取「插件表」这一件事。
    """
    index: dict[str, dict] = {}
    for sub in sorted(p for p in _MODULES_DIR.iterdir() if p.is_dir()):
        mp = sub / "manifest.json"
        if not mp.is_file():
            continue
        raw = json.loads(mp.read_text(encoding="utf-8"))
        entry = dict(raw)
        entry["_directory"] = str(sub)
        index[str(raw.get("id", sub.name))] = entry
    return index


def _pack_dir() -> Path:
    return bundled_root() / PACK_ID


# ─────────────────────────── 仓库里真的有这个包 ───────────────────────────


def test_bundled_root_is_the_project_packs_dir() -> None:
    """发行版包根是**项目根**的 `packs/`。

    ★ 为什么要单独测这一条：`packs/` 与 `services/api/packs/` 同名，
      放错层级会出现「import packs 导到发行目录」这种串味——
      症状极怪（`packs.roots` 导到发行目录里的某个插件），
      而根因只是一层路径。
    """
    assert bundled_root().name == "packs"
    assert bundled_root().parent == _REPO_ROOT
    assert bundled_root() != _REPO_ROOT / "services" / "api" / "packs"


def test_official_pack_exists_in_bundled_root() -> None:
    """发行版里躺着首发官方包。"""
    d = _pack_dir()
    assert (d / "pack.json").is_file(), f"发行版里没有 {PACK_ID}"


def test_pack_is_discoverable_as_bundled() -> None:
    """它能被**发现**——出现在发行版包列表里，来源标 bundled。"""
    found = dict((pid, src) for pid, _d, src in discover_pack_dirs(_REPO_ROOT / "data"))
    assert found.get(PACK_ID) == "bundled"


def test_pack_id_equals_directory_name() -> None:
    """包 id 必须等于目录名（B1-4）。

    ★ 这一条是「反证」：协议规定 id 与目录名必须相等。
      如果哪天有人把目录改名而没改 id，这里立刻红。
    """
    raw = json.loads((_pack_dir() / "pack.json").read_text(encoding="utf-8"))
    assert raw["id"] == _pack_dir().name


# ─────────────────────────── 包与真插件对得上 ───────────────────────────


def test_pack_passes_structure_validation() -> None:
    """B1 结构校验通过。"""
    raw = json.loads((_pack_dir() / "pack.json").read_text(encoding="utf-8"))
    from packs.validate import parse_manifest

    manifest = parse_manifest(raw, directory=_pack_dir())
    validate_structure(manifest, dirname=_pack_dir().name)


def test_pack_members_match_real_plugin_versions() -> None:
    """★ 包声明的每个成员都存在，且**版本与真插件一致**。

    ★ 这是本文件最重要的一条：它验的是「包没有在说谎」。
      协议层能验「版本号格式对」，但验不了「这个版本号是对的」——
      后者只有拿真 manifest 比才知道。
    """
    index = _real_plugin_index()
    raw = json.loads((_pack_dir() / "pack.json").read_text(encoding="utf-8"))
    members = raw["members"]
    declared = list(members.get("required", [])) + list(members.get("optional", []))
    assert declared, "包没有声明任何成员"

    for member in declared:
        pid = member["id"]
        assert pid in index, f"成员 {pid} 在本机没有对应的真插件"
        assert member["version"] == index[pid]["version"], (
            f"成员 {pid} 声明版本 {member['version']}，"
            f"真插件是 {index[pid]['version']}"
        )


def test_pack_passes_consistency_against_real_plugins() -> None:
    """B2 一致性校验（用真插件表）通过。

    ★ 这一条验的是**权限预算**这类需要真插件能力表才能判的规则：
      预算是必需成员 `provides` 之并集的子集（B2-3）、
      objectLevel 不高于成员最高风险级（B2-4）。
    """
    index = _real_plugin_index()
    raw = json.loads((_pack_dir() / "pack.json").read_text(encoding="utf-8"))
    from packs.validate import parse_manifest

    manifest = parse_manifest(raw, directory=_pack_dir())
    validate_consistency(manifest, index)


def test_permission_budget_is_a_subset_of_member_capabilities() -> None:
    """★ 预算 ⊆ 必需成员声明之并集（B2-3）—— 显式重述一遍。

    ★ 为什么值得单独写一条：`validate_consistency` 已经查了这条，
      但它是「校验器没报错」；这条是「预算确实没超出成员的声明」，
      读起来就是那句协议原文。规则变更时，测试标题会先于人察觉。
    """
    index = _real_plugin_index()
    raw = json.loads((_pack_dir() / "pack.json").read_text(encoding="utf-8"))
    budget = raw.get("permissionBudget") or {}
    declared: set[str] = set()
    for member in raw["members"]["required"]:
        declared.update(index[member["id"]].get("provides", []))
    assert set(budget.get("capabilities", [])) <= declared


# ─────────────────────────── 作者命令面跑得通 ───────────────────────────


def test_check_pack_passes() -> None:
    """`pack check` 通过（用真插件表算摘要）。"""
    result = check_pack(_pack_dir(), discover=_real_discover())
    assert result.ok, f"check 失败：{result.errors}"
    assert not result.warnings, f"check 有告警：{result.warnings}"


def test_lock_file_is_committed_and_matches() -> None:
    """基线锁存在，且每个成员的摘要与真插件当前内容一致。

    ★ 为什么要验「与真插件当前内容一致」而不只是「锁文件存在」：
      锁的**价值**在于钉住内容。锁在但摘要已经过时 = 锁骗人。
    """
    lock_path = _pack_dir() / "pack.lock.json"
    assert lock_path.is_file(), "没有提交基线锁"

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["packId"] == PACK_ID
    assert lock["members"], "锁里没有成员"

    index = _real_plugin_index()
    from packs.digest import content_digest

    for entry in lock["members"]:
        plugin = index[entry["id"]]
        actual = content_digest(Path(plugin["_directory"]))
        assert entry["digest"] == actual, (
            f"成员 {entry['id']} 的锁摘要已过时：锁={entry['digest']} 实际={actual}"
        )


def test_lock_pack_is_idempotent() -> None:
    """再跑一次 lock，锁内容不变（幂等）。"""
    before = (_pack_dir() / "pack.lock.json").read_text(encoding="utf-8")
    lock_pack(_pack_dir(), discover=_real_discover(), now="2026-01-01T00:00:00+08:00")
    after = (_pack_dir() / "pack.lock.json").read_text(encoding="utf-8")
    # generatedAt 会变，selfDigest 也会变（它对整个包目录算，包含锁自己？——见下）
    before_data = json.loads(before)
    after_data = json.loads(after)
    assert before_data["members"] == after_data["members"]


# ─────────────────────────── 能被装配器真装配 ───────────────────────────


class _FakeKernel:
    """假内核：只记启用了谁。"""

    def __init__(self) -> None:
        self.enabled: list[str] = []
        self.disabled: list[str] = []

    def enable(self, plugin_id: str, registry) -> dict:
        self.enabled.append(plugin_id)
        return {}

    def disable(self, plugin_id: str, registry) -> dict:
        self.disabled.append(plugin_id)
        return {}


def _real_discover():
    """给 `check_pack` / `lock_pack` 用的发现器（形状与真发现器一致）。"""

    class _Found:
        def __init__(self, plugins: list) -> None:
            self.plugins = plugins
            self.errors: list = []

    class _Info:
        def __init__(self, entry: dict) -> None:
            self.id = entry["id"]
            self.manifest = entry
            self.directory = entry["_directory"]

    return lambda: _Found([_Info(e) for e in _real_plugin_index().values()])


def test_assembler_can_load_the_official_pack(tmp_path: Path) -> None:
    """★ 装配器能真把这个包装起来（必需成员全启用）。

    ★ 这是端到端的那一环：前面所有测试都在验「清单写对」，
      这一条验「装配器读得懂它、并且真能把成员装上」。
    """
    kernel = _FakeKernel()
    assembler = PackAssembler(
        discover=_real_discover(),
        enable=kernel.enable,
        disable=kernel.disable,
        registry=None,
        data_dir=tmp_path / "data",
    )
    report = assembler.load(_pack_dir(), ask_optional=lambda _pid: True)
    assert report.ok, f"装载失败：{report.error}"
    assert report.status == "loaded"
    for pid in ("finance", "todo", "calendar"):
        assert pid in kernel.enabled, f"必需成员 {pid} 没被启用"


def test_assembler_dry_run_does_not_enable_anything(tmp_path: Path) -> None:
    """干跑不产生副作用（不启用任何插件）。

    ★ 干跑的意义就是「先看看会发生什么」——
      如果它照样启用插件，那它就不是干跑，是「装作是干跑的装载」。
    """
    kernel = _FakeKernel()
    assembler = PackAssembler(
        discover=_real_discover(),
        enable=kernel.enable,
        disable=kernel.disable,
        registry=None,
        data_dir=tmp_path / "data",
    )
    report = assembler.load(_pack_dir(), ask_optional=lambda _pid: True, dry_run=True)
    assert not kernel.enabled, "干跑却启用了插件"
    assert report.status in {"loaded", "dry-run"}


def test_resolve_pack_dir_finds_the_bundled_pack(tmp_path: Path) -> None:
    """包目录解析能找到发行版那份。"""
    d = resolve_pack_dir(PACK_ID, tmp_path / "data")
    assert d is not None
    assert d == _pack_dir()
