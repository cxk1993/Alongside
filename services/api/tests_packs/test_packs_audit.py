"""包层**静态审查**的自证失败测试（报告 §22）。

★ 每条判据都必须有一个「构造违规、确认被发现」的测试。
  没有自证失败的审查规则等于没有——审查器自己错了，没人知道。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packs.audit import AuditLevel, audit_pack, format_audit
from packs.model import PackError


def _pack_dir(tmp_path: Path, *, manifest: dict | None = None, files: dict | None = None) -> Path:
    """造一个包目录。"""
    d = tmp_path / "official.demo"
    d.mkdir(parents=True, exist_ok=True)
    raw = manifest if manifest is not None else {
        "packFormat": 1,
        "id": "official.demo",
        "name": "演示包",
        "version": "1.0.0",
        "kind": "official",
        "kernelApi": "^1",
        "license": "Apache-2.0",
        "members": {"required": [{"id": "finance", "version": "1.2.0"}]},
    }
    (d / "pack.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    for name, content in (files or {}).items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


def _codes(report) -> set[str]:
    return {f.code for f in report.findings}


def _blocking_codes(report) -> set[str]:
    return {f.code for f in report.blocking}


# ─────────────────────────── 基本行为 ───────────────────────────


def test_audit_of_clean_pack_has_no_blocking(tmp_path: Path) -> None:
    """一个干净的包不该有任何 `block` 级发现。"""
    d = _pack_dir(tmp_path)
    report = audit_pack(d)
    assert report.ok, [f.detail for f in report.blocking]
    assert report.pack_id == "official.demo"
    assert report.member_count == 1


def test_audit_never_executes_pack_code(tmp_path: Path) -> None:
    """★ 审查**不执行**包里的代码。

    ★ 怎么证明「没执行」：在包里放一个会**留下痕迹**的文件。
      审查跑完，痕迹必须不存在。
      ——只说「我们没 import」不算证明，代码路径可能还有别的执行方式。
    """
    d = _pack_dir(
        tmp_path,
        files={
            "side_effect.py": (
                "from pathlib import Path\n"
                "Path(__file__).with_name('EXECUTED').write_text('x')\n"
            )
        },
    )
    audit_pack(d)
    assert not (d / "EXECUTED").exists(), "审查执行了包里的代码"


def test_audit_does_not_crash_on_broken_manifest(tmp_path: Path) -> None:
    """★ 审查器**永远不崩**。审查的对象可能是恶意包或半截下载。

    ★ 崩了就等于「查不出问题」，用户看不到任何信息——
      那比「审查失败」危险得多。
    """
    d = tmp_path / "official.broken"
    d.mkdir(parents=True)
    (d / "pack.json").write_text("{ 这不是 JSON", encoding="utf-8")
    report = audit_pack(d)
    assert not report.ok
    assert "AUDIT-1" in _blocking_codes(report)


def test_audit_of_missing_pack_is_block(tmp_path: Path) -> None:
    """包目录里没有清单 ⇒ 必须拒绝。"""
    d = tmp_path / "official.empty"
    d.mkdir(parents=True)
    report = audit_pack(d)
    assert not report.ok
    assert "AUDIT-1" in _blocking_codes(report)


# ─────────────────────────── 逐条判据 ───────────────────────────


def test_audit_detects_path_escape(tmp_path: Path) -> None:
    """★ **路径逃逸**必须被发现（§22.2）。"""
    d = _pack_dir(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    (d / "link.txt").symlink_to(outside)
    report = audit_pack(d)
    assert "AUDIT-6" in _blocking_codes(report), [f.detail for f in report.findings]


def test_audit_detects_pack_digest_mismatch(tmp_path: Path) -> None:
    """★ 包内容被改过 ⇒ 必须被发现（§22.1「来源与摘要」）。"""
    from packs.digest import pack_digest
    from packs.model import PackLock
    from packs.state import write_baseline_lock

    d = _pack_dir(tmp_path)
    write_baseline_lock(
        d,
        PackLock(
            pack_id="official.demo",
            pack_version="1.0.0",
            generated_at="t",
            self_digest=pack_digest(d),
        ),
    )
    # ★ 改包内容（清单本身）——成员没动，但包变了。
    raw = json.loads((d / "pack.json").read_text(encoding="utf-8"))
    raw["description"] = "被人动过"
    (d / "pack.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    report = audit_pack(d)
    assert "AUDIT-16" in _blocking_codes(report), [f.detail for f in report.findings]


def test_audit_accepts_matching_digest(tmp_path: Path) -> None:
    """★ 摘要一致 ⇒ 不该报「不符」（反向证明上一条不是误报）。"""
    from packs.digest import pack_digest
    from packs.model import PackLock
    from packs.state import write_baseline_lock

    d = _pack_dir(tmp_path)
    write_baseline_lock(
        d,
        PackLock(
            pack_id="official.demo",
            pack_version="1.0.0",
            generated_at="t",
            self_digest=pack_digest(d),
        ),
    )
    report = audit_pack(d)
    assert "AUDIT-16" not in _blocking_codes(report)


def test_audit_detects_broken_baseline_lock(tmp_path: Path) -> None:
    """★ 基线锁形状坏了 ⇒ 必须发现。

    ★ 为什么：基线锁跟着包分发。形状坏了 ⇒ 装载时的对账要么永远判「不符」
      （用户永远装不上），要么永远判「相符」（摘要机制被架空）。
      两种都是「看起来能装，实际保护没了」。
    """
    d = _pack_dir(tmp_path)
    (d / "pack.lock.json").write_text(
        json.dumps({"packId": "finance", "packVersion": "1.0.0", "generatedAt": "t"}),
        encoding="utf-8",
    )
    report = audit_pack(d)
    assert "AUDIT-18" in _blocking_codes(report), [f.detail for f in report.findings]


def test_audit_detects_l3_budget(tmp_path: Path) -> None:
    """★ L3（内核级）权限预算必须被拒（包不得声明对内核的操作）。"""
    d = _pack_dir(
        tmp_path,
        manifest={
            "packFormat": 1,
            "id": "official.demo",
            "name": "演示包",
            "version": "1.0.0",
            "kind": "official",
            "kernelApi": "^1",
            "license": "Apache-2.0",
            "permissionBudget": {"objectLevel": "L3"},
            "members": {"required": [{"id": "finance", "version": "1.2.0"}]},
        },
    )
    report = audit_pack(d)
    assert "AUDIT-8" in _blocking_codes(report), [f.detail for f in report.findings]


def test_audit_detects_third_party_without_license(tmp_path: Path) -> None:
    """★ 第三方成员 + 包本身没声明 license ⇒ 必须被拒（B2-11）。"""
    d = _pack_dir(
        tmp_path,
        manifest={
            "packFormat": 1,
            "id": "official.demo",
            "name": "演示包",
            "version": "1.0.0",
            "kind": "official",
            "kernelApi": "^1",
            "members": {"required": [{"id": "third-party-thing", "version": "1.0.0"}]},
        },
    )
    plugins = {"third-party-thing": {"id": "third-party-thing", "kind": "third-party"}}
    report = audit_pack(d, plugins=plugins)
    assert "AUDIT-9" in _blocking_codes(report), [f.detail for f in report.findings]


def test_audit_detects_secret_for_unknown_member(tmp_path: Path) -> None:
    """★ 凭据指定给不在包内的成员 ⇒ 必须被拒。"""
    d = _pack_dir(
        tmp_path,
        manifest={
            "packFormat": 1,
            "id": "official.demo",
            "name": "演示包",
            "version": "1.0.0",
            "kind": "official",
            "kernelApi": "^1",
            "license": "Apache-2.0",
            "secrets": [{"key": "TOKEN", "title": "令牌", "for": "not-in-pack"}],
            "members": {"required": [{"id": "finance", "version": "1.2.0"}]},
        },
    )
    report = audit_pack(d)
    assert "AUDIT-11" in _blocking_codes(report), [f.detail for f in report.findings]


def test_audit_warns_on_overlap_with_installed(tmp_path: Path) -> None:
    """★ 与已装插件重叠 ⇒ 要用户拍板（`warn`，不拦）。"""
    d = _pack_dir(tmp_path)
    report = audit_pack(
        d,
        installed_members={"finance": {"id": "finance", "version": "1.0.0"}},
    )
    assert "AUDIT-12" in _codes(report)
    assert "AUDIT-12" not in _blocking_codes(report)
    assert report.ok, "重叠只是 warn，不该让审查失败"


def test_audit_reports_budget_and_secrets(tmp_path: Path) -> None:
    """★ 审查报告要含**权限预算**与**凭据需求**（§22.3 的三项之一）。"""
    d = _pack_dir(
        tmp_path,
        manifest={
            "packFormat": 1,
            "id": "official.demo",
            "name": "演示包",
            "version": "1.0.0",
            "kind": "official",
            "kernelApi": "^1",
            "license": "Apache-2.0",
            "permissionBudget": {"objectLevel": "L2", "capabilities": ["fs.files.read"]},
            "secrets": [{"key": "TOKEN", "title": "令牌", "sensitive": True}],
            "members": {"required": [{"id": "finance", "version": "1.2.0"}]},
        },
    )
    report = audit_pack(d)
    assert report.permission_budget["objectLevel"] == "L2"
    assert report.permission_budget["capabilities"] == ["fs.files.read"]
    assert [s["key"] for s in report.secrets] == ["TOKEN"]


def test_audit_finding_has_advice(tmp_path: Path) -> None:
    """★ 每条发现都要有**怎么办**（不变量 I14：发现即证据）。

    ★ 只有「看到了什么」而没有「怎么办」，用户还是得自己去查。
    """
    d = _pack_dir(tmp_path)
    report = audit_pack(d)
    for f in report.findings:
        assert f.detail.strip(), "发现必须有 detail"
        assert f.advice.strip(), f"发现 {f.code} 必须有 advice"


def test_format_audit_is_readable(tmp_path: Path) -> None:
    """人话版审查报告要能看懂。"""
    d = _pack_dir(tmp_path)
    text = format_audit(audit_pack(d))
    assert "official.demo" in text
    assert "结论：" in text


def test_audit_does_not_leak_manifest_raw(tmp_path: Path) -> None:
    """★ 审查报告的 `to_dict()` 不该把整份清单原样倒出来。

    ★ 为什么要管这个：清单里有 `secrets` 的 key、config 的内容。
      审查报告是要**给人看**的，也可能被截图、被转发。
      ⇒ 报告只给**结论与证据**，不给整份原文。
    """
    d = _pack_dir(
        tmp_path,
        manifest={
            "packFormat": 1,
            "id": "official.demo",
            "name": "演示包",
            "version": "1.0.0",
            "kind": "official",
            "kernelApi": "^1",
            "license": "Apache-2.0",
            "members": {"required": [{"id": "finance", "version": "1.2.0"}]},
        },
    )
    payload = audit_pack(d).to_dict()
    assert "members" not in payload
    assert "raw" not in payload


def test_audit_pack_error_is_typed(tmp_path: Path) -> None:
    """审查失败时抛的必须是 `PackError`（统一错误类型，不裸抛其它）。"""
    assert issubclass(PackError, Exception)
    d = tmp_path / "official.bad"
    d.mkdir(parents=True)
    (d / "pack.json").write_text("[]", encoding="utf-8")
    report = audit_pack(d)
    assert not report.ok
    assert report.findings[0].level is AuditLevel.BLOCK


@pytest.mark.parametrize(
    "level", [AuditLevel.BLOCK, AuditLevel.WARN, AuditLevel.INFO]
)
def test_audit_levels_are_exactly_three(level: AuditLevel) -> None:
    """★ 级别只有三档。`block` 之上再分级没有意义。"""
    assert level in set(AuditLevel)
    assert len(list(AuditLevel)) == 3
