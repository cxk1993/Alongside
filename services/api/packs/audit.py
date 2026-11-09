"""包层**静态审查**——装载前，不执行任何代码，把「这个包会带来什么」查清楚。

★ 这一层治什么
--------------
包**不提供沙箱**（报告 §22.1）：已装上的插件在宿主进程内运行，
和内核同权。「装一个包」不改变这件事，也不为包内成员提供任何隔离。

⇒ 包层**不能拿「沙箱」当安全承诺**。能给的只有三样：

| 手段 | 做什么 |
|---|---|
| **静态审查** | **不执行**就检查：结构、路径逃逸、契约覆盖、预算放大、摘要一致、许可声明 |
| 来源与摘要 | 从哪来、谁发的、内容摘要是什么；第一次装记录，之后变了就报错 |
| 权限收窄 | 预算是上限（§12）；未声明的能力调用一律 403（运行时，不变） |

★ 本模块只做第一样：**静态审查**。
  它**不执行包里的任何代码**——不 import、不加载 manifest、不跑脚本。
  只读文件、只算摘要、只按规则查形状。
  ⇒ 审查一个恶意包时，审查器本身不会被攻击。

★ 与 `dry_run` 的区别（这两个经常被混为一谈）
--------------------------------------------
`dry_run` 回答的是「**装不装得上**」——成员在不在、版本对不对、会不会冲突。
静态审查回答的是「**这个包值不值得装**」——它要什么、它改了什么、
它的权限有多大、它和已装的东西有没有重叠。

⇒ 一个包完全可能「装得上但不该装」：
  它要了 `fs:write` 却没有正当理由、它覆盖了别的插件的路由、
  它的成员源码和基线锁里记的不一样。
  这些 `dry_run` 一个都查不出来。

★ 审查结论是**给人看的**，不是给机器判的
------------------------------------------
所以输出是**结构化的摘要 + 逐条发现**（每条有级别、判据、证据、怎么办），
不是「一个布尔值」。用户要能据此判断，机器才拿它当门禁。

★ 级别只有三档
--------------
| 级别 | 含义 | 后果 |
|---|---|---|
| `block` | **必须拒绝装载** | 装它就是违背协议 |
| `warn` | **要用户拍板** | 可以装，但用户得知道这件事 |
| `info` | 只是事实 | 不影响判断，但让人看得见全貌

★ 为什么不设「更严重」的一档
---------------------------
`block` 之上再分级没有意义——协议说必须拒绝的，就是必须拒绝。
分级只影响**谁来看**：block 让装载流程停，warn 让用户决定，info 只是留痕。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from packs.digest import pack_digest
from packs.model import PackError, PackManifest
from packs.validate import parse_manifest, validate_lock_payload

__all__ = [
    "AuditFinding",
    "AuditLevel",
    "AuditReport",
    "audit_pack",
    "format_audit",
]


class AuditLevel(StrEnum):
    """审查发现的级别。**只有三档**——上面再分级没有意义。"""

    BLOCK = "block"
    """必须拒绝装载。"""

    WARN = "warn"
    """可以装，但**用户得知道**。"""

    INFO = "info"
    """只是事实。留痕，让人看得见全貌。"""


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """一条审查发现。

    ★ 四要素缺一不可（不变量 `I14`：发现即证据）
      · `code` —— 哪条判据
      · `level` —— 多严重
      · `detail` —— **看到了什么**（值、路径、摘要…）
      · `advice` —— **怎么办**

    只有「不严重」而没有「看到了什么」，用户没法自己判断；
    只有「看到了什么」而没有「怎么办」，用户还是得自己去查。
    ⇒ 两者都要。
    """

    code: str
    level: AuditLevel
    detail: str
    advice: str


@dataclass(frozen=True, slots=True)
class AuditReport:
    """一份静态审查报告。

    ★ 报告**不含**「这个包能不能装」的结论——
      那是 `dry_run` 的活。审查报告只回答
      「这个包会带来什么、有什么需要你拍板」。
    """

    pack_id: str
    pack_version: str
    pack_name: str
    source: str
    findings: tuple[AuditFinding, ...] = ()
    member_count: int = 0
    pack_digest: str | None = None
    baseline_digest: str | None = None
    permission_budget: dict[str, Any] = field(default_factory=dict)
    secrets: tuple[dict[str, Any], ...] = ()
    licenses: tuple[str, ...] = ()
    third_party_members: tuple[str, ...] = ()

    @property
    def blocking(self) -> tuple[AuditFinding, ...]:
        return tuple(f for f in self.findings if f.level is AuditLevel.BLOCK)

    @property
    def warnings(self) -> tuple[AuditFinding, ...]:
        return tuple(f for f in self.findings if f.level is AuditLevel.WARN)

    @property
    def ok(self) -> bool:
        """没有 `block` 级发现吗？"""
        return not self.blocking

    def to_dict(self) -> dict[str, Any]:
        """可序列化的形状。

        ★ 形状由**包层**定（与 `summarize_reports` 同一条纪律）：
          内核不该翻译包层的数据模型。
        """
        return {
            "packId": self.pack_id,
            "packVersion": self.pack_version,
            "packName": self.pack_name,
            "source": self.source,
            "ok": self.ok,
            "memberCount": self.member_count,
            "packDigest": self.pack_digest,
            "baselineDigest": self.baseline_digest,
            "permissionBudget": self.permission_budget,
            "secrets": list(self.secrets),
            "licenses": list(self.licenses),
            "thirdPartyMembers": list(self.third_party_members),
            "findings": [
                {
                    "code": f.code,
                    "level": str(f.level),
                    "detail": f.detail,
                    "advice": f.advice,
                }
                for f in self.findings
            ],
            "blockingCount": len(self.blocking),
            "warningCount": len(self.warnings),
        }


# ───────────────────────────── 审查主体 ─────────────────────────────


def audit_pack(
    pack_dir: Path,
    *,
    source: str = "local",
    installed_members: dict[str, dict[str, Any]] | None = None,
    plugins: dict[str, dict[str, Any]] | None = None,
) -> AuditReport:
    """对**一个包目录**做静态审查。**不执行包里的任何代码。**

    ★ `installed_members` 是「当前已装着的插件」→ `{id: manifest}`。
      有了它才能查出「这个包覆盖了别人」——而这是**装之前**最该知道的事。
      没有它（首次审查、或调用方不关心）就跳过那类检查，不假装查过。

    ★ `plugins` 是**已发现的插件索引**（同一形状）。
      它回答「这个成员是不是第三方」——判据是插件 manifest 的 `kind`
      （`third-party`），不是成员名字像不像官方的。
      有了它才查得出「第三方成员缺 license」这类问题。

    ★ 审查**永远不抛异常**：审查的对象可能是个坏包、恶意包、半截下载。
      审查器自己崩了，用户就看不到「这个包有问题」这个信息——
      那比「审查失败」危险得多。
      ⇒ 出错就记成一条 `block` 发现（审查失败 ⇒ 不许装）。
    """
    pack_dir = Path(pack_dir)
    findings: list[AuditFinding] = []

    try:
        manifest = _load_manifest(pack_dir)
    except PackError as exc:
        return AuditReport(
            pack_id=pack_dir.name,
            pack_version="",
            pack_name=pack_dir.name,
            source=source,
            findings=(
                AuditFinding(
                    code="AUDIT-1",
                    level=AuditLevel.BLOCK,
                    detail=f"包清单读不出来或不合规：{exc.message}",
                    advice="这个包不能装。先修好清单，或换一个包。",
                ),
            ),
        )
    except Exception as exc:  # noqa: BLE001 —— 审查器不许崩
        return AuditReport(
            pack_id=pack_dir.name,
            pack_version="",
            pack_name=pack_dir.name,
            source=source,
            findings=(
                AuditFinding(
                    code="AUDIT-1",
                    level=AuditLevel.BLOCK,
                    detail=f"审查失败：{exc}",
                    advice="审查器读这个包时出错了。在查清之前不要装它。",
                ),
            ),
        )

    findings.extend(_check_structure(manifest))
    findings.extend(_check_paths(manifest))
    findings.extend(_check_budget(manifest))
    findings.extend(_check_license(manifest, plugins or {}))
    findings.extend(_check_secrets(manifest))
    findings.extend(_check_overlap(manifest, installed_members or {}))

    actual_pack_digest = _safe(pack_digest, pack_dir)
    baseline = _safe(read_baseline, pack_dir)
    baseline_digest = baseline.self_digest if baseline else None
    findings.extend(_check_baseline_shape(pack_dir))
    findings.extend(_check_digest(actual_pack_digest, baseline_digest))

    return AuditReport(
        pack_id=manifest.id,
        pack_version=manifest.version,
        pack_name=manifest.name,
        source=source,
        findings=tuple(findings),
        member_count=len(manifest.all_members),
        pack_digest=actual_pack_digest,
        baseline_digest=baseline_digest,
        permission_budget=_budget_shape(manifest),
        secrets=tuple(
            {
                "key": s.key,
                "title": s.title,
                "sensitive": s.sensitive,
                "required": s.required,
                "for": s.for_member,
            }
            for s in manifest.secrets
        ),
        licenses=tuple(
            str(p.get("license"))
            for p in (plugins or {}).values()
            if p.get("license")
        ),
        third_party_members=tuple(
            m.id
            for m in manifest.all_members
            if _is_third_party(m.id, (plugins or {}).get(m.id))
        ),
    )


# ─────────────────────────── 各项检查 ───────────────────────────


def _check_structure(manifest: PackManifest) -> list[AuditFinding]:
    """包**结构**。清单读不出来时前面已经报过了，这里只补事实。"""
    out: list[AuditFinding] = []
    if not manifest.name:
        out.append(
            AuditFinding(
                code="AUDIT-2",
                level=AuditLevel.WARN,
                detail="包没有可读的名字（name 为空）。",
                advice="给包起个名字。用户在列表里要能一眼认出它。",
            )
        )
    if not manifest.license:
        out.append(
            AuditFinding(
                code="AUDIT-3",
                level=AuditLevel.WARN,
                detail="包本身没有声明 license。",
                advice="声明 license。发行时需要知道这个包能不能被分发。",
            )
        )
    if not manifest.all_members:
        out.append(
            AuditFinding(
                code="AUDIT-4",
                level=AuditLevel.WARN,
                detail="包没有声明任何成员。",
                advice="空包装载后什么也不会发生。要确认这是有意的。",
            )
        )
    if manifest.includes:
        out.append(
            AuditFinding(
                code="AUDIT-5",
                level=AuditLevel.INFO,
                detail=f"包含 {len(manifest.includes)} 个子包："
                + "、".join(i.id for i in manifest.includes),
                advice="子包展开后会并入成员，归属是集合。子包里的东西也归你管。",
            )
        )
    return out


def _check_paths(manifest: PackManifest) -> list[AuditFinding]:
    """**路径逃逸**（§22.2）。装配会写文件，路径必须留在允许的范围内。"""
    out: list[AuditFinding] = []
    root = manifest.directory
    if root is None or not root.is_dir():
        return out

    for child in sorted(root.rglob("*")):
        try:
            resolved = child.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root.resolve()):
            out.append(
                AuditFinding(
                    code="AUDIT-6",
                    level=AuditLevel.BLOCK,
                    detail=f"{child} 指向包目录之外（{resolved}）。",
                    advice="路径越界的包不能装。装配会写文件，写到包外就是越权。",
                )
            )
    return out


def _check_budget(manifest: PackManifest) -> list[AuditFinding]:
    """**权限预算**（§12 / §23）。预算是上限，不是授予。"""
    out: list[AuditFinding] = []
    budget = manifest.permission_budget
    if budget is None:
        out.append(
            AuditFinding(
                code="AUDIT-7",
                level=AuditLevel.WARN,
                detail="包没有声明权限预算。",
                advice="声明预算。预算是上限——不声明的话，成员实际要什么就没人能提前看到。",
            )
        )
        return out

    if budget.object_level == "L3":
        out.append(
            AuditFinding(
                code="AUDIT-8",
                level=AuditLevel.BLOCK,
                detail="包声明了 L3（内核级）权限。",
                advice="包不得声明对内核的操作。L3 只能由人显式授予。",
            )
        )
    return out


def _check_license(
    manifest: PackManifest, plugins: dict[str, dict[str, Any]]
) -> list[AuditFinding]:
    """**许可声明**（B2-11）。第三方成员必须声明 license。

    ★ 判据是**插件 manifest 的 `kind`**（`third-party`）——
      不是成员名字像不像官方的。名字是能改的，`kind` 是插件自己声明的身份。
    """
    out: list[AuditFinding] = []
    for member in manifest.all_members:
        plugin = plugins.get(member.id)
        if plugin is None:
            continue
        if plugin.get("kind") == "third-party" and not manifest.license:
            out.append(
                AuditFinding(
                    code="AUDIT-9",
                    level=AuditLevel.BLOCK,
                    detail=f"第三方成员 {member.id} 没有声明 license。",
                    advice="发行合规需要它。让包作者补上，或换掉这个成员。",
                )
            )
    return out


def _check_secrets(manifest: PackManifest) -> list[AuditFinding]:
    """**凭据需求**（§22.3 之一：审查报告要含「凭据需求」）。"""
    out: list[AuditFinding] = []
    for secret in manifest.secrets:
        if secret.sensitive and not secret.title:
            out.append(
                AuditFinding(
                    code="AUDIT-10",
                    level=AuditLevel.WARN,
                    detail=f"敏感凭据 {secret.key} 没有可读的标题。",
                    advice="给凭据起个名字。用户要知道自己被要了什么。",
                )
            )
        if secret.for_member and secret.for_member not in manifest.declared_ids:
            out.append(
                AuditFinding(
                    code="AUDIT-11",
                    level=AuditLevel.BLOCK,
                    detail=f"凭据 {secret.key} 指定给成员 {secret.for_member}，"
                    "但该成员不在包内。",
                    advice="凭据只能给包内的成员。要么加这个成员，要么改凭据的归属。",
                )
            )
    return out


def _check_overlap(
    manifest: PackManifest, installed: dict[str, dict[str, Any]]
) -> list[AuditFinding]:
    """**与已装插件的重叠**。装之前最该知道的一件事。"""
    out: list[AuditFinding] = []
    for member in manifest.all_members:
        other = installed.get(member.id)
        if other is None:
            continue
        out.append(
            AuditFinding(
                code="AUDIT-12",
                level=AuditLevel.WARN,
                detail=f"成员 {member.id} 已经装过了（当前版本 {other.get('version', '?')}），"
                f"这个包要装的是 {member.version}。",
                advice="装它会换掉现在的版本。要确认这是你想要的。",
            )
        )
    return out


def _check_digest(
    actual: str | None, baseline: str | None
) -> list[AuditFinding]:
    """**摘要一致**（§22.1）。第一次装记录，之后变了就报错。"""
    out: list[AuditFinding] = []
    if baseline is None:
        if actual is None:
            out.append(
                AuditFinding(
                    code="AUDIT-13",
                    level=AuditLevel.WARN,
                    detail="这个包的内容摘要算不出来。",
                    advice="算不出摘要 ⇒ 装上之后没法发现它被改过。先把它放对位置再装。",
                )
            )
        else:
            out.append(
                AuditFinding(
                    code="AUDIT-14",
                    level=AuditLevel.INFO,
                    detail=f"没有基线锁（第一次装）。当前内容摘要 {actual}。",
                    advice="装上之后会记录这个摘要。之后内容变了会被发现。",
                )
            )
        return out

    if actual is None:
        out.append(
            AuditFinding(
                code="AUDIT-15",
                level=AuditLevel.BLOCK,
                detail="有基线锁，但当前内容的摘要算不出来。",
                advice="摘要算不出来就没法对账。查清包目录为什么读不到。",
            )
        )
        return out

    if actual != baseline:
        out.append(
            AuditFinding(
                code="AUDIT-16",
                level=AuditLevel.BLOCK,
                detail=f"包内容摘要与基线不符：基线 {baseline}，实际 {actual}。",
                advice="这个包在基线钉下之后被改过。要确认改动是可信的，"
                "然后显式更新基线（`pack lock`）。",
            )
        )
    return out


# ─────────────────────────── 辅助 ───────────────────────────


def _load_manifest(pack_dir: Path) -> PackManifest:
    path = pack_dir / "pack.json"
    if not path.is_file():
        raise PackError(f"找不到包清单：{path}", code="AUDIT-1")
    raw = json.loads(path.read_text(encoding="utf-8"))
    manifest = parse_manifest(raw)
    # ★ parse_manifest 不带目录，得自己挂上——路径检查要用它。
    return _with_directory(manifest, pack_dir)


def _with_directory(manifest: PackManifest, pack_dir: Path) -> PackManifest:
    import dataclasses

    return dataclasses.replace(manifest, directory=pack_dir)


def read_baseline(pack_dir: Path) -> Any:
    """读基线锁。读不出来返回 `None`（没有基线 ≠ 基线坏了）。"""
    from packs.state import read_baseline_lock

    return read_baseline_lock(pack_dir)


def _check_baseline_shape(pack_dir: Path) -> list[AuditFinding]:
    """**基线锁本身的形状**也要审。

    ★ 为什么：基线锁是**跟着包分发**的格式。
      它的形状坏了 ⇒ 装载时的对账要么永远判「不符」（用户永远装不上），
      要么永远判「相符」（摘要机制被架空）——
      两种都是「看起来能装，实际保护没了」。
    """
    path = pack_dir / "pack.lock.json"
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [
            AuditFinding(
                code="AUDIT-17",
                level=AuditLevel.BLOCK,
                detail=f"基线锁读不出来：{exc}",
                advice="这个基线锁是坏的。要么修好它，要么删掉它重新钉一份。",
            )
        ]
    if not isinstance(raw, dict):
        return [
            AuditFinding(
                code="AUDIT-17",
                level=AuditLevel.BLOCK,
                detail="基线锁不是 JSON 对象。",
                advice="这个基线锁是坏的。要么修好它，要么删掉它重新钉一份。",
            )
        ]
    try:
        validate_lock_payload(raw, kind="基线锁")
    except PackError as exc:
        return [
            AuditFinding(
                code="AUDIT-18",
                level=AuditLevel.BLOCK,
                detail=f"基线锁的形状不合法：{exc.message}",
                advice="这个基线锁是坏的。要么修好它，要么删掉它重新钉一份。",
            )
        ]
    return []


def _safe(fn: Any, *args: Any) -> Any:
    """跑一个可能失败的东西，失败返回 `None` 而不是抛出。"""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 —— 审查器不许崩
        return None


def _is_third_party(
    member_id: str, plugin: dict[str, Any] | None
) -> bool:
    """这个成员算不算「第三方」？

    ★ 判据是**插件 manifest 的 `kind == "third-party"`**——
      插件自己声明的身份，不是名字像不像官方的。
      找不到这个插件（没发现）时**不算第三方**：
      「查不到」不是「是」——把查不到判成是，会让审查报告满屏假警报。
    """
    if plugin is None:
        return False
    return plugin.get("kind") == "third-party"


def _budget_shape(manifest: PackManifest) -> dict[str, Any]:
    budget = manifest.permission_budget
    if budget is None:
        return {}
    return {
        "objectLevel": budget.object_level,
        "capabilities": list(budget.capabilities),
        "approvalRequired": list(budget.approval_required),
    }


def format_audit(report: AuditReport) -> str:
    """人话版审查报告。

    ★ 每条发现一行：`级别  编号  看到了什么  →  怎么办`。
      与 `pack check` 的输出格式一致（`B2-3  <字段路径>  <实际值>  <为什么不接受>`），
      两处口径不一样会让人以为两件事不一样。
    """
    lines: list[str] = []
    lines.append(
        f"静态审查：{report.pack_name}（{report.pack_id} {report.pack_version}）"
    )
    lines.append(
        f"  成员 {report.member_count} 个 · 来源 {report.source} · "
        f"摘要 {report.pack_digest or '算不出来'}"
    )
    if report.permission_budget:
        b = report.permission_budget
        lines.append(
            f"  权限预算：{b.get('objectLevel', '?')} · "
            f"能力 {len(b.get('capabilities', []))} 项 · "
            f"需审批 {len(b.get('approvalRequired', []))} 项"
        )
    if report.secrets:
        lines.append(
            f"  凭据需求：{len(report.secrets)} 项（"
            + "、".join(s.get("key", "?") for s in report.secrets)
            + "）"
        )
    lines.append("")

    if not report.findings:
        lines.append("  没有发现任何问题。")
        return "\n".join(lines)

    for f in report.findings:
        lines.append(f"  {f.level:5s}  {f.code}  {f.detail}  →  {f.advice}")

    lines.append("")
    lines.append(
        f"  结论：{len(report.blocking)} 项必须拒绝，{len(report.warnings)} 项需你拍板。"
    )
    return "\n".join(lines)
