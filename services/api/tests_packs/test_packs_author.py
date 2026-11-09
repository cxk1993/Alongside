"""包作者命令面的自证失败测试（协议 §25.4 / §26）。

★ 守的是三条纪律：
  1. `check` 与 `diff` **一个字节都不写**（能只读地跑在任意目录上）。
  2. `lock` 是**基线锁的唯一写入口**，且写的是基线锁（不是装配快照）。
  3. 命令面**不自己扫插件目录**——发现器注入，口径与内核一致。

★ 每条都按 §26 的要求写：**构造违规输入，确认它被拒绝 / 被发现**。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packs.author import (
    check_pack,
    diff_pack,
    format_check,
    format_diff,
    lock_pack,
)
from packs.digest import content_digest


class _Found:
    def __init__(self, manifests: list[dict]) -> None:
        self.plugins = [
            type("Info", (), {"id": m["id"], "manifest": m, "directory": m.get("_directory")})()
            for m in manifests
        ]
        self.errors: list = []


def _discoverer(index: dict):
    return lambda: _Found(list(index.values()))


def _plugin_dir(tmp_path: Path, plugin_id: str, body: str = "v1") -> Path:
    d = tmp_path / "plugin-src" / plugin_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps({"id": plugin_id, "version": "1.0.0"}, ensure_ascii=False), encoding="utf-8"
    )
    (d / "main.py").write_text(body, encoding="utf-8")
    return d


def _pack(tmp_path: Path, **over) -> Path:
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
    d = tmp_path / "official.demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pack.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return d


def _index(tmp_path: Path, body: str = "v1") -> dict:
    return {
        "finance": {
            "id": "finance",
            "kind": "builtin",
            "version": "1.2.0",
            "provides": ["finance.entry.read"],
            "riskLevel": "L1",
            "_directory": str(_plugin_dir(tmp_path, "finance", body)),
        }
    }


# ─────────────────────────── check ───────────────────────────


def test_check_reports_unknown_field(tmp_path: Path) -> None:
    """拼错的字段必须被**发现**（而不是悄悄放过去）。"""
    pack = _pack(tmp_path, requiers=["x"])
    result = check_pack(pack, discover=_discoverer(_index(tmp_path)))
    assert not result.ok
    assert any("requiers" in e for e in result.errors), result.errors


def test_check_writes_nothing(tmp_path: Path) -> None:
    """★ `check` 一个字节都不写（§25.4「只读地跑在任意目录上」）。"""
    pack = _pack(tmp_path)
    before = sorted(p.name for p in pack.iterdir())
    check_pack(pack, discover=_discoverer(_index(tmp_path)))
    assert sorted(p.name for p in pack.iterdir()) == before, "check 不得写文件"


def test_check_warns_when_member_not_installed(tmp_path: Path) -> None:
    """成员不在本机 ⇒ **警告**不是错误（作者可能在没装插件的机器上写包）。"""
    pack = _pack(tmp_path)
    result = check_pack(pack, discover=_discoverer({}))
    assert result.ok, "成员没装不该让校验失败"
    assert any("finance" in w for w in result.warnings), result.warnings


# ─────────────────────────── lock ───────────────────────────


def test_lock_writes_baseline_lock(tmp_path: Path) -> None:
    """`lock` 写**基线锁**（在包目录里），且摘要取**实际值**。"""
    pack = _pack(tmp_path)
    index = _index(tmp_path)
    lock = lock_pack(pack, discover=_discoverer(index), now="2026-10-28T00:00:00+08:00")

    baseline = pack / "pack.lock.json"
    assert baseline.exists(), "lock 必须写基线锁"
    actual = content_digest(Path(index["finance"]["_directory"]))
    assert lock.members[0].digest == actual, "基线锁里的摘要必须是实际算出来的值"
    assert lock.members[0].digest != "sha256:" + "0" * 64, "不许用全零占位"


def test_lock_records_pack_level_digest(tmp_path: Path) -> None:
    """★ 基线锁要记**包级摘要**——「这个包本身是什么」。

    ★ 此前这个字段有定义有读写，却**永远 null**：没有任何写入者。
      一个永远为空的字段不证明任何事。
    """
    pack = _pack(tmp_path)
    lock = lock_pack(pack, discover=_discoverer(_index(tmp_path)), now="t")
    assert lock.self_digest is not None, "基线锁必须记包级摘要"
    assert lock.self_digest.startswith("sha256:")
    assert lock.self_digest != "sha256:" + "0" * 64, "不许用全零占位"

    # ★ 落盘后读回来还在（不是只活在内存里）。
    on_disk = json.loads((pack / "pack.lock.json").read_text(encoding="utf-8"))
    assert on_disk["selfDigest"] == lock.self_digest


def test_lock_excludes_itself_from_pack_digest(tmp_path: Path) -> None:
    """★ 包级摘要必须**排除基线锁自己**（自指的摘要恒等于现状）。

    ★ 为什么：基线锁住在包目录里，若把它算进包级摘要：
      写一次锁 ⇒ 摘要变一次 ⇒ 下次对账「包摘要与基线不符」恒成立。
      ⇒ 那不是摘要，是恒等式——恒等式什么也不证明。
    """
    pack = _pack(tmp_path)
    first = lock_pack(pack, discover=_discoverer(_index(tmp_path)), now="t")
    # ★ 锁已经落盘了。现在再算一次包级摘要，必须与第一次**一样**。
    second = lock_pack(pack, discover=_discoverer(_index(tmp_path)), now="t2")
    assert first.self_digest == second.self_digest, (
        "重写锁不应改变包级摘要——说明基线锁被算进了自己的摘要里（自指）"
    )


def test_lock_records_member_ownership(tmp_path: Path) -> None:
    """★ 基线锁要记**归属**（谁带进来的）——归属是集合，不是单值。"""
    pack = _pack(tmp_path)
    lock = lock_pack(pack, discover=_discoverer(_index(tmp_path)), now="t")
    assert lock.members[0].from_packs == ("official.demo",), (
        "成员归属应记下是哪个包带进来的"
    )


def test_lock_rejects_bad_shape(tmp_path: Path) -> None:
    """★ 写侧必须**挡住坏形状**——坏锁的代价由用户承担。

    ★ 最狠的一种：摘要格式写错 ⇒ 后续每次装载都判「摘要不符」
      ⇒ 用户永远装不上这个包，而且报错指向「内容被改过」，完全指错方向。
    """
    from packs.model import LockedMember, PackError, PackLock
    from packs.state import write_baseline_lock

    with pytest.raises(PackError) as exc:
        write_baseline_lock(
            tmp_path,
            PackLock(
                pack_id="official.demo",
                pack_version="1.0.0",
                generated_at="t",
                members=(
                    LockedMember(id="finance", version="1.0.0", digest="sha256:不是摘要"),
                ),
            ),
        )
    assert "digest" in exc.value.message, exc.value.message


def test_lock_rejects_unknown_top_field(tmp_path: Path) -> None:
    """★ 锁的字段是**封闭**的——多出来的字段必须被挡住。"""
    from packs.model import PackError
    from packs.validate import validate_lock_payload

    with pytest.raises(PackError) as exc:
        validate_lock_payload(
            {
                "packId": "official.demo",
                "packVersion": "1.0.0",
                "generatedAt": "t",
                "多出来的字段": 1,
            },
            kind="基线锁",
        )
    assert "多出来的字段" in exc.value.message, exc.value.message


def test_lock_rejects_plugin_shaped_pack_id(tmp_path: Path) -> None:
    """★ 锁的 packId 必须是**包的**命名空间，不能是插件 id 的形状。

    ★ 否则「这份锁属于哪个包」就答不上来。
    """
    from packs.model import PackError
    from packs.validate import validate_lock_payload

    with pytest.raises(PackError) as exc:
        validate_lock_payload(
            {"packId": "finance", "packVersion": "1.0.0", "generatedAt": "t"},
            kind="基线锁",
        )
    assert "packId" in exc.value.message, exc.value.message


def test_lock_reads_legacy_pack_digest_field(tmp_path: Path) -> None:
    """★ 读时**兼容旧字段名** `packDigest`，写时只写规范名 `selfDigest`。

    ★ 为什么不能因为规范改名就让旧锁读不出来：
      读不出来 ⇒ 被当成「基线坏了」⇒ 硬拒绝 ⇒ 用户白白装不上包。
    """
    from packs.state import read_baseline_lock

    d = tmp_path / "official.demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pack.lock.json").write_text(
        json.dumps(
            {
                "packId": "official.demo",
                "packVersion": "1.0.0",
                "generatedAt": "t",
                "packDigest": "sha256:" + "c" * 64,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    lock = read_baseline_lock(d)
    assert lock is not None
    assert lock.self_digest == "sha256:" + "c" * 64, "旧字段名也必须能读出来"


def test_lock_is_the_only_writer(tmp_path: Path) -> None:
    """★ 基线锁的**唯一**写入口是 `lock`（§15.2 规则 2）。

    ★ 这条守的是「谁在什么时候改了基线必须有唯一答案」——
      装载流程、校验命令都不得写它。
    """
    pack = _pack(tmp_path)
    lock_pack(pack, discover=_discoverer(_index(tmp_path)), now="t")
    baseline = pack / "pack.lock.json"
    before = baseline.read_text(encoding="utf-8")

    check_pack(pack, discover=_discoverer(_index(tmp_path)))
    diff_pack(pack, discover=_discoverer(_index(tmp_path)))

    assert baseline.read_text(encoding="utf-8") == before, "check / diff 不得改基线锁"


def test_lock_does_not_touch_data_dir(tmp_path: Path) -> None:
    """`lock` **不碰数据目录**——基线跟着包走，不跟着机器走。"""
    pack = _pack(tmp_path)
    data_dir = tmp_path / "data"
    lock_pack(pack, discover=_discoverer(_index(tmp_path)), now="t")
    assert not data_dir.exists(), "lock 不得在数据目录留东西"


# ─────────────────────────── diff ───────────────────────────


def test_diff_lists_new_member(tmp_path: Path) -> None:
    """基线里没有的成员 ⇒ 「新增」。"""
    pack = _pack(tmp_path)
    index = _index(tmp_path)
    lock_pack(pack, discover=_discoverer(index), now="t")

    # 清单加一个成员
    raw = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    raw["members"]["optional"] = [{"id": "todo", "version": "0.8.1"}]
    (pack / "pack.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = diff_pack(pack, discover=_discoverer(index))
    assert "todo" in result.added, result


def test_diff_detects_content_change(tmp_path: Path) -> None:
    """★ 内容变了（版本没变）必须被**发现**——这是锁的全部价值。"""
    pack = _pack(tmp_path)
    index = _index(tmp_path, body="v1")
    lock_pack(pack, discover=_discoverer(index), now="t")

    (Path(index["finance"]["_directory"]) / "main.py").write_text("v2", encoding="utf-8")

    result = diff_pack(pack, discover=_discoverer(index))
    assert "finance" in result.digest_changed, result
    assert "finance" not in result.unchanged


def test_diff_writes_nothing(tmp_path: Path) -> None:
    """★ `diff` 一个字节都不写。"""
    pack = _pack(tmp_path)
    index = _index(tmp_path)
    lock_pack(pack, discover=_discoverer(index), now="t")
    before = sorted(p.name for p in pack.iterdir())
    diff_pack(pack, discover=_discoverer(index))
    assert sorted(p.name for p in pack.iterdir()) == before, "diff 不得写文件"


def test_diff_without_baseline_lists_everything_as_new(tmp_path: Path) -> None:
    """没有基线 ⇒ 全部算「新增」（不是错误）。"""
    pack = _pack(tmp_path)
    result = diff_pack(pack, discover=_discoverer(_index(tmp_path)))
    assert result.added == ("finance",), result


# ─────────────────────────── 输出格式 ───────────────────────────


def test_format_check_and_diff_are_readable(tmp_path: Path) -> None:
    """命令输出必须是**人话**（§25.4「输出人话报告」）。"""
    bad = _pack(tmp_path, requiers=["x"])
    result = check_pack(bad, discover=_discoverer(_index(tmp_path)))
    text = format_check(result)
    assert "official.demo" in text
    assert "requiers" in text

    # ★ `diff` 对一个**结构就坏**的包应当直接报错（而不是给出一份「差异」）：
    #   清单读不出来时，「将会发生什么」这个问题就没有答案。
    #   ⇒ 那是错误路径，不是「人话报告」的场合。
    good = _pack(tmp_path / "second")
    d = diff_pack(good, discover=_discoverer(_index(tmp_path)))
    dtext = format_diff(d)
    assert "新增" in dtext
