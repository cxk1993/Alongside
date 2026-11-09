"""包目录（catalog）的并表逻辑自证。

★ 这里测的是**纯逻辑**：两张表（可发现的 / 已登记的）并成一张。
  查成员启用态要连数据库，那部分由路由层负责，不在这里。

★ 核心判据是「三个状态分得开」：
  可发现 / 已登记 / 已装配 —— 它们**不是一回事**。
  合成一个字段的话，界面上就说不出「它存在、但还没装」。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from packs.catalog import build_catalog
from packs.roots import DATA_DIRNAME


def _write_pack(root: Path, pack_id: str, **head: object) -> Path:
    d = root / pack_id
    d.mkdir(parents=True, exist_ok=True)
    raw = {
        "packFormat": 1,
        "id": pack_id,
        "name": head.pop("name", "示例包"),
        "version": head.pop("version", "1.0.0"),
        "kind": "local",
        "kernelApi": "^1",
        "members": {"required": [], "optional": [], "excluded": []},
    }
    raw.update(head)
    (d / "pack.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return d


def _data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    (d / DATA_DIRNAME).mkdir(parents=True, exist_ok=True)
    return d


def _local(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只取**本地**来源的行。

    ★ 为什么每次都要过滤：发行版根里躺着真的官方包，
      它会被一并列出来。断言写成「整个列表等于 [...]」的话，
      官方包一落地测试就红——而红的原因跟被测的规则毫无关系。
      ⇒ 断言必须对准**被测的那条规则**，而不是当时的仓库状态。
    """
    return [r for r in rows if r["source"] == "local"]


# ─────────────────────────── 三态分得开 ───────────────────────────


def test_discovered_but_not_registered_is_visible(tmp_path: Path) -> None:
    """★ 可发现但未登记的包**必须出现在列表里**。

    ★ 这是本文件的核心判据。发行版自带的官方包就是这种状态：
      它躺在项目根 `packs/` 里，不需要被登记就在那儿。
      早先的列表只列「已登记的」⇒ 官方包在界面上根本看不见。
    """
    data_dir = _data_dir(tmp_path)
    _write_pack(data_dir / DATA_DIRNAME, "local.thing")

    rows = build_catalog(data_dir, [], is_installed=lambda _pid: False)
    local = _local(rows)

    assert [r["id"] for r in local] == ["local.thing"]
    row = local[0]
    assert row["registered"] is False
    assert row["installed"] is False
    # ★ 存在（可发现）这件事本身要被表达出来
    assert row["pack_dir_exists"] is True


def test_registered_and_installed_are_separate_flags(tmp_path: Path) -> None:
    """★ 「已登记」与「已装配」是两个独立字段，不许合成一个。

    ★ 协议 §6 的区分：登记是**声明**，装配是**有副作用的动作**。
      「登记过但没装配」是完全合法的状态（试装过了、还没决定）。
    """
    data_dir = _data_dir(tmp_path)
    pkg = _write_pack(data_dir / DATA_DIRNAME, "local.thing")
    registry = [{"id": "local.thing", "path": str(pkg), "source": "local"}]

    rows = build_catalog(data_dir, registry, is_installed=lambda _pid: True)
    local = _local(rows)

    assert len(local) == 1
    row = local[0]
    assert row["registered"] is True
    assert row["installed"] is True


def test_registered_but_not_installed(tmp_path: Path) -> None:
    """登记过、没装配 —— 两个标志不同。"""
    data_dir = _data_dir(tmp_path)
    pkg = _write_pack(data_dir / DATA_DIRNAME, "local.thing")
    registry = [{"id": "local.thing", "path": str(pkg), "source": "local"}]

    rows = build_catalog(data_dir, registry, is_installed=lambda _pid: False)
    local = _local(rows)

    row = local[0]
    assert row["registered"] is True
    assert row["installed"] is False


# ─────────────────────────── 半残条目 ───────────────────────────


