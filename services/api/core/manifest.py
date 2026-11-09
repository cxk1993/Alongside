# ── 协议在 Python 侧的运行时落点 ──
"""模块 manifest 模型 + 自动发现。

★ 这是 T03 的"模块自动发现"职责（总纲 §1.3 / 验收 #2-#3）。
  校验失败必须**明确指出是哪个模块、哪个字段**（不许静默跳过）。
  正式的 plugin.schema.json 归 T14；这里是内核运行期的最小校验。
"""
from __future__ import annotations

import importlib
import logging
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.errors import ManifestError

log = logging.getLogger("kernel.manifest")

_KINDS = {"core", "builtin", "third-party", "container"}
_ID_RE = __import__("re").compile(r"^[a-z][a-z0-9-]*$")
# activates_on 条目只允许 startup:always 或 event:<topic>
#（topic 点分小写、至少两段，如 event:note.created）——与 contracts/plugin.schema.json
# 的 pattern 保持字面一致，两层校验同一语义。
_ACTIVATES_ON_RE = __import__("re").compile(
    r"^(startup:always|event:[a-z0-9-]+(\.[a-z0-9-]+)+)$"
)


class ManifestApi(BaseModel):
    base: str = Field(..., description="路由前缀，必须为 /api/v1/<id>")
    openapi: str | None = None
    health: str | None = None
    # ISSUE-008 方案 A（2026-09-20）：resource → 真实路由段 的显式映射。
    # 键 = provides 倒数第二段（resource），值 = api.base 下的路由段（如
    # "/entries"、"/health-of-system"）。声明则 MCP 转发用它，未声明的
    # resource 走机械「resource+s」推导——T18「加工具零额外声明」对
    # 常规 REST 资源依旧成立，显式声明只用于不规则/语义命名路由。
    tools: dict[str, str] = {}


