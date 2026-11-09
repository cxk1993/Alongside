"""包层**接线层**与启动装配的测试。

★ 与 `test_packs.py` 的分工
-------------------------
`test_packs.py` 测的是**规则层**（B1/B2 校验、装配流程、补偿语义）——
它不需要内核，是个自洽的纯逻辑单元。

本文件测的是**接线层**：
  - 「已装配包登记表」读写（幂等、坏表不谎报）
  - 装配器怎么用内核能力构造出来
  - 启动装配入口（`boot_packs`）的配置行为与降级纪律

★ 为什么接线层要单独一个文件
--------------------------
因为它**碰内核**（读 `core.config`、拿 `get_settings`）。
和规则层混在一起时，规则的失败会被接线的失败盖住——
「是规则错了还是没接上」分不清。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from packs.assembler import PackAssembler
from packs.boot import (
    boot_packs,
    pack_settings,
    resolve_data_dir,
    summarize_reports,
)
from packs.model import LoadReport, LoadStatus, MemberOutcome, PackError
from packs.wiring import (
    build_assembler,
    read_registry,
    register_pack,
    registry_path,
    unregister_pack,
)

# ─────────────────────────── 接线层与启动装配 ───────────────────────────
# ★ 2026-10-28：包层接进内核的通路。
#   这些测试守住三件事：
#     ① 装配**接得上**（内核能力能喂给装配器）；
#     ② 装配**不拖垮启动**（任何失败都只降级，不抛）；
#     ③ 配置项**真的生效**（不是摆设——界面显示 partial、实际跑 atomic 就是骗人）。



# ── 接线层：登记表 ──


def test_registry_empty_is_not_error(tmp_path: Path) -> None:
    """登记表不存在 = 「还没装过任何包」，是**正常状态**，不是故障。"""
    assert read_registry(tmp_path) == []


def test_register_pack_is_idempotent(tmp_path: Path) -> None:
    """同一个包重复登记是**更新**，不是追加——否则启动时装配两遍。"""
    register_pack(tmp_path, pack_id="official.life-tasks", path="/tmp/p1")
    register_pack(tmp_path, pack_id="official.life-tasks", path="/tmp/p2")
    entries = read_registry(tmp_path)
    assert len(entries) == 1
    assert entries[0]["path"] == "/tmp/p2"


def test_unregister_pack(tmp_path: Path) -> None:
    assert unregister_pack(tmp_path, "x") is False  # 本来就没有
    register_pack(tmp_path, pack_id="x", path="/tmp/p")
    assert unregister_pack(tmp_path, "x") is True
    assert read_registry(tmp_path) == []


def test_broken_registry_raises_not_silent_empty(tmp_path: Path) -> None:
    """登记表坏了必须抛——谎报成「没装过」会导致重复装配。"""
    registry_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    registry_path(tmp_path).write_text("{ 坏 json", encoding="utf-8")
    with pytest.raises(PackError):
        read_registry(tmp_path)


def test_registry_missing_packs_key_raises(tmp_path: Path) -> None:
    registry_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    registry_path(tmp_path).write_text('{"version": 1}', encoding="utf-8")
    with pytest.raises(PackError):
        read_registry(tmp_path)


# ── 接线层：装配器构造 ──


def test_build_assembler_returns_pack_assembler(tmp_path: Path) -> None:
    asm = build_assembler(
        discover=lambda: [],
        enable=lambda *a: {},
        disable=lambda *a: {},
        registry=None,
        data_dir=tmp_path,
    )
    assert isinstance(asm, PackAssembler)


# ── 数据目录推导 ──


def test_resolve_data_dir_from_db_path() -> None:
    """数据根 = 数据库所在目录（一个真相源，别各配各的）。"""
    assert resolve_data_dir("./data/alongside.db").name == "data"


def test_resolve_data_dir_absolute_path_kept(tmp_path: Path) -> None:
    """绝对路径的父目录原样保留（平台无关：Windows 下 `/x` 会带盘符，故用 tmp_path 造绝对路径）。"""
    db = tmp_path / "alongside.db"
    p = resolve_data_dir(str(db))
    assert p == tmp_path, p
    assert p.is_absolute()


# ── 配置项真的生效 ──


def test_pack_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("PACKS_ENABLED", "PACKS_AUTOLOAD", "PACKS_FAILURE_MODE"):
        monkeypatch.delenv(k, raising=False)
    cfg = pack_settings()
    assert cfg["enabled"] is False, "默认关：不做没被要求的事"
    assert cfg["autoload"] is True
    assert cfg["failure_mode"] == "atomic"


def test_pack_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACKS_ENABLED", "true")
    monkeypatch.setenv("PACKS_AUTOLOAD", "false")
    monkeypatch.setenv("PACKS_FAILURE_MODE", "partial")
    cfg = pack_settings()
    assert cfg["enabled"] is True
    assert cfg["autoload"] is False
    assert cfg["failure_mode"] == "partial"


def test_pack_settings_bad_failure_mode_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """配错了不静默：取默认并记一笔（但不让启动失败）。"""
    monkeypatch.setenv("PACKS_FAILURE_MODE", "nonsense")
    assert pack_settings()["failure_mode"] == "atomic"


def test_boot_packs_disabled_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACKS_ENABLED", "false")
    assert boot_packs(app=object()) == []


def test_boot_packs_manual_mode_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """手动模式：启动时不装（等用户显式触发）。"""
    monkeypatch.setenv("PACKS_ENABLED", "true")
    monkeypatch.setenv("PACKS_AUTOLOAD", "false")
    assert boot_packs(app=object()) == []


def test_boot_packs_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """包层挂了不许拖垮启动——它��增强，不是前提。"""
    monkeypatch.setenv("PACKS_ENABLED", "true")
    monkeypatch.setenv("PACKS_AUTOLOAD", "true")
    # app 是 object() ⇒ 取不到 registry ⇒ 包层内部应吞掉
    assert boot_packs(app=object()) == []


# ── 报告摘要 ──


def test_summarize_reports_keeps_member_traces() -> None:
    """逐成员留痕是必带字段——不论成败，用户都要能问出「每个成员怎么了」。"""

    report = LoadReport(
        pack_id="official.demo",
        status="failed",
        error="炸了",
        members=(
            MemberOutcome(id="finance", status=LoadStatus.LOADED, version="1.0.0"),
            MemberOutcome(id="todo", status=LoadStatus.FAILED, version="0.8.1", reason="炸了"),
        ),
    )
    out = summarize_reports([report])
    assert len(out) == 1
    assert out[0]["pack_id"] == "official.demo"
    assert out[0]["ok"] is False
    assert [m["id"] for m in out[0]["members"]] == ["finance", "todo"]
    assert out[0]["members"][1]["reason"] == "炸了"
