"""包目录解析的自证失败测试（`packs/roots.py`）。

★ 为什么这些测试要这么写
-----------------------
这个文件治的病是「**三处各算各的**」——
路由算 `data_dir / "packs" / <id>`、装配器算 `packs_root / <id>`、
boot 传 `data_dir / "packs"`。

⇒ 而「三处各算各的」这种病，**光看代码看不出来**：
  三处都「对」，合起来才不对。
⇒ 所以测试必须**跨模块**验证「同一个 id 在各处解析到同一个目录」，
  而不是各测各的函数。

★ 全部不依赖 DB、不依赖 FastAPI —— 纯路径逻辑。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from packs.roots import (
    BUNDLED_DIRNAME,
    DATA_DIRNAME,
    MANIFEST_NAME,
    bundled_root,
    data_root,
    discover_pack_dirs,
    resolve_pack_dir,
)


def _make_pack(root: Path, pack_id: str, *, member: str = "finance") -> Path:
    """在 `root` 下造一个「像包」的目录（有 pack.json）。"""
    d = root / pack_id
    d.mkdir(parents=True, exist_ok=True)
    (d / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "id": pack_id,
                "format": 1,
                "required": [{"id": member, "version": "1.0.0"}],
            }
        ),
        encoding="utf-8",
    )
    return d


# ─────────────────────────── 基本解析 ───────────────────────────


def test_data_root_is_under_data_dir(tmp_path: Path) -> None:
    """★ 数据包根 = 数据目录下的 `packs/`。

    「数据库、备份、日志、包目录本来就在同一个 `data/` 下」
    ——数据根从数据库路径推出来（`packs.boot.resolve_data_dir`），
    所以包目录必须是它的子目录，不能是别的什么地方。
    """
    assert data_root(tmp_path) == tmp_path / DATA_DIRNAME


def test_resolve_finds_pack_in_data_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _make_pack(data_dir / DATA_DIRNAME, "official.life")

    found = resolve_pack_dir("official.life", data_dir)

    assert found is not None
    assert found == data_dir / DATA_DIRNAME / "official.life"


def test_resolve_returns_none_when_absent(tmp_path: Path) -> None:
    """找不到是**正常状态**（用户还没装），不是异常。"""
    assert resolve_pack_dir("nothing.here", tmp_path / "data") is None


def test_resolve_rejects_empty_pack_id(tmp_path: Path) -> None:
    """空 id 不该被当成「当前目录」。"""
    assert resolve_pack_dir("", tmp_path / "data") is None


def test_directory_without_manifest_is_not_a_pack(tmp_path: Path) -> None:
    """★ 判据是**有 pack.json**，不是「有目录」。

    数据目录里还躺着 registry.json、state.json、pack.assembly.json
    等等——那些都不是包。若按「有目录就算」，
    装包时会把留档文件当成包来读。
    """
    data_dir = tmp_path / "data"
    (data_dir / DATA_DIRNAME / "not-a-pack").mkdir(parents=True)

    assert resolve_pack_dir("not-a-pack", data_dir) is None


# ─────────────────────────── 优先级 ───────────────────────────


def test_data_dir_wins_over_bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 同 id 两处都有 ⇒ **数据目录那份赢**。

    理由：用户装了本地包，说明他要的就是本地那份；
    发行版那份是「默认提供的」，不是「用户选的」。
    """
    bundled = tmp_path / "dist" / BUNDLED_DIRNAME
    monkeypatch.setattr("packs.roots.bundled_root", lambda: bundled)

    data_dir = tmp_path / "data"
    _make_pack(bundled, "dup")
    _make_pack(data_dir / DATA_DIRNAME, "dup")

    found = resolve_pack_dir("dup", data_dir)

    assert found == data_dir / DATA_DIRNAME / "dup"


def test_bundled_used_when_data_dir_lacks_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """发行版包在数据目录里没有时，应当被找到。"""
    bundled = tmp_path / "dist" / BUNDLED_DIRNAME
    monkeypatch.setattr("packs.roots.bundled_root", lambda: bundled)
    _make_pack(bundled, "official.life")

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    found = resolve_pack_dir("official.life", data_dir)

    assert found == bundled / "official.life"


def test_extra_root_wins_over_data_dir(tmp_path: Path) -> None:
    """★ 显式注入的根排在数据目录**之前**。

    「显式 > 默认」——调用方明确说出来的位置，
    比调用方没说的默认约定更该被尊重。
    """
    data_dir = tmp_path / "data"
    _make_pack(data_dir / DATA_DIRNAME, "dup")
    extra = tmp_path / "custom"
    _make_pack(extra, "dup")

    found = resolve_pack_dir("dup", data_dir, extra_roots=(extra,))

    assert found == extra / "dup"


# ─────────────────────────── 发现 ───────────────────────────


def test_discover_lists_both_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = tmp_path / "dist" / BUNDLED_DIRNAME
    monkeypatch.setattr("packs.roots.bundled_root", lambda: bundled)

    data_dir = tmp_path / "data"
    _make_pack(data_dir / DATA_DIRNAME, "local.one")
    _make_pack(bundled, "official.two")

    found = discover_pack_dirs(data_dir)
    ids = {pid for pid, _dir, _src in found}

    assert ids == {"local.one", "official.two"}


