"""包层装配器（Assembler）。

★ 包层不实现「怎么装」，只决定「装什么、按什么顺序、失败怎么办」——
  enable / disable 全部委托内核（`core.plugins.manager.PluginManager`）。

★ 装载是一次**批量状态转移 + 一个提交点**（报告 §19.2）：
   0  取得独占
   1  读 pack.json → B1
   2  读发现的插件 → B2
   3  展开 includes → 归属集合 + B2-7/8/9
   4  对账 pack.lock.json
   5  读用户调整（只读！）并合并成员集
   6  展示预算与凭据需求（第一期只汇总，不交互）
   7  ── 提交点：此前全部只读 ──
   8  按序 enable
   9  任一失败 → 逆序 disable 已成功的（包级补偿）
   10 全部成功 → 写锁 + 存档原件
   11 释放独占，产出报告
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packs.config import (
    ACTION_SKIP,
    ACTION_UPDATE,
    ACTION_WRITE,
    ENABLED_KEY,
    ConfigDecision,
    ConfigPlan,
    applied_after,
    plan_config,
)
from packs.digest import content_digest, pack_digest
from packs.model import (
    AdjustmentAction,
    AssemblySnapshot,
    LoadReport,
    LoadStatus,
    LockedMember,
    MemberOutcome,
    PackError,
    PackManifest,
)
from packs.routing import route_capabilities
from packs.state import PackStore, read_baseline_lock
from packs.validate import (
    expand_includes,
    parse_manifest,
    validate_consistency,
    validate_structure,
)

log = logging.getLogger(__name__)

# 正在装载的目标：(pack_id, 数据目录)。键是**资源**，不是名字。
_ACTIVE: set[tuple[str, str]] = set()


class PackBusyError(PackError):
    code = "PACK_BUSY"


@dataclass
class PackAssembler:
    """装配器。所有内核能力都从外部注入，包层自己不认识「插件管理器」。"""

    discover: Callable[[], Any]
    """返回带 `.plugins` 的发现结果（PluginInfo 列表）。"""

    enable: Callable[[str, Any], Mapping[str, Any]]
    disable: Callable[[str, Any], Mapping[str, Any]]
    registry: Any
    """路由注册表，原样传给内核的 enable / disable。"""

    data_dir: Path
    packs_root: Path | None = None
    """**额外**的包查找根。默认 `None`（不额外找）。

    ★ 语义：它是「除了数据目录与发行版包根之外，**再**去哪找」，
      排在最前（显式 > 默认）。
      早先它的语义是「包目录的根」，装配器自己算 `packs_root / <id>`——
      那让「包在哪」有多个答案（路由、装配器、boot 各算各的）。
      ⇒ 现在解析统一交给 `packs.roots.resolve_pack_dir()`，
        本字段只是「额外的根」这一条信息。
    """

    discover_modules: Callable[[str], PackManifest | None] | None = None
    """`includes` 的子包加载器。默认交给 `packs.roots.resolve_pack_dir()` 解析。

    ★ 只有在调用方**自己有**一套子包发现逻辑时才覆盖它
      （如「子包不在磁盘上，在数据库里」这种部署）。
      覆盖了就完全接管这条解析——`packs.roots` 不再参与。
    """

    read_settings: Callable[[str], Mapping[str, Any]] | None = None
    """读某个成员**当前生效**的设置 → `{key: value}`。

    ★ 为什么是注入的：包层**不碰数据库**。
      `plugin_setting` 是内核的表，而「包怎么写设置」是包层的事——
      内核只提供「读设置」「写设置」两个窄动作（与 `enable` / `disable` 同一纪律）。
      ⇒ 不给就退化成「不落地任何配置」，而不是「自己去查库」。

    ★ 不给时会怎样：包清单里的 `config` **完全不生效**。
      这不是「静默跳过」——装载报告里会记一条「配置未落地（未接入设置读写）」。
    """

    write_settings: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None
    """写某个成员的设置（整批 upsert）。**不给就不写任何配置。**"""

    now: Callable[[], str] = field(
        default=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z")
    )

    failure_mode: str = "atomic"
    """成员启用失败时的姿态。

    - `atomic`（默认）：**整包回滚**——逆序补偿已启用的成员，包恢复到「没装」的状态。
    - `partial`：**保留已成功的成员**，失败的那个记为失败，��继续后面的成员。

    ★ 为什么默认 atomic：「装到一半的包」比「没装的包」更难解释——
      用户看到一个半残的包，不知道该重装还是修单个成员。
      整包回滚的语义干净：要么全好，要么全没动。

    ★ 但 `partial` 在一种情况下体验明显更好：**包里有个可选成员装不上**
      （比如那个插件在当前平台不可用）。这时把整包拆掉，
      用户什么也拿不到——而他本来能拿到大部分。
      所以留成可切，而不是写死。

    ★ 无论哪种姿态，**失败都会逐成员留痕**（见 LoadReport.members）——
      这是「可追问、可审计」的地基：不论最终状态如何，
      用户都能问出「每个成员到底怎么了」。
    """

    # ─────────────────────────── 公开入口 ───────────────────────────

    def load(
        self,
        pack_dir: Path | str,
        *,
        ask_optional: Callable[[str], bool] | None = None,
        dry_run: bool = False,
    ) -> LoadReport:
        """装载一个包。

        `ask_optional`：可选成员**逐个**询问；不传则所有可选成员一律不装
        （保守默认 —— 不替用户决定「反正不占地方，装上吧」）。
        """
        pack_dir = Path(pack_dir)
        manifest = self._read_manifest(pack_dir)
        key = (manifest.id, str(Path(self.data_dir).resolve()))

        if key in _ACTIVE:
            raise PackBusyError(
                f"该包已有装载在进行中：{manifest.id}。同一目标的装载必须串行——"
                "并发会写出互相覆盖的锁，并把成员推进谁都没预期的组合。"
            )

        _ACTIVE.add(key)
        try:
            return self._load_locked(pack_dir, manifest, ask_optional=ask_optional, dry_run=dry_run)
        finally:
            _ACTIVE.discard(key)

    # ─────────────────────────── 内部流程 ───────────────────────────

    def _read_manifest(self, pack_dir: Path) -> PackManifest:
        path = pack_dir / "pack.json"
        if not path.exists():
            raise PackError(f"找不到 {path}。", code="B1-0")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PackError(f"pack.json 不是合法 JSON：{exc}", code="B1-3") from exc
        return parse_manifest(raw, directory=pack_dir)

    def _plugin_index(self) -> dict[str, dict[str, Any]]:
        """插件 id → 插件信息（manifest + 目录）。

        ★ 为什么在 manifest 里塞一个 `_directory` 键：
          算内容摘要必须知道**插件目录在哪**——manifest 本身不含路径。
          而 `validate_consistency` / `_degraded_reason` 都在读这张表，
          改成嵌套结构会让它们全要改。
          ⇒ 保留「扁平 dict」的形状，只多带一个下划线开头的内部键。
          下划线前缀 = 内部用，不是协议字段。
        """
        found = self.discover()
        index: dict[str, dict[str, Any]] = {}
        for info in getattr(found, "plugins", []):
            manifest = getattr(info, "manifest", None)
            if isinstance(manifest, Mapping):
                entry = dict(manifest)
                directory = getattr(info, "directory", None)
                if directory is not None:
                    entry["_directory"] = str(directory)
                index[str(getattr(info, "id", ""))] = entry
        return index

    def _load_child(self, pack_id: str) -> PackManifest | None:
        """找 `includes` 里的子包。**解析只有一处**（`packs.roots`）。

        ★ 早先这里自己算 `packs_root / pack_id`，
          而路由算 `data_dir / "packs" / pack_id`、`boot` 传 `data_dir / "packs"`
          ——三处各算各的 ⇒ 迟早分叉，
          表现是「界面能看见这个包、点装配却找不到」。

        ★ `packs_root` 现在的语义是「**额外**的查找根」：
          它排在数据目录**之前**（显式 > 默认）。
          不传时仍然能工作——`packs.roots` 会依次找
          数据目录 → 发行版包根。
        """
        if self.discover_modules is not None:
            return self.discover_modules(pack_id)
        from packs.roots import resolve_pack_dir

        extra = (self.packs_root,) if self.packs_root is not None else ()
        child_dir = resolve_pack_dir(pack_id, Path(self.data_dir), extra_roots=extra)
        if child_dir is None:
            return None
        try:
            return self._read_manifest(child_dir)
        except PackError:
            return None

    def _load_locked(
        self,
        pack_dir: Path,
        manifest: PackManifest,
        *,
        ask_optional: Callable[[str], bool] | None,
        dry_run: bool,
    ) -> LoadReport:
        store = PackStore(self.data_dir, manifest.id)

        # ── 1~2. 结构 + 一致性（只读） ──
        validate_structure(manifest, dirname=pack_dir.name)
        plugins = self._plugin_index()
        validate_consistency(manifest, plugins)

        # ── 3. 展开 includes（只读） ──
        chain, origin = expand_includes(manifest, self._load_child)

        # ── 3a. 能力路由裁决（只读，纯计算）────────────────────────
        #   ★ 位置在提交点之前：它只读清单与插件 manifest，不产生任何副作用。
        #   ★ 为什么在**这里**（展开之后、成员判定之前）：
        #     路由要回答「这个包里，同一个能力有谁提供、选了谁」，
        #     所以必须等 includes 展完、成员集齐了才算得出来。
        #   ★ 为什么在这里算而不是 enable 之后：
        #     路由结果是**装配期视图**（不激活谁），
        #     不是运行期状态。它不改变任何插件的启用状态。
        selected_providers, provider_conflicts = route_capabilities(chain, plugins)
        if provider_conflicts:
            # ★ 不阻断启动：这是「待裁决」，不是失败。
            #   系统照常起来，只是这些能力不可用（unavailable）。
            #   阻断会让内核在包层说话之前就把系统判死 ⇒ 包层永远没机会裁决。
            for item in provider_conflicts:
                log.warning(
                    "能力 %s 有多个提供者（%s）且本包未显式选定 ⇒ 该能力装配后不可用。"
                    "请在 pack.json 的 providerChoice 中选定其一。",
                    item["capability"],
                    ", ".join(item["candidates"]),
                )

        # 展开后的成员（按依赖在前、自身在后）都并进来
        ordered_members: list[tuple[str, str, str | None]] = []
        # (member_id, version, 清单里钉的摘要)
        required_ids: set[str] = set()
        optional_ids: set[str] = set()
        seen: set[str] = set()
        pinned: dict[str, tuple[str, str]] = {}  # member_id → (digest, 来源包)
        for pack in chain:
            for member in pack.required:
                required_ids.add(member.id)
                if member.id not in seen:
                    seen.add(member.id)
                    ordered_members.append((member.id, member.version, member.digest))
                self._note_pinned(pinned, member.id, member.digest, pack.id)
            for member in pack.optional:
                optional_ids.add(member.id)
        # 顶层包的可选成员排在其必需成员之后
        for member in manifest.optional:
            if member.id not in seen:
                seen.add(member.id)
                ordered_members.append((member.id, member.version, member.digest))
            self._note_pinned(pinned, member.id, member.digest, manifest.id)

        # ── 4. 对账（只读） ──
        #   ★ 两个文件，两个角色（详见 packs/state.py 的模块头）：
        #     · 基线锁（包目录，pack.lock.json）=「我装的是什么」→ **对账基准**
        #     · 装配快照（数据目录）=「上次装成了什么」→ **差量装配的依据**
        #   装载**只读**基线锁（§15.2 规则 2）；快照在成功后重写。
        baseline = read_baseline_lock(pack_dir)
        baseline_members = baseline.by_id() if baseline else {}
        snapshot = store.load_snapshot()
        snapshot_members = snapshot.by_id() if snapshot else {}
        new_members: list[str] = []
        summary_not_verified: list[str] = []

        # ── 4a. 包级摘要对账（§15.3 第 2 行，先于成员对账）──
        #   ★ 为什么先对包、再对成员：
        #     「包本身被改过」与「某个成员被改过」是**两件不同的事**，
        #     后者只是包的一个组成部分被动了。
        #     先说包，才能在报错时说清「是这个包被改了」，
        #     而不是让用户去逐个成员里猜是哪个。
        #   ★ 包级摘要只覆盖**包自己的内容**（清单原文、内嵌插件、README），
        #     成员插件是引用关系，它们的摘要由成员级对账负责。
        actual_pack_digest = self._pack_digest(pack_dir)
        expected_pack_digest = baseline.self_digest if baseline else None
        if (
            expected_pack_digest is not None
            and actual_pack_digest is not None
            and expected_pack_digest != actual_pack_digest
        ):
            return LoadReport(
                pack_id=manifest.id,
                status="failed",
                members=(),
                error=(
                    f"包 {manifest.id} 的内容摘要与基线不符："
                    f"期望 {expected_pack_digest}，实际 {actual_pack_digest}。"
                    "包本身（清单、内嵌插件、说明文件）被改过了。"
                    "要接受新内容，请显式更新基线（`alongside pack lock`）；"
                    "要保持原内容，请还原该包目录。"
                ),
                dry_run=dry_run,
            )

        # ── 5. 读用户调整（只读！）并合并成员集 ──
        state = store.load_state()
        available = set(plugins)
        outcomes: list[MemberOutcome] = []
        to_enable: list[tuple[str, str]] = []

        # ── 5b. 包声明的「停用成员」（只算，不写）─────────────────
        #   ★ 为什么在这里算（提交点之前）：这个判断只依赖清单，
        #     是纯计算。提交点之前只读，是 §19.2 的硬要求。
        #   ★ 为什么不用 `plan_config`：那个函数要读「当前生效值」才能判归属，
        #     而这里只需要「包里说了谁该关」——用清单就够了。
        #     （真正落地配置时会在提交点之后再算一次完整计划。）
        config_disabled: set[str] = set()
        for member_id, values in manifest.config.items():
            if values.get(ENABLED_KEY) is False:
                config_disabled.add(member_id)

        for member_id, version, declared_digest in ordered_members:
            is_optional = member_id in optional_ids and member_id not in required_ids
            decision = state.decision(member_id)

            if decision is AdjustmentAction.REMOVE:
                outcomes.append(self._out(member_id, LoadStatus.SKIPPED, version, origin, "用户已移除此成员"))
                continue

            if member_id not in available:
                if is_optional:
                    outcomes.append(self._out(member_id, LoadStatus.SKIPPED, version, origin, "可选成员未安装"))
                else:
                    # 必需成员缺失 → 整包失败（可选缺失才降级）
                    return LoadReport(
                        pack_id=manifest.id,
                        status="failed",
                        members=tuple(outcomes),
                        error=(
                            f"必需成员 {member_id} 不在已发现的插件里（期望版本 {version}）。"
                            "必需成员缺失时这个包不成立。"
                        ),
                        dry_run=dry_run,
                    )

            # 可选成员：用户没表态过 → 问
            if is_optional and decision is None:
                wanted = bool(ask_optional(member_id)) if ask_optional else False
                if not wanted:
                    outcomes.append(self._out(member_id, LoadStatus.SKIPPED, version, origin, "未选中（可选成员）"))
                    continue

            # 用户明确停用 → 不启用（I13：装载不覆盖用户调整）
            if decision is AdjustmentAction.DISABLE:
                outcomes.append(self._out(member_id, LoadStatus.SKIPPED, version, origin, "用户已停用"))
                continue

            # ── 4a. 摘要对账（§15.3）────────────────────────────
            #   ★ 这是本轮修的**真缺陷**：原实现只比版本号，
            #     注释却写「摘要一致」——比的是版本，说的却是摘要。
            #     而 §15.1 说得很死：同一版本下代码被改过是常态，
            #     只比版本 ⇒ 「变了能发现」这个锁的全部价值落空。
            actual_digest = self._digest_of(member_id, plugins)
            expected_digest = self._expected_digest(
                member_id, declared_digest, pinned, baseline_members
            )

            if expected_digest is not None and actual_digest is not None:
                if expected_digest != actual_digest:
                    # ★ 摘要不符 ⇒ **硬拒绝**（§15.3 第 2 行）。
                    #   「不自动更新、不静默重解」——自动更新等于把锁变成日志。
                    return LoadReport(
                        pack_id=manifest.id,
                        status="failed",
                        members=tuple(outcomes),
                        error=(
                            f"成员 {member_id} 的内容摘要与基线不符："
                            f"期望 {expected_digest}，实际 {actual_digest}。"
                            "内容在装上之后被改过（或基线记错了）。"
                            "要接受新内容，请显式更新基线（`alongside pack lock`）；"
                            "要保持原内容，请还原该成员的文件。"
                        ),
                        dry_run=dry_run,
                    )
            else:
                # ★ 摘要**没校验**：算不出实际摘要，或没有可比基线。
                #   这不是「摘要一致」，是「没能力发现变化」——必须留痕。
                summary_not_verified.append(member_id)

            # ── 4b. 差量：装配快照里有、且摘要一致 → 复用 ──────
            #   ★ 复用判据是**快照**（上次装成了什么），不是基线锁
            #     （那是「我装的是什么」——两者不是一回事，见 packs/state.py）。
            prior = snapshot_members.get(member_id)
            if (
                prior is not None
                and prior.version == version
                and prior.digest is not None
                and actual_digest is not None
                and prior.digest == actual_digest
            ):
                outcomes.append(
                    self._out(member_id, LoadStatus.REUSED, version, origin, "已在位，摘要一致")
                )
                continue

            # ── 3b. 包声明的启用状态（协议 §11 的 `config.<id>.enabled`）──
            #   ★ 为什么要放在**这里**（成员判定阶段，而不是 enable 之前）：
            #     这个键表达的是「这个包希望这个成员是关的」，
            #     它决定的是「这个成员算不算被选中」——
            #     而「选中」这件事在 §19.2 步骤 5 就定了。
            #     放到 enable 之前 ⇒ 成员的 outcome 已经先记成「已安装」，
            #     后面再跳过，报告里就会自相矛盾。
            #   ★ 用户层在上（§21.4 规则一）：用户明确启用过的成员，
            #     包说「关」也**不关**——用户的调整优先。
            if member_id in config_disabled and state.decision(member_id) is not AdjustmentAction.ENABLE:
                outcomes.append(
                    self._out(
                        member_id,
                        LoadStatus.SKIPPED,
                        version,
                        origin,
                        "包声明该成员在本包中停用",
                    )
                )
                continue

            if prior is None:
                new_members.append(member_id)

            degraded = self._degraded_reason(member_id, plugins, manifest)
            if degraded is not None:
                outcomes.append(self._out(member_id, LoadStatus.DEGRADED, version, origin, degraded))
                continue

            to_enable.append((member_id, version))
            outcomes.append(self._out(member_id, LoadStatus.LOADED, version, origin, None))

        # ── 6. 预算与凭据汇总（第一期只汇总，不交互） ──
        budget = manifest.permission_budget
        secrets = [s for s in manifest.secrets if s.required]

        if dry_run:
            return LoadReport(
                pack_id=manifest.id,
                status="loaded",
                members=tuple(outcomes),
                user_adjusted=state.touched_ids(),
                new_members=tuple(new_members),
                summary_not_verified=tuple(summary_not_verified),
                selected_providers=tuple(selected_providers),
                provider_conflicts=tuple(provider_conflicts),
                dry_run=True,
            )

        # ── 7~9. 提交点 → 逐个 enable，失败逆序补偿 ──
        applied: list[str] = []
        failed_member: str | None = None
        failed_error: str | None = None
        for member_id, _version in to_enable:
            try:
                self.enable(member_id, self.registry)
                applied.append(member_id)
            except Exception as exc:  # noqa: BLE001 —— 内核异常一律按失败处理
                failed_member = member_id
                failed_error = f"{type(exc).__name__}: {exc}"
                break

        if failed_member is not None:
            if self.failure_mode == "partial":
                # ★ partial：保留已成功的成员，失败的那个留痕，继续后面的。
                #   语义：**部分成功是一个合法状态**，不是「装到一半的残骸」——
                #   因为报告里逐成员写清了每个成员的状态，
                #   用户能问出「哪个没装上、为什么」，也能单独再装它。
                outcomes.append(
                    self._out(
                        failed_member,
                        LoadStatus.FAILED,
                        self._version_of(ordered_members, failed_member),
                        origin,
                        failed_error or "启用失败",
                    )
                )
                for member_id, _version in to_enable:
                    if member_id == failed_member or member_id in applied:
                        continue
                    try:
                        self.enable(member_id, self.registry)
                        applied.append(member_id)
                        outcomes.append(
                            self._out(
                                member_id,
                                LoadStatus.LOADED,
                                _version,
                                origin,
                                "已安装",
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        outcomes.append(
                            self._out(
                                member_id,
                                LoadStatus.FAILED,
                                _version,
                                origin,
                                f"{type(exc).__name__}: {exc}",
                            )
                        )
                return LoadReport(
                    pack_id=manifest.id,
                    status="loaded",
                    members=tuple(outcomes),
                    user_adjusted=state.touched_ids(),
                    new_members=tuple(new_members),
                    error=(
                        f"部分成功：{len(applied)} 个成员已启用，"
                        f"但 {failed_member} 启用失败（{failed_error}）。"
                        "已保留成功部分，未回滚。"
                    ),
                    selected_providers=tuple(selected_providers),
                    provider_conflicts=tuple(provider_conflicts),
                    dry_run=False,
                )

            # ── atomic（默认）：逆序补偿，回到「没装」的状态 ──
            for done in reversed(applied):
                # 补偿本身失败：记下来，但不掩盖原始错误
                with contextlib.suppress(Exception):
                    self.disable(done, self.registry)
            outcomes.append(
                self._out(
                    failed_member,
                    LoadStatus.FAILED,
                    self._version_of(ordered_members, failed_member),
                    origin,
                    failed_error or "启用失败",
                )
            )
            return LoadReport(
                pack_id=manifest.id,
                status="failed",
                members=tuple(outcomes),
                user_adjusted=state.touched_ids(),
                new_members=tuple(new_members),
                error=(
                    f"成员 {failed_member} 启用失败：{failed_error}。"
                    f"已回放 {len(applied)} 个成员的逆操作。"
                ),
                selected_providers=tuple(selected_providers),
                provider_conflicts=tuple(provider_conflicts),
                dry_run=False,
            )

        # ── 9b. 落地包提供的配置值（协议 §11 / §16.1）──
        #   ★ 为什么放在**这里**（全部成员启用成功之后）：
        #     · 协议 §19.2 第 7 步是**提交点**——此前全部只读。
        #       写配置放在提交点之前 ⇒ 预检失败会留下写过的设置。
        #     · 写配置放在 enable 途中 ⇒ enable 失败要回滚设置，
        #       而「删掉某项设置」在插件层没有对应能力，回滚不干净。
        #     ⇒ 放在「全成功」之后：写了就不需要回滚。
        #   ★ 为什么单项写不进去不让整包失败：
        #     插件的 `settingsSchema` 常常是 `additionalProperties: false` 的空壳，
        #     此时包里给的任何设置值都会被拒。
        #     「配置不合意」不该让整个场景不可用——
        #     与 §12 第 4 条「成员降级，不是整包失败」同一条纪律。
        config_outcome = self._apply_config(manifest, store)

        # ── 10. 存档原件 + 写**装配快照**（不是基线锁）──
        #   ★ 关键区分（§15.2 规则 2 vs §19.2 步骤 10 的矛盾在这里消解）：
        #     · 基线锁（pack.lock.json，**包目录**）—— 装载**只读**，
        #       只有显式命令 `alongside pack lock` 才写。
        #     · 装配快照（pack.assembly.json，**数据目录**）—— 装载**重写**，
        #       记「这台机器这次装成了什么」。
        #     若装载重写基线锁，§15.3 的「摘要不符 ⇒ 硬拒绝」就永远不会触发。
        store.archive_original(manifest.raw)
        store.save_snapshot(
            AssemblySnapshot(
                pack_id=manifest.id,
                pack_version=manifest.version,
                generated_at=self.now(),
                self_digest=self._pack_digest(pack_dir),
                kernel_api=manifest.kernel_api,
                members=tuple(
                    LockedMember(
                        id=mid,
                        version=ver,
                        digest=self._digest_of(mid, plugins),
                        from_packs=tuple(origin.get(mid, ())),
                    )
                    for mid, ver, _declared in ordered_members
                    if any(o.id == mid and o.status is not LoadStatus.SKIPPED for o in outcomes)
                ),
            )
        )

        # 预算 / 凭据只进报告，不在这里做交互（授权永远逐插件发生）
        _ = (budget, secrets)

        return LoadReport(
            pack_id=manifest.id,
            status="loaded",
            members=tuple(outcomes),
            user_adjusted=state.touched_ids(),
            new_members=tuple(new_members),
            summary_not_verified=tuple(summary_not_verified),
            overridden=tuple(config_outcome.overridden),
            selected_providers=tuple(selected_providers),
            provider_conflicts=tuple(provider_conflicts),
            dry_run=False,
        )

    # ─────────────────────────── 配置落地 ───────────────────────────

    def _apply_config(self, manifest: PackManifest, store: PackStore) -> ConfigPlan:
        """落地包提供的配置值。**逐项**处理，**单项失败不影响整包**。

        ★ 层序（协议 §16.1）：基包 → 派生包 overrides → 用户覆盖层。
          本层实现的是「**包层**」这一段：
            · 没人写过的项 ⇒ 包补上默认值；
            · 是**包自己**写的项 ⇒ 包可以更新它；
            · 是**用户/别的来源**写的项 ⇒ 包**不许动**，只留痕。

        ★ 为什么需要「包自己写过什么」这份记录：
          `plugin_setting` 表**不记来源**，包写的值与用户写的值混成一份。
          没有那份记录就答不出「这一项现在归谁」——
          而 §16.1 第 3 条要求「每次覆盖都要留痕」，
          归因不了就等于没留痕。
        """
        if not manifest.config:
            return ConfigPlan()

        if self.read_settings is None or self.write_settings is None:
            # ★ 没接入设置读写 ⇒ 如实说「没落地」，不假装成功。
            plan = ConfigPlan()
            plan.decisions.append(
                ConfigDecision(
                    member_id="*",
                    key="*",
                    action="skip",
                    reason="未接入设置读写（read_settings / write_settings 未提供）",
                )
            )
            return plan

        # 读当前生效值（每个成员一次）。
        current: dict[str, dict[str, Any]] = {}
        for member_id in manifest.config:
            try:
                current[member_id] = dict(self.read_settings(member_id) or {})
            except Exception:  # noqa: BLE001 —— 读不到就当没值，不让整包失败
                current[member_id] = {}

        plan = plan_config(
            manifest,
            current_settings=current,
            applied=store.load_config_applied(),
        )

        # ── 写入：按**判定结果**逐键写 ──
        #   ★ 为什么**逐键**而不按成员整批：
        #     设置接口 `write_settings` 是「整批 upsert」——
        #     传进去的每个键都会被写上。
        #     若按成员整批传（把该成员的全部 config 键都传进去），
        #     那些被判为「保留用户值」的键（ACTION_KEEP）
        #     也会被**用包的值覆盖掉**——
        #     用户的修改当场被抹掉，而报告里还写着「保留了用户的值」。
        #     ⇒ 只能按判定结果传：这次该写哪个键，就只传哪个键。
        #   ★ 写失败的项要**改判**（write → skip），而 `ConfigDecision` 是
        #     frozen 的（不可变）——所以用 `dataclasses.replace` 换掉那一条，
        #     而不是就地改属性（就地改会绕过 frozen 的保护，且 mypy 会报）。
        written: set[tuple[str, str]] = set()
        for index, decision in enumerate(plan.decisions):
            if decision.action not in (ACTION_WRITE, ACTION_UPDATE):
                continue
            member_id = decision.member_id
            try:
                # ★ 一次只写一个键：接口是整批 upsert，
                #   传一个键就只动一个键。
                self.write_settings(member_id, {decision.key: decision.value})
            except Exception as exc:  # noqa: BLE001 —— 单项写不进去不该毁掉整包
                plan.decisions[index] = dataclasses.replace(
                    decision,
                    action=ACTION_SKIP,
                    reason=f"写入失败：{type(exc).__name__}: {exc}",
                )
                continue
            written.add((member_id, decision.key))

        # 记「我写过什么」——只记真写进去的。
        try:
            store.save_config_applied(
                applied_after(plan, store.load_config_applied(), only=written)
            )
        except OSError as exc:
            log.warning("包 %s 的配置来源记录写不下去：%s", manifest.id, exc)

        return plan

    # ─────────────────────────── 小工具 ───────────────────────────

    @staticmethod
    def _version_of(
        ordered_members: Sequence[tuple[str, str, str | None]], member_id: str
    ) -> str:
        """从成员列表里取某个成员的期望版本。

        ★ 为什么要单独一个函数：成员元组现在带三个字段（id/version/摘要），
          `dict(ordered_members)` 这种写法会直接炸——
          字典的键必须是**可哈希**的，元组是可哈希的，但值会变成整个元组，
          取出来就不是版本号了。
          ⇒ 显式取，别让「顺手」写出来的 dict 构造掩盖语义。
        """
        for mid, version, _digest in ordered_members:
            if mid == member_id:
                return version
        return ""

    @staticmethod
    def _out(
        member_id: str,
        status: LoadStatus,
        version: str | None,
        origin: Mapping[str, Sequence[str]],
        reason: str | None,
    ) -> MemberOutcome:
        return MemberOutcome(
            id=member_id,
            status=status,
            version=version,
            reason=reason,
            from_packs=tuple(origin.get(member_id, ())),
        )

    @staticmethod
    def _degraded_reason(
        member_id: str,
        plugins: Mapping[str, Mapping[str, Any]],
        manifest: PackManifest,
    ) -> str | None:
        """成员的 `requires` 有没有「谁都提供不了」的能力。

        ★ 判据必须是**可判定**的：包层没有求解器，也不猜。
          只在这一种情形降级——所需能力在全量插件里都找不到提供者。
          找得到提供者、但不在本包预算内，不降级（预算管的是包内授予范围，
          不是全局可用性）。
        """
        plugin = plugins.get(member_id) or {}
        needed = {str(c) for c in plugin.get("requires", []) or []}
        if not needed:
            return None
        provided: set[str] = set()
        for other in plugins.values():
            provided.update(str(c) for c in other.get("provides", []) or [])
        missing = sorted(needed - provided)
        if missing:
            return f"所需能力 {missing[0]} 没有任何插件提供——该成员降级，不整包失败。"
        return None

    @staticmethod
    def _note_pinned(
        pinned: dict[str, tuple[str, str]],
        member_id: str,
        digest: str | None,
        pack_id: str,
    ) -> None:
        """记下「这个成员的摘要是哪个包钉的」。

        ★ 为什么要记来源：
          同一个成员可能被多个包引入（§10.2 第 4 条：归属是集合）。
          若 A 包钉了摘要、B 包没钉，不该被当成「A 钉的」——
          否则 B 包升级时会莫名被 A 包的摘要卡住。
          ⇒ 记下钉它的那个包，报错时能说清「谁钉的」。

        ★ 冲突怎么办：**先钉的赢，但不留冲突**——
          真正的版本冲突由 B2-9 在展开期就报掉了（同一成员两个版本必报错）。
          这里只可能是「同版本、一个钉一个没钉」，不是冲突。
        """
        if not digest:
            return
        if member_id in pinned:
            return
        pinned[member_id] = (digest, pack_id)

    @staticmethod
    def _expected_digest(
        member_id: str,
        declared_digest: str | None,
        pinned: Mapping[str, tuple[str, str]],
        baseline_members: Mapping[str, Any],
    ) -> str | None:
        """这个成员的**期望摘要**是什么。

        ★ 优先级（从硬到软）：
          ① 清单里显式钉的（`members[].digest`）——**人写的，最权威**
          ② 基线锁里记的（`pack.lock.json`）——**工具生成的基线**
          ③ 都没有 ⇒ `None`（没有可比基线 ⇒ 本次不校验摘要）

        ★ 为什么清单优先于基线锁：
          清单是**作者声明的期望**（跟着包走，能分发给别人）；
          基线锁是**工具生成的记录**（本地生成，可能过期）。
          当两者不一致时，**以作者声明为准**——
          否则「作者改了清单但还没跑 lock 命令」就会被基线锁挡住，
          而那正是作者的正常开发流程。

        ★ 为什么「没有基线」不是错：
          第一次装一个包时本来就没有基线。§15.4 说这种情况要**留痕**
          （`summaryNotVerified`），不是拒绝。
        """
        if declared_digest:
            return declared_digest
        pinned_entry = pinned.get(member_id)
        if pinned_entry is not None:
            return pinned_entry[0]
        prior = baseline_members.get(member_id)
        digest = getattr(prior, "digest", None)
        if isinstance(digest, str) and digest:
            return digest
        return None

    @staticmethod
    def _pack_digest(pack_dir: Path) -> str | None:
        """算**包目录**的内容摘要（包级摘要）。算不出来返回 `None`。

        ★ 为什么快照也要记包级摘要：
          快照回答的是「这台机器上次装成了什么」——
          不只是「装了哪些成员」，还有「装的是**哪个包**」。
          没有包级摘要，就无法区分
          「同一个成员的集合，来自同一个包」与「来自两个不同的包」。

        ★ 算的时候排除 `pack.lock.json`（见 `packs/digest.py` 的 `pack_digest`）：
          基线锁住在包目录里，把它算进去 ⇒ 每写一次锁摘要就变一次
          ⇒ 摘要恒等于现状 ⇒ 这个字段什么也不证明。
        """
        try:
            return pack_digest(Path(pack_dir))
        except (OSError, FileNotFoundError):
            return None

    @staticmethod
    def _digest_of(member_id: str, plugins: Mapping[str, Mapping[str, Any]]) -> str | None:
        """算一个成员的**实际内容摘要**。

        返回 `sha256:…`，或 `None`（**算不出来**）。

        ★ 为什么不再返回全零占位：
          协议 §15.1 说得很死——锁的全部价值在于「变了能发现」，
          而只记版本号的锁会在「同一版本下代码被改过」时给出「没变」的错误结论。
          一个全零摘要看着像「算过了」，其实什么都没说：
          它会让「摘要一致」这个判断**恒真**，等于把整个机制架空。

        ★ 算不出来时返回 `None` 而不是编一个值：
          「没有目录」与「摘要是全零」是两件完全不同的事。
          前者要人处理（插件目录不在？路径错了？），后者会静默骗过人。

        ★ 为什么优先用插件 manifest 自带的 `digest`：
          那是**作者声明**的摘要（人钉死的期望值），比本地实算更权威。
          但插件 schema 目前没有这个字段 ⇒ 现实路径是本地实算。
        """
        plugin = plugins.get(member_id) or {}
        declared = plugin.get("digest")
        if isinstance(declared, str) and declared.startswith("sha256:"):
            return declared

        directory = plugin.get("_directory")
        if not directory:
            return None
        try:
            return content_digest(Path(str(directory)))
        except (OSError, FileNotFoundError):
            return None