class Manifest(BaseModel):
    id: str
    name: str
    version: str
    kind: str
    minKernel: str | None = None
    kernelApi: str | None = None
    icon: str | None = None
    description: str | None = None
    author: str | None = None
    # ★ 路径 a（2026-09-25）：容器型模块（kind="container"）允许**无 api**
    #   —— 系统窗等纯前端编排容器没有后端路由，声明式注册即"一切皆插件"的正形态。
    #   非 container 型仍强制声明 api（校验见 _validate）。向后兼容：存量模块零影响。
    api: ManifestApi | None = None
    # ★ 路径 a：容器型模块的页签声明——一次定稿，形状与 Zcode
    #   SystemApp（70c0702）对接面一致：{key, label, entry?}，entry=模块 id
    #（无 entry 页 = 「后端模块 · 仅服务」薄壳语义）。非 container 型可忽略。
    tabs: list[dict[str, Any]] = []
    # ★ 本容器吸收的模块 id 列表：这些模块**不再单独出现在底端栏**，入口收进本容器。
    #   典型场景：某后端模块被更完整的容器取代（无独立视图页），由本容器接管其入口。
    #   声明了 tabs[].entry 的模块**不必**在此重复列出 —— entry 已隐含吸收。
    absorbs: list[str] = []
    provides: list[str] = []
    requires: list[str] = []
    # ★（#009 软依赖声明的静态半边）：缺了**不阻塞启动**的依赖。
    #   语义：硬依赖（requires）缺 = 起不来（T14 fail-fast 不变）；
    #        软依赖（本字段）缺 = 坞位挂 degraded 角标，功能降级但不死。
    #   与 requires 必须互斥（同一条目不得两处都写 —— #033 闸门规则 ④）。
    optionalDependencies: list[str] = []
    slots: list[str] = []
    emits: list[str] = []
    consumes: list[str] = []
    # ★ 2026-09-25（修与补）：schema 的 permissions 有**两种格式** ——
    #   字符串数组（旧，细粒度）与对象（新，filesystem/network/subprocess 粗开关）。
    #   此前模型只声明 list[str]，**对象格式会被 pydantic 直接判非法** → 与 schema 口径打架。
    #   此处放宽为联合类型；**归一化在 core/plugins/permissions.normalize_permissions**
    #（唯一入口），本模型只负责"看得见、不报错"。
    permissions: list[str] | dict[str, bool] = Field(default_factory=lambda: [])
    activates_on: list[str] = []
    migrations: str | None = None
    settingsSchema: str | None = None
    # ★ D′-1（2026-09-23 · 唯一读法之内核侧）：这两个字段**原文通道一直在用、模型通道
    #   却从未声明**（pydantic 默认 extra="ignore" 把它们静默吞掉）——
    #   实测：所有内置 manifest 都写着 `entry` 与 `window`，而 `Manifest` 模型看不见它们，
    #   导致同一份 manifest 两套口径（模型通道 / 原文通道）。此处补齐，让模型也"看得见"。
    #   前端侧（PluginContext 改读模型）**刻意留后**：那是 E5 在制区，改动必撞车。
    entry: str | None = None
    window: dict[str, Any] | None = None
    lifecycle: dict[str, Any] = Field(default_factory=dict)

    # ══════════════════════════════════════════════════════════════
    # 协议新增字段的运行时落点（2026-10-26）
    # ══════════════════════════════════════════════════════════════
    # ★ 为什么补这一段：协议 Schema 已有 35 个字段，但这个模型只声明了 24 个。
    #   剩下 10 个（下面这组）此前**只能从 model_extra 里摸**——校验层认真对待它们，
    #   运行层却没有任何具名落点，调用方得写 m.model_extra.get("riskLevel")。
    #   结果是：插件作者声明了 riskLevel / concurrency / schedule，
    #   内核读得到值却从不消费——**声明了等于没声明**。
    #   这是比「字段被静默吞掉」更隐蔽的一类协议失效：值没丢，但没人用。
    #
    # ★ 补法：给每个协议字段一个具名落点 + 明确默认值。
    #   默认值取「内核推断的保守默认」——与协议兼容策略一致：
    #   字段缺失不拦，按保守默认走；只有显式声明错才失败。
    #   这样存量插件零改动通过，新插件又能真正被内核读懂。

    # 能力定义（三角色的 Service Definition 侧）：本插件「定义」了哪些能力。
    # ★ 一个能力最多一个 defines，可以有多个 provides（多 Provider 由配置选定）。
    #   兼容模式：若全系统没有任何插件写 defines，则 provides 隐含 defines。
    defines: list[str] = []
    # 依赖丧失时的降级策略：manual（人工介入）/ auto（自动转 PAUSED）。
    # ★ auto = requires 中任一能力被撤销时，内核**主动**把本插件转入 PAUSED。
    #   这不是插件自己写逻辑，而是内核在依赖变化时转移阶段——
    #   插件只声明希望被怎样对待。
    # ★ 默认必须是 manual：若默认 auto，那么内核升级后所有老插件
    #   都会在依赖丢失时被停掉，而它们甚至不知道自己有这个声明。
    deactivation: str = "manual"
    # 风险等级 L0~L3：决定审批与沙箱强度。third-party 不得声明 L0。
    riskLevel: str | None = None
    # 执行约束：沙箱等级 + 审批策略。
    execution: dict[str, Any] = Field(default_factory=dict)
    # 副作用声明：是否触碰外部系统、是否需要检查点。
    sideEffects: dict[str, Any] = Field(default_factory=dict)
    # 生命周期轨道：persistent（留数据）/ ephemeral（不留）。
    track: str = "persistent"
    # 来源与审计：谁装的、是否签名。
    origin: dict[str, Any] = Field(default_factory=dict)
    # 并发契约：乐观锁、独占、并行安全操作。
    concurrency: dict[str, Any] = Field(default_factory=dict)
    # 定时调度：六种选择器之一（at/after_seconds/every_seconds/daily/weekly/cron）。
    schedule: dict[str, Any] = Field(default_factory=dict)
    # 大输出策略：内联上限、溢出去处、失败兜底。
    outputPolicy: dict[str, Any] = Field(default_factory=dict)
    # 扩展位：协议向前兼容的容器。放行不报不拒——先让插件写上，
    # 等内核开始解释它再收紧校验。之所以要给具名落点（而不是留在 model_extra），
    # 是为了让「协议字段 ↔ 运行时落点」的对应关系可机器核对。
    extensions: dict[str, Any] = Field(default_factory=dict)

    # ── D′-2（2026-09-23）：未知字段必须**被报告**，不再静默吞 ──
    # 为什么是 class-level：pydantic v2 的 extra 策略在模型级生效，而我们要按 manifest 的
    # kind **分档**（内置严、第三方宽）→ 故模型级放 allow（不丢信息），分档判定放到 _validate。
    model_config = ConfigDict(extra="allow")

    @property
    def unknown_fields(self) -> dict[str, Any]:
        """本 manifest 里模型不认识的顶层字段（`extra="allow"` 会收在这里）。

        ★ 静默失败防御：旧行为下这些字段被**直接丢弃且不留痕**——
          `requires` 拼成 `requiers` 时，作者以为声明了硬依赖，实际 `requires` 仍是空表。
        """
        return dict(self.model_extra or {})

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