def test_discover_dedupes_same_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 同 id 两处都有时**只列一份**（数据目录那份）。

    ★ 这条与 `resolve_pack_dir` 的优先级**必须同口径**——
      否则「列表里有」与「能装配」会指向两个不同的目录。
      这正是本文件存在的原因：一个改了另一个没改，就会分叉。
    """
    bundled = tmp_path / "dist" / BUNDLED_DIRNAME
    monkeypatch.setattr("packs.roots.bundled_root", lambda: bundled)

    data_dir = tmp_path / "data"
    _make_pack(bundled, "dup")
    _make_pack(data_dir / DATA_DIRNAME, "dup")

    found = discover_pack_dirs(data_dir)

    assert len(found) == 1
    pid, path, source = found[0]
    assert pid == "dup"
    assert path == data_dir / DATA_DIRNAME / "dup"
    assert source == "local"


def test_discover_skips_non_pack_dirs(tmp_path: Path) -> None:
    """留档目录（state.json 之类）不该被列成包。

    ★ 只断言**本地**那部分为空，不断言整个列表为空：
      发行版根（`packs/`）里躺着真的官方包，它是**该被列出来的**。
      早先这个断言写的是「整个列表为空」——那只是因为当时发行版里没有包。
      断言写成「整个列表为空」的话，等真包一落地，测试就红，
      而红的原因跟被测的规则（本地留档不算包）毫无关系。
      ⇒ 断言必须对准**被测的那条规则**，而不是当时的仓库状态。
    """
    data_dir = tmp_path / "data"
    packs = data_dir / DATA_DIRNAME
    packs.mkdir(parents=True)
    (packs / "registry.json").write_text("{}", encoding="utf-8")
    (packs / "some-pack").mkdir()

    local = [(pid, src) for pid, _d, src in discover_pack_dirs(data_dir) if src == "local"]
    assert local == []


def test_discover_on_missing_root_is_empty(tmp_path: Path) -> None:
    """根目录不存在时返回空列表——不抛。

    ★ 同上：断的是「本地根不存在」这条路径，
      所以只看本地来源那一半。
    """
    local = [
        (pid, src) for pid, _d, src in discover_pack_dirs(tmp_path / "nope") if src == "local"
    ]
    assert local == []


# ─────────────────────────── 跨模块同口径 ───────────────────────────


def test_router_and_assembler_resolve_to_same_dir(tmp_path: Path) -> None:
    """★ 跨模块一致性：路由与装配器对同一个 id 必须给出**同一个目录**。

    ★ 这条是本文件的核心判据。
      早先路由算 `data_dir / "packs" / <id>`、装配器算 `packs_root / <id>`——
      两处都「对」（各自自洽），合起来才不对：
      「界面能看见这个包、点装配却找不到」。

    ★ 所以测试不能只测 `resolve_pack_dir` 本身
      ——那只能证明「这个函数自洽」，证明不了「大家都用它」。
    """
    from modules.packs.router import _pack_dir

    data_dir = tmp_path / "data"
    made = _make_pack(data_dir / DATA_DIRNAME, "official.life")

    resolved = resolve_pack_dir("official.life", data_dir)
    routed = _pack_dir(data_dir, "official.life")

    assert resolved == made
    assert routed == made


def test_router_falls_back_to_data_path_when_missing(tmp_path: Path) -> None:
    """★ 解析不到时路由**退回数据目录那条路径**（不抛、不返回 None）。

    因为调用方要用它判「这个包在不在」（404 / pack_dir_exists），
    必须永远拿得到一个可判断的路径。
    """
    from modules.packs.router import _pack_dir

    data_dir = tmp_path / "data"
    got = _pack_dir(data_dir, "not.installed")

    assert got == data_dir / DATA_DIRNAME / "not.installed"
    assert not got.is_dir()


# ─────────────────────────── 发行版包根 ───────────────────────────


def test_bundled_root_is_project_level_not_api_level() -> None:
    """★ 发行版包根是**项目根**下的 `packs/`，不是 `services/api/packs/`。

    ★ 为什么这条要钉死：`services/api/packs/` 是**Python 包名**，
      而项目根的 `packs/` 是**发行结构**（随发行版走的包目录）。
      两个都叫「packs」——放错层级就会出现
      「import packs 导到了发行目录」这种极难查的串味。

    ★ 判据写成「相对 `services/api` 往上两级」——
      比「名字对不对」强，因为它对目录改名也成立。
    """
    root = bundled_root()
    api_root = Path(__file__).resolve().parent.parent  # services/api

    assert root.name == BUNDLED_DIRNAME
    # 发行版包根 = services/api 的**上上级**下的 packs/
    assert root == api_root.parent.parent / BUNDLED_DIRNAME
    # 且**不等于** Python 包目录本身
    assert root != api_root / BUNDLED_DIRNAME


def test_bundled_root_missing_is_not_an_error() -> None:
    """发行版没带任何官方包是**合法状态**，不该抛异常。

    ★ 判据：返回的路径**照样可用**（调用方 `.is_dir()` 判空即可），
      而不是抛「找不到」。
    """
    root = bundled_root()

    assert isinstance(root, Path)
    assert root.is_absolute()
