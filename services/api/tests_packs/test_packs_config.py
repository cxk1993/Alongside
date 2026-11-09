"""包提供的配置值 —— 自证失败测试。

★ 为什么要单独一个文件
-------------------
配置落地是本轮**新补**的运行时能力（协议 §11 / §16.1 / §19.3）：
此前 `packs/validate.py` 会**解析** `config`、`expand_includes` 会**裁决归属**，
但装配器**从头到尾没有读过它**。

⇒ 「包给了配置值」这件事，协议写了、校验器查了、**运行时没做**。
这正是「文档里写了规则而代码没实现 = 规则不存在」要防的那种裂缝。

★ 判据来自协议原文，不是实现自说自话
------------------------------------
| 协议位置 | 规则 | 本文件的哪条测试守着 |
|---|---|---|
| §16.1 第 1 条 | 按层序应用，整段替换，最后写入者胜 | `test_用户值优先于包值` |
| §16.1 第 3 条 | 每次覆盖都要留痕 | `test_覆盖留痕记层与路径` |
| §19.3 硬要求② | `overridden` 不能省 | `test_覆盖留痕记层与路径` |
| §11 | `config.<id>.enabled` 是成员的启用状态 | `test_enabled_false_不启用该成员` |
| §21.4 规则一 | 用户调整优先于包的期望 | `test_用户明确启用过_包说关也照样启用` |
| §12 第 4 条 | 成员降级，不是整包失败 | `test_单项写不进去_不毁整包` |

★ 「自证失败」的含义
-------------------
不是「跑一遍看绿不绿」，而是**构造一个违规输入，确认它被拒绝**。
所以每条测试都刻意构造了「如果实现写错了就会通过」的那个反面情形。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from packs.assembler import PackAssembler
from packs.config import (
    ACTION_KEEP,
    ACTION_SAME,
    ACTION_SKIP,
    ACTION_UPDATE,
    ACTION_WRITE,
    ENABLED_KEY,
    applied_after,
    plan_config,
)
from packs.model import PackManifest
from packs.state import PackStore

# ─────────────────────────── 测试用清单 ───────────────────────────


def _manifest(
    *,
    pack_id: str = "official.life-tasks",
    config: dict[str, dict[str, Any]] | None = None,
    required: tuple[str, ...] = ("finance", "todo"),
) -> PackManifest:
    """造一个最小清单（不经过 `parse_manifest`——那层由 validate 的测试守着）。"""
    from packs.model import MemberRef

    return PackManifest(
        pack_format=1,
        id=pack_id,
        name="测试包",
        version="1.0.0",
        kind="official",
        kernel_api="1.0.0",
        required=tuple(MemberRef(id=mid, version="1.0.0") for mid in required),
        config=config or {},
    )


# ─────────────────────────── 归属判据 ───────────────────────────


def test_没人写过_包补上默认值() -> None:
    """包给的值没人写过 ⇒ 写进去。"""
    plan = plan_config(
        _manifest(config={"finance": {"ledger": "top"}}),
        current_settings={},
        applied={},
    )
    assert [d.action for d in plan.decisions] == [ACTION_WRITE]
    assert plan.decisions[0].value == "top"


def test_是包自己写的_包可以更新它() -> None:
    """当前值就是包上次写的那个 ⇒ 包这次给新值 ⇒ 允许覆盖。"""
    plan = plan_config(
        _manifest(config={"finance": {"ledger": "new"}}),
        current_settings={"finance": {"ledger": "old"}},
        applied={"finance": {"ledger": "old"}},
    )
    assert [d.action for d in plan.decisions] == [ACTION_UPDATE]
    assert plan.decisions[0].value == "new"
    assert plan.decisions[0].current == '"old"'


def test_值没变_判为无变化() -> None:
    """当前值 = 包写的值 = 包这次给的值 ⇒ 无变化（但仍算「包的值生效」）。"""
    plan = plan_config(
        _manifest(config={"finance": {"ledger": "same"}}),
        current_settings={"finance": {"ledger": "same"}},
        applied={"finance": {"ledger": "same"}},
    )
    assert [d.action for d in plan.decisions] == [ACTION_SAME]


def test_用户改过_包不许动() -> None:
    """★ 核心判据：当前值与包记录的不一致 ⇒ 值归用户 ⇒ 包不许动。"""
    plan = plan_config(
        _manifest(config={"finance": {"ledger": "pack-wants"}}),
        current_settings={"finance": {"ledger": "user-changed"}},
        applied={"finance": {"ledger": "pack-wrote-earlier"}},
    )
    assert [d.action for d in plan.decisions] == [ACTION_KEEP]
    assert plan.writes == []


def test_用户改过_留痕说清归因() -> None:
    """用户的值赢了 ⇒ 也要留痕，且 `layer` 记 `user`。"""
    plan = plan_config(
        _manifest(config={"finance": {"ledger": "pack-wants"}}),
        current_settings={"finance": {"ledger": "user-changed"}},
        applied={"finance": {"ledger": "pack-wrote-earlier"}},
    )
    kept = plan.kept
    assert len(kept) == 1
    assert kept[0]["layer"] == "user"
    assert kept[0]["path"] == "config.finance.ledger"
    assert kept[0]["current"] == '"user-changed"'


def test_包没声明的键_包不碰() -> None:
    """★ §11.1「整段替换，不做深合并」⇒ 包没声明的 key，包不许动。

    判据：计划里**不该出现**这个键的任何裁决。
    """
    plan = plan_config(
        _manifest(config={"finance": {"ledger": "top"}}),
        current_settings={"finance": {"ledger": "old", "currency": "CNY"}},
        applied={"finance": {"ledger": "old"}},
    )
    keys = {d.key for d in plan.decisions}
    assert "currency" not in keys


def test_config_指向未声明的成员_记为跳过() -> None:
    """config 指了包外的成员 ⇒ 不静默忽略，记一条跳过。"""
    plan = plan_config(
        _manifest(config={"ghost": {"x": 1}}),
        current_settings={},
        applied={},
    )
    assert [d.action for d in plan.decisions] == [ACTION_SKIP]
    assert "未声明" in (plan.decisions[0].reason or "")


# ─────────────────────────── 留痕（§19.3） ───────────────────────────


def test_覆盖留痕记层与路径() -> None:
    """★ §19.3 的 `overridden` 不能省。

    形状：`{member, path, layer, current}`。
    `layer` 记的是**包 id**（协议示例是 `local.my-pack`），不是成员 id。
    """
    plan = plan_config(
        _manifest(pack_id="local.my-pack", config={"finance": {"ledger": "top"}}),
        current_settings={},
        applied={},
    )
    over = plan.overridden
    assert len(over) == 1
    assert over[0]["member"] == "finance"
    assert over[0]["path"] == "config.finance.ledger"
    assert over[0]["layer"] == "local.my-pack"


def test_无变化也算包的值生效() -> None:
    """★ §19.3 硬要求是「用户必须能看出『我看到的值是哪个包给的』」。

    所以「还是包给的那个值」也必须能看出来——否则用户看到值却答不出它哪来的。
    """
    plan = plan_config(
        _manifest(config={"finance": {"ledger": "same"}}),
        current_settings={"finance": {"ledger": "same"}},
        applied={"finance": {"ledger": "same"}},
    )
    assert len(plan.overridden) == 1
    assert plan.overridden[0]["layer"] == "official.life-tasks"


# ─────────────────────────── enabled 键 ───────────────────────────


def test_enabled_false_不落设置_只进停用提示() -> None:
    """★ `config.<id>.enabled` 是**启用状态**，不是设置项。

    而启用状态归用户调整层管（§21.4 规则一），
    装载**不写**用户调整层（§19.2 步骤 10）
    ⇒ 它不进 `plugin_setting`，只进「本次别启用这个成员」的提示。
    """
    plan = plan_config(
        _manifest(config={"finance": {ENABLED_KEY: False}}),
        current_settings={},
        applied={},
    )
    assert plan.decisions == []
    assert plan.disable_hints == ["finance"]


def test_enabled_true_没有额外含义() -> None:
    """`enabled: true` 就是默认（启用），不产生任何裁决。"""
    plan = plan_config(
        _manifest(config={"finance": {ENABLED_KEY: True}}),
        current_settings={},
        applied={},
    )
    assert plan.decisions == []
    assert plan.disable_hints == []


# ─────────────────────────── 记录簿 ───────────────────────────


def test_记录簿只记包写进去的() -> None:
    """用户的值不进记录簿——它不是包能主张的东西。"""
    plan = plan_config(
        _manifest(config={"finance": {"ledger": "top", "other": "x"}}),
        current_settings={"finance": {"other": "user"}},
        applied={},
    )
    result = applied_after(plan, {}, only={("finance", "ledger")})
    assert result == {"finance": {"ledger": "top"}}


def test_写失败的键不进记录簿() -> None:
    """写失败的键不能让下次以为「这个值还是包写的」。"""
    plan = plan_config(
        _manifest(config={"finance": {"a": 1, "b": 2}}),
        current_settings={},
        applied={},
    )
    result = applied_after(plan, {}, only={("finance", "a")})
    assert result == {"finance": {"a": 1}}


def test_记录簿读不出来当没写过(tmp_path: Path) -> None:
    """记录簿是**我们自己**写的，坏了/没有都按「没写过」处理——不是错误。

    理由：包层第一次装载时它必然不存在。
    """
    store = PackStore(tmp_path, "official.life-tasks")
    assert store.load_config_applied() == {}


def test_记录簿往返(tmp_path: Path) -> None:
    store = PackStore(tmp_path, "official.life-tasks")
    store.save_config_applied({"finance": {"ledger": "top"}})
    assert store.load_config_applied() == {"finance": {"ledger": "top"}}
    # 文件在**数据目录**下（跟着机器走），不在包目录里。
    assert store.config_applied_path.name == "config.applied.json"
    assert store.config_applied_path.parent.name == "official.life-tasks"
    payload = json.loads(store.config_applied_path.read_text(encoding="utf-8"))
    assert payload["packId"] == "official.life-tasks"


# ─────────────────────────── 装配器里的落地 ───────────────────────────


class _FakePlugins:
    """极简的「发现结果」——装配器只用到 `.plugins` 与成员的目录。"""

    def __init__(self, ids: list[str], root: Path) -> None:
        self.plugins = [_FakePlugin(pid, root) for pid in ids]
        self._by_id = {p.id: p for p in self.plugins}

    def get(self, plugin_id: str) -> Any:
        return self._by_id[plugin_id]


class _FakePlugin:
    def __init__(self, plugin_id: str, root: Path) -> None:
        self.id = plugin_id
        self.manifest: dict[str, Any] = {"id": plugin_id, "version": "1.0.0"}
        self.directory = root / plugin_id
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "manifest.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )


def _assembler(
    tmp_path: Path,
    plugins: _FakePlugins,
    *,
    settings: dict[str, dict[str, Any]] | None = None,
    write_ok: bool = True,
) -> tuple[PackAssembler, dict[str, dict[str, Any]]]:
    """造一个装配器，注入假的 enable/disable 与设置读写。

    ★ 设置读写是**注入的**——装配器不碰数据库。
      这里用字典当数据库，把「写了什么」记下来供断言。
    """
    store: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (settings or {}).items()}

    def read_settings(plugin_id: str) -> dict[str, Any]:
        return dict(store.get(plugin_id) or {})

    def write_settings(plugin_id: str, values: dict[str, Any]) -> dict[str, Any]:
        if not write_ok:
            raise RuntimeError("设置校验失败")
        store.setdefault(plugin_id, {}).update(values)
        return dict(store[plugin_id])

    assembler = PackAssembler(
        discover=lambda: plugins,
        enable=lambda pid, reg: {"id": pid},
        disable=lambda pid, reg: {"id": pid},
        registry=None,
        data_dir=tmp_path / "data",
        read_settings=read_settings,
        write_settings=write_settings,
    )
    return assembler, store


def _pack_dir(tmp_path: Path, *, config: dict[str, Any]) -> Path:
    """造一个包目录（`pack.json` + 一个成员插件目录）。

    ★ 目录名必须**等于包 id**（协议 B1-4）——
      所以目录叫 `official.life-tasks`，不叫 `pack`。
    """
    pack = tmp_path / "official.life-tasks"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "pack.json").write_text(
        json.dumps(
            {
                "packFormat": 1,
                "id": "official.life-tasks",
                "name": "生活事务整理",
                "version": "1.0.0",
                "kind": "official",
                "kernelApi": "1.0.0",
                "members": {"required": [{"id": "finance", "version": "1.0.0"}]},
                "config": config,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return pack


def test_装配时配置落地(tmp_path: Path) -> None:
    """★ 端到端：包里给了配置 ⇒ 装载后设置里真的有这个值。"""
    plugins = _FakePlugins(["finance"], tmp_path / "plugins")
    assembler, store = _assembler(tmp_path, plugins)
    pack = _pack_dir(tmp_path, config={"finance": {"ledger": "top"}})

    report = assembler.load(pack)

    assert report.ok, report.error
    assert store["finance"]["ledger"] == "top"


def test_装配时用户值不被覆盖(tmp_path: Path) -> None:
    """★ 端到端：用户改过的值 ⇒ 装载后**原样不动**。

    ★ 这是本轮修掉的**最严重的一处**：原实现按成员整批 upsert，
      把被判为「保留用户值」的键也用包的值覆盖了——
      用户的修改当场被抹掉，而报告里还写着「保留了用户的值」。
    """
    plugins = _FakePlugins(["finance"], tmp_path / "plugins")
    assembler, store = _assembler(
        tmp_path, plugins, settings={"finance": {"ledger": "user-changed"}}
    )
    pack = _pack_dir(tmp_path, config={"finance": {"ledger": "pack-wants"}})

    report = assembler.load(pack)

    assert report.ok, report.error
    assert store["finance"]["ledger"] == "user-changed"


def test_装配时部分键被写_部分键保留(tmp_path: Path) -> None:
    """★ 同一个成员里，一个键包写、一个键用户写 ⇒ 各归各的。"""
    plugins = _FakePlugins(["finance"], tmp_path / "plugins")
    assembler, store = _assembler(
        tmp_path, plugins, settings={"finance": {"currency": "CNY"}}
    )
    pack = _pack_dir(
        tmp_path, config={"finance": {"ledger": "top", "currency": "USD"}}
    )

    report = assembler.load(pack)

    assert report.ok, report.error
    assert store["finance"]["ledger"] == "top"
    assert store["finance"]["currency"] == "CNY"


def test_装配时包更新自己写的键_不动用户改过的键(tmp_path: Path) -> None:
    """★ 本轮修掉的**最严重一处**的正面场景。

    时间线：
      ① 第一次装载：包写 `ledger=old`（用户没写过）⇒ 包的值。
      ② 用户改：`ledger=user-changed` ⇒ 值归用户。
      ③ 第二次装载：包给 `ledger=new` ⇒ **包不许动它**。

    ★ 为什么这条最容易漏：
      第 ③ 步里「包写的键」与「用户改过的键」是**同一个键**。
      若实现按成员整批 upsert，用户的修改当场被抹掉——
      而报告里还写着「保留了用户的值」。
    """
    plugins = _FakePlugins(["finance"], tmp_path / "plugins")
    data_dir = tmp_path / "data"
    pack = _pack_dir(tmp_path, config={"finance": {"ledger": "old"}})

    # ── ① 第一次装载：包写进去 ──
    assembler1, store1 = _assembler(tmp_path, plugins)
    report1 = assembler1.load(pack)
    assert report1.ok, report1.error
    assert store1["finance"]["ledger"] == "old"

    # ── ② 用户改：直接改「数据库」 ──
    store1["finance"]["ledger"] = "user-changed"

    # ── ③ 第二次装载：包给新值，但不许覆盖用户的 ──
    pack2 = _pack_dir(tmp_path, config={"finance": {"ledger": "new"}})
    # ★ 用**同一个 data_dir**，才能读到上一次留下的「我写过什么」记录。
    assembler2 = PackAssembler(
        discover=lambda: plugins,
        enable=lambda pid, reg: {"id": pid},
        disable=lambda pid, reg: {"id": pid},
        registry=None,
        data_dir=data_dir,
        read_settings=lambda pid: dict(store1.get(pid) or {}),
        write_settings=lambda pid, values: (
            store1.setdefault(pid, {}).update(values) or dict(store1[pid])
        ),
    )
    report2 = assembler2.load(pack2)

    assert report2.ok, report2.error
    assert store1["finance"]["ledger"] == "user-changed", "用户的值被包覆盖了"


def test_单项写不进去_不毁整包(tmp_path: Path) -> None:
    """★ §12 第 4 条同一条纪律：成员降级，不是整包失败。

    写配置写不进去 ⇒ 成员**照样启用**，包**照样成立**。
    """
    plugins = _FakePlugins(["finance"], tmp_path / "plugins")
    assembler, _store = _assembler(tmp_path, plugins, write_ok=False)
    pack = _pack_dir(tmp_path, config={"finance": {"ledger": "top"}})

    report = assembler.load(pack)

    assert report.ok, report.error
    assert any(m.id == "finance" and m.status.value == "loaded" for m in report.members)


def test_enabled_false_装配时真不启用(tmp_path: Path) -> None:
    """★ `config.<id>.enabled: false` ⇒ 这个成员**不启用**。"""
    plugins = _FakePlugins(["finance"], tmp_path / "plugins")
    enabled: list[str] = []

    def enable(pid: str, reg: Any) -> dict[str, Any]:
        enabled.append(pid)
        return {"id": pid}

    assembler = PackAssembler(
        discover=lambda: plugins,
        enable=enable,
        disable=lambda pid, reg: {"id": pid},
        registry=None,
        data_dir=tmp_path / "data",
    )
    pack = _pack_dir(tmp_path, config={"finance": {ENABLED_KEY: False}})

    report = assembler.load(pack)

    assert enabled == []
    assert any(
        m.id == "finance" and m.status.value == "skipped" for m in report.members
    )


def test_没接入设置读写_如实说没落地(tmp_path: Path) -> None:
    """★ 没接入 `read_settings` / `write_settings` ⇒ 配置**不落地**，
    而且**如实说**——不假装成功。

    ★ 为什么这条重要：装配器**不碰数据库**。
      忘了接线时若「静默跳过」，用户会以为包给了配置、实际没给——
      而配置没给往往表现为「插件行为不对」，极难归因。
    """
    plugins = _FakePlugins(["finance"], tmp_path / "plugins")
    assembler = PackAssembler(
        discover=lambda: plugins,
        enable=lambda pid, reg: {"id": pid},
        disable=lambda pid, reg: {"id": pid},
        registry=None,
        data_dir=tmp_path / "data",
    )
    pack = _pack_dir(tmp_path, config={"finance": {"ledger": "top"}})

    report = assembler.load(pack)

    assert report.ok, report.error
    # 报告里没有「配置已落地」的留痕 —— 因为确实没落地。
    assert report.overridden == ()


def test_装载报告带覆盖留痕(tmp_path: Path) -> None:
    """★ §19.3：`overridden` 必须出现在报告里。"""
    plugins = _FakePlugins(["finance"], tmp_path / "plugins")
    assembler, _store = _assembler(tmp_path, plugins)
    pack = _pack_dir(tmp_path, config={"finance": {"ledger": "top"}})

    report = assembler.load(pack)

    assert report.overridden
    assert report.overridden[0]["path"] == "config.finance.ledger"
    assert report.overridden[0]["layer"] == "official.life-tasks"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