def _validate(module_id: str, raw: dict[str, Any], dir_name: str) -> Manifest:
    """校验单个 manifest，失败抛 ManifestError（带模块名+字段）。"""
    try:
        m = Manifest(**raw)
    except ValidationError as exc:
        locs = "; ".join(
            f"{'/'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"
            for e in exc.errors()
        )
        raise ManifestError(
            f"模块「{module_id}」manifest 校验失败：{locs}"
        ) from exc

    if m.id != dir_name:
        raise ManifestError(
            f"模块「{dir_name}」manifest.id={m.id!r} 必须等于目录名 {dir_name!r}"
        )
    if not _ID_RE.match(m.id):
        raise ManifestError(
            f"模块「{m.id}」id 必须是小写英文/数字/连字符，且以字母开头"
        )
    if m.kind not in _KINDS:
        raise ManifestError(
            f"模块「{m.id}」kind={m.kind!r} 必须是 {sorted(_KINDS)} 之一"
        )
    # ★ 路径 a：container 型可无 api；非 container 型必须声明 api
    if m.api is None:
        if m.kind != "container":
            raise ManifestError(
                f"模块「{m.id}」kind={m.kind!r} 必须声明 api（仅 kind=\"container\" 可省）"
            )
    else:
        expected_base = f"/api/v1/{m.id}"
        if m.api.base != expected_base:
            raise ManifestError(
                f"模块「{m.id}」api.base 必须是 {expected_base!r}，实际为 {m.api.base!r}"
            )
    # ★ 路径 a：tabs 形状校验（一次定稿）——每项必须含 key/label，entry 可选
    for idx, tab in enumerate(m.tabs):
        if not isinstance(tab, dict):
            raise ManifestError(
                f"模块「{m.id}」tabs[{idx}] 必须是对象 {{key,label,entry?}}"
            )
        missing = [k for k in ("key", "label") if not tab.get(k)]
        if missing:
            raise ManifestError(
                f"模块「{m.id}」tabs[{idx}] 缺必填字段 {missing}（形状 {{key,label,entry?}}）"
            )
        if "entry" in tab and tab["entry"] is not None and not isinstance(tab["entry"], str):
            raise ManifestError(
                f"模块「{m.id}」tabs[{idx}].entry 必须是字符串（模块 id）或省略"
            )
    # ★ absorbs 校验：被本容器吸收的模块 id 列表。
    #   ① 必须是字符串 id，不能是自己（自己吸收自己无意义）；
    #   ② 与 tabs[].entry 不得重复 —— entry 已隐含吸收，重复写两遍会让
    #      "吸收关系"有两个来源，埋下分叉。
    for aid in m.absorbs:
        if not isinstance(aid, str) or not aid:
            raise ManifestError(
                f"模块「{m.id}」absorbs 条目 {aid!r} 非法：必须是非空字符串（被吸收的模块 id）"
            )
        if aid == m.id:
            raise ManifestError(f"模块「{m.id}」不能吸收自己（absorbs 含自身 id）")
    entry_ids = {t.get("entry") for t in m.tabs if isinstance(t, dict)}
    dup = sorted(set(m.absorbs) & {e for e in entry_ids if isinstance(e, str)})
    if dup:
        raise ManifestError(
            f"模块「{m.id}」absorbs 与 tabs[].entry 重复声明 {dup} —— entry 已隐含吸收，不必重复写"
        )
    for act in m.activates_on:
        if not _ACTIVATES_ON_RE.match(act):
            raise ManifestError(
                f"模块「{m.id}」activates_on 条目 {act!r} 非法：只支持 "
                "startup:always 或 event:<topic>（点分小写、至少两段，如 event:note.created）"
            )

    # ★ D′-2（2026-09-23）：未知字段分档处理 —— 不再静默吞
    #   留扩展位：`x_` 前缀 与 `extensions` 对象一律放行（对齐 Agent Plugins 的 extensions
    #   命名空间思路：客户端特定数据有明确的落脚处，不必挤进顶层）。
    unknown = {
        k: v
        for k, v in m.unknown_fields.items()
        if not k.startswith("x_") and k != "extensions"
    }
    if unknown:
        names = sorted(unknown)
        if m.kind == "builtin":
            # 内置插件由我们自己写 → 拼错必须当场拒（这正是 D 提案的原始 bug：
            # `requires` 拼成 `requiers` 时，作者以为声明了硬依赖，实际是空表）
            raise ManifestError(
                f"模块「{m.id}」manifest 含内核不认识的字段 {names}；"
                "内置插件的字段名必须字面正确（拼错会静默失效）。"
                "确需携带客户端私有数据请用 `extensions` 对象或 `x_` 前缀。"
            )
        # 第三方插件：报告但继续（对齐 Agent Plugins Spec「未知字段 MUST report」）
        log.warning(
            "模块 %s（kind=%s）manifest 含未知字段 %s —— 已忽略（第三方允许扩展）",
            m.id,
            m.kind,
            names,
        )

    return m