def test_registered_but_directory_gone_still_listed(tmp_path: Path) -> None:
    """★ 登记过但目录被删 —— 仍要在列表里，且要能看出目录不在。

    ★ 为什么不能省掉：它不在列表里的话，用户永远不知道自己的登记里有死条目，
      界面上「这个包不见了」会变成「我好像没登记过」。
    """
    data_dir = _data_dir(tmp_path)
    registry = [{"id": "local.ghost", "path": "/nowhere/local.ghost", "source": "local"}]

    rows = build_catalog(data_dir, registry, is_installed=lambda _pid: False)
    local = _local(rows)

    assert [r["id"] for r in local] == ["local.ghost"]
    row = local[0]
    assert row["registered"] is True
    assert row["pack_dir_exists"] is False


# ─────────────────────────── 展示字段 ───────────────────────────


def test_row_carries_display_fields(tmp_path: Path) -> None:
    """★ 一行里带得上名字、版本、描述 —— 界面上不能只显示一个 id。

    ★ 官方包叫「生活事务整理」，用户看到 `official.life-tasks` 是没意义的。
    """
    data_dir = _data_dir(tmp_path)
    _write_pack(
        data_dir / DATA_DIRNAME,
        "local.thing",
        name="生活事务整理",
        version="1.2.3",
        description="账单、待办、日程",
        icon="wallet",
        tagline="收拢到一处",
    )

    rows = build_catalog(data_dir, [], is_installed=lambda _pid: False)
    local = _local(rows)

    row = local[0]
    assert row["name"] == "生活事务整理"
    assert row["version"] == "1.2.3"
    assert row["description"] == "账单、待办、日程"
    assert row["icon"] == "wallet"
    assert row["tagline"] == "收拢到一处"


def test_broken_manifest_does_not_break_the_list(tmp_path: Path) -> None:
    """★ 清单坏了不许让整个列表挂掉。

    ★ 列表是**展示**用途。坏包照样要被列出来（它有 `pack.json`），
      只是名字显示成 id —— 「它在，但它坏得读不出名字」，
      这个状态本身就该看得见，而不是让用户看到一片空白。
    """
    data_dir = _data_dir(tmp_path)
    d = data_dir / DATA_DIRNAME / "local.broken"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pack.json").write_text("{ 这不是 JSON", encoding="utf-8")

    rows = build_catalog(data_dir, [], is_installed=lambda _pid: False)
    local = _local(rows)

    assert [r["id"] for r in local] == ["local.broken"]
    # 名字退回 id
    assert local[0]["name"] == "local.broken"


def test_empty_registry_and_no_packs_is_empty_list(tmp_path: Path) -> None:
    """什么都没登记、也没有包 ⇒ 空列表（不抛）。"""
    data_dir = _data_dir(tmp_path)
    assert _local(build_catalog(data_dir, [], is_installed=lambda _pid: False)) == []


# ─────────────────────────── 发行版官方包 ───────────────────────────


def test_bundled_official_pack_shows_up_as_discovered(tmp_path: Path) -> None:
    """★ 发行版里的官方包在列表里，来源标 `bundled`，且未登记。

    ★ 这条直接对着「官方包在界面上看不见」那个病：
      发行版根里真的躺着 `official.life-tasks`，
      而它**不需要**被登记才存在。
    """
    from packs.roots import bundled_root

    rows = build_catalog(tmp_path / "data", [], is_installed=lambda _pid: False)

    by_id = {r["id"]: r for r in rows}
    if "official.life-tasks" not in by_id:
        # 发行版里还没有官方包时这条测不了（不是错，只是还没落地）。
        pytest.skip("发行版里还没有官方包")
    row = by_id["official.life-tasks"]
    assert row["source"] == "bundled"
    assert row["registered"] is False
    assert row["name"] == "生活事务整理"
    assert row["version"] == "1.0.0"
    assert row["path"] == str(bundled_root() / "official.life-tasks")