# ── · 依赖声明闸门（#033 四条静态规则）─────────────────────────
# 依据：Home Assistant hassfest「Reject manifest dependencies on core integrations」
#（PR #169425）+ 我方显式声明线（#019→#021→#020）。**依赖声明错误必须在闸门层
# 拦截，不能留给运行时** —— 等加载期撞墙时模块已经挂了一半，现场很难定位。
#
# ★ 语义锚点（2026-09-23 实装时被真实目录打回后校准）：`requires` /
#   `optionalDependencies` 里写的是**能力名**（如 `web.entry.read`），**不是模块 id** ——
#   闸门判据是「该能力必须被某个模块 provides」。第一版按"模块 id 必须实存"判，
#   上线即被 catalog（它依赖的是 web 的能力，不是 web 这个模块）拦下 ——
#   这次校准本身就是闸门价值的证明。
#
# 四条规则：① 依赖的能力必须被某模块 provides ② 禁止反向依赖内核内部能力
#           ③ 循环依赖检测（能力→提供者模块，只沿硬依赖）④ 软依赖不得同时是硬依赖
KERNEL_RESERVED_CAPABILITY_PREFIXES: tuple[str, ...] = ("core.", "kernel.", "_kernel.")


def _scan_third_party_provides() -> dict[str, str]:
    """★ 2026-09-25：把 `plugins/` 里第三方插件的 provides 也纳入
    能力映射，否则内置插件**无法声明对第三方能力的依赖**（闸门会误判"没人 provides"）。

    ★ 边界说明：第三方插件是**可卸载**的，所以内置插件对它的依赖**只应写软依赖**
      （optionalDependencies）—— 见下方软依赖放宽处理。此函数只负责"看得见"。
    """
    provider: dict[str, str] = {}
    try:
        import json as _json

        plug_dir = Path(__file__).resolve().parents[2] / "plugins"
        if not plug_dir.is_dir():
            return provider
        for mf in sorted(plug_dir.glob("*/manifest.json")):
            try:
                raw = _json.loads(mf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for cap in raw.get("provides") or []:
                provider.setdefault(str(cap), str(raw.get("id") or mf.parent.name))
    except Exception:  # noqa: BLE001 —— 尽力而为，绝不让它拖垮启动
        pass
    return provider


def _gate_dependency_declarations(found: dict[str, tuple[Manifest, Path]]) -> None:
    """依赖声明闸门：全部模块发现后统一校验，失败抛 ManifestError（启动即失败）。"""
    # 能力名 → 提供者模块 id
    provider: dict[str, str] = {}
    # 能力名 → 全部提供者（发现第二个就记下来，不静默忽略）
    multi_providers: dict[str, list[str]] = {}

    def _record(cap: str, mid: str) -> None:
        if cap in provider:
            multi_providers.setdefault(cap, [provider[cap]]).append(mid)
        else:
            provider[cap] = mid

    for mid, (m, _p) in found.items():
        for cap in m.provides:
            _record(cap, mid)
    # ★ 第三方插件的 provides 也纳入（否则内置无法依赖第三方能力）
    for cap, mid in _scan_third_party_provides().items():
        _record(cap, mid)

    # ★ 发现第二个提供者 ⇒ **记录并告警**，不许静默忽略。
    #   旧实现是 `provider.setdefault(cap, mid)`：先到先得，
    #   后提供者被**静默忽略**——这正是跨插件校验规则 13 自己的注释
    #   批评的行为（「违反『不许静默失败』铁律」）。
    #   新口径（2026-10-28）：多 Provider **不阻断启动**，
    #   但**必须留痕**——它是「待裁决冲突」，
    #   由包层用 `providerChoice` 裁决；没包裁决则该能力标 unavailable。
    #   ⇒ 这里既不抛错（抛错等于让内核替包层做业务裁决），也不静默。
    for cap, mids in sorted(multi_providers.items()):
        log.warning(
            "能力 %s 有多个提供者（%s）：已选 %s，其余待裁决。"
            "如需固定选谁，请在包的 pack.json 的 providerChoice 中声明。",
            cap,
            ", ".join(sorted(set(mids))),
            provider[cap],
        )

    # 模块 → 其硬依赖所指向的模块（经「能力 → 提供者」映射；软依赖不入图）
    hard_edges: dict[str, set[str]] = {mid: set() for mid in found}

    for mid, (m, _p) in found.items():
        hard, soft = list(m.requires), list(m.optionalDependencies)

        # ④ 软硬互斥（同一能力不得两处都写）
        both = sorted(set(hard) & set(soft))
        if both:
            raise ManifestError(
                f"模块「{mid}」的能力 {both} 同时出现在 requires 与 optionalDependencies"
                "（软硬必须互斥：要么缺了会死，要么缺了降级，不能两头都占）"
            )

        for cap in hard + soft:
            field = "requires" if cap in hard else "optionalDependencies"
            # ② 不得反向依赖内核内部能力
            if cap.startswith(KERNEL_RESERVED_CAPABILITY_PREFIXES):
                raise ManifestError(
                    f"模块「{mid}」{field} 依赖内核内部能力 {cap!r}"
                    "（内核是宿主、不对外 provides —— 反向依赖禁止）"
                )
            # ① 依赖的能力必须真的有人 provides
            #   ★ 2026-09-25：**软依赖放宽** ——
            #     optionalDependencies 的语义本就是「缺了降级不死」（见 Manifest 注释），
            #     故找不到提供者时**只告警**；硬依赖仍 fail-fast。
            if cap not in provider:
                if field == "optionalDependencies":
                    log.warning(
                        "模块 %s 的软依赖 %r 当前没有提供者（允许：缺了降级不死）", mid, cap
                    )
                    continue
                raise ManifestError(
                    f"模块「{mid}」{field} 声明的能力 {cap!r} 没有任何模块 provides"
                )
            if field == "requires":
                owner = provider[cap]
                if owner != mid:  # 依赖自己提供的能力不构成环
                    hard_edges[mid].add(owner)

    # ③ 循环依赖：沿「能力 → 提供者模块」的硬边遍历（软依赖缺了不死，不参与）
    white, gray, black = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(found, white)

    def visit(node: str, path: list[str]) -> None:
        color[node] = gray
        for nxt in sorted(hard_edges.get(node, ())):
            if nxt not in found:  # 规则①已拦，此处防御性跳过
                continue
            if color[nxt] == gray:
                raise ManifestError("检测到循环硬依赖：" + " → ".join([*path, node, nxt]))
            if color[nxt] == white:
                visit(nxt, [*path, node])
        color[node] = black

    for mid in sorted(found):
        if color[mid] == white:
            visit(mid, [])


# ── · 软依赖缺失的运行时判定（#009 的 degraded 半边）────────────
# 硬依赖缺失 = 启动期 fail-fast（T14 语义），**运行时不会出现** —— 所以运行期只判软依赖。
# 产出 degraded + reason_code，供 O1 四态面板消费（#009：「增加 reason_code 字段即可
# 对齐，不动 schema 主结构」）。
#
# health / O1 侧接入示例（无需改本模块即可用）：
#     avail = {cap for m, _ in discovered for cap in m.provides}
#     st = dependency_status(manifest, avail)
#     # st.state ∈ {"ok", "degraded"}
#     # st.reason_code 形如 "optional_dependency_missing:finance.beeccount.read"
@dataclass(frozen=True)
class DependencyStatus:
    """模块依赖态判定结果（运行期；只覆盖软依赖半边）。"""

    state: str  # "ok" | "degraded"
    reason_code: str | None
    missing: tuple[str, ...]


def dependency_status(m: Any, available: Collection[str]) -> DependencyStatus:
    """按软依赖声明 + 当前可用能力集合，判定模块依赖态。

    ★ 只判软依赖（`optionalDependencies`）：硬依赖缺失已由启动期 fail-fast 拦下，
      运行期不会出现 —— 此处不重复判定，避免两处语义打架。
    """
    missing = tuple(sorted(c for c in m.optionalDependencies if c not in available))
    if not missing:
        return DependencyStatus(state="ok", reason_code=None, missing=())
    return DependencyStatus(
        state="degraded",
        reason_code="optional_dependency_missing:" + ",".join(missing),
        missing=missing,
    )


def discover_modules(modules_dir: str | Path) -> list[tuple[Manifest, Path]]:
    """扫描 modules/*/manifest.json，返回 (manifest, 目录) 列表。

    同 id 重复 -> 报错。无 router.py -> 报错。
    """
    root = Path(modules_dir)
    found: dict[str, tuple[Manifest, Path]] = {}
    results: list[tuple[Manifest, Path]] = []

    if not root.is_dir():
        return results

    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        manifest_path = sub / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            raw = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
        except __import__("json").JSONDecodeError as exc:
            raise ManifestError(
                f"模块「{sub.name}」manifest.json 不是合法 JSON：{exc}"
            ) from exc

        m = _validate(sub.name, raw, sub.name)
        if m.id in found:
            raise ManifestError(f"模块 id 重复：{m.id!r}（{sub.name} 与已发现模块冲突）")
        found[m.id] = (m, sub)
        results.append((m, sub))

    # ★ 全部发现后统一过「依赖声明闸门」（①实存 ②禁内核 ③环 ④软硬互斥）
    _gate_dependency_declarations(found)

    return results


def load_router(module_id: str) -> Any:
    """动态导入 modules.<id>.router 并取 router 对象。"""
    try:
        mod = importlib.import_module(f"modules.{module_id}.router")
    except ModuleNotFoundError as exc:
        raise ManifestError(
            f"模块「{module_id}」缺少 router.py（无法 import modules.{module_id}.router）：{exc}"
        ) from exc
    router = getattr(mod, "router", None)
    if router is None:
        raise ManifestError(f"模块「{module_id}」router.py 未定义 router = APIRouter()")
    return router
