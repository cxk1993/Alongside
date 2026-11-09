"""插件管理器（总纲 §1.3 / 验收 B）：install / enable / disable / uninstall / 设置。

★ 注册表(plugin_state)与运行时对象(挂载的 router)分离 —— 这是热插拔的前提，
  也是"一切皆插件"不被腐蚀的结构保障。

职责边界（不抢别人的活）：
  - 发现/校验/版本/权限/迁移/设置/生命周期 分别由同包其它模块负责，这里只编排。
  - 路由挂载/摘掉 交给 core.registry.ModuleRegistry（T03 的 RouterHost）。
  - 数据库表/会话 交给 db（T04）。

★ 可逆性纪律（ADR-0005，2026-10-03）：
  每个 effect 必须携带逆；失败时按「应用的逆序」回放逆（论文 Theorem 16）。
  改动前请自问：这个 effect 的逆是什么？在哪个状态上生效？
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
from typing import Any

from sqlmodel import select

from core.deps import kernel_session
from core.errors import ConflictError, ForbiddenError, NotFoundError
from core.kernel import EffectKind, get_kernel
from core.plugins import discover as discover_mod
from core.plugins.discover import DiscoveryResult, PluginInfo, mount_plugin
from core.plugins.lifecycle import run_lifecycle_hook
from core.plugins.migrations import (
    recorded_versions,
    rollback_migrations,
    rollback_versions,
    run_migrations,
)
from core.plugins.permissions import is_valid_permission, normalize_permissions
from core.plugins.phase import Phase, PhaseReason
from core.plugins.settings import read_settings, write_settings
from db.models.system import PluginSetting, PluginState

log = logging.getLogger("kernel.plugins")

# ★ ADR-0005（2026-10-03）· 启动恢复的退避上限。
#   严守「卡档缓行纪律」：达上限后**不自动禁用、不改 enabled、不摘路由** ——
#   只退避（本轮跳过重试）+ 报警，状态原样保留等人来看。计数是**进程内**的，
#   不落库 —— 否则「跳过」会被固化成事实，插件再也等不到自愈。
_RECOVERY_MAX_CONSECUTIVE = 3

# 启动恢复的进程内追踪（不落库 —— 见上方说明）
_recovery_failures: dict[str, int] = {}
_recovery_last_error: dict[str, str] = {}
_recovery_lock = threading.Lock()


def reset_recovery_tracker() -> None:
    """清空启动恢复追踪（测试与「修复后手动重试成功」两处会调）。"""
    with _recovery_lock:
        _recovery_failures.clear()
        _recovery_last_error.clear()


def _register_dependencies(info: PluginInfo) -> None:
    """把 manifest 的依赖声明登记进依赖表。

    ★ `provides` 走 effect（`table.provide`）——所以它**自动带逆**：
      插件卸载时内核回放 effect，能力名会从依赖表里撤掉。
      不走 effect 的话，插件卸载后依赖表里还留着它的能力名，
      而这种残留会让别的插件误判「这个能力还能用」——最难查的一类。

    ★ `requires` **不在这里登记**——它有独立的撤销路径
      （`table.unregister_requirements`），由调用方作为 effect 登记。
      混在一起会让 provides 的逆被登记两次，撤一次不够 ⇒ 依赖表残留。
    """
    from core.plugins.dependency import get_dependency_table

    table = get_dependency_table()
    provides = list(info.manifest.get("provides") or [])

    for cap in provides:
        try:
            table.provide(cap, info.id)
        except Exception as exc:  # noqa: BLE001 —— 登记失败不该阻断装载
            log.warning(
                "能力提供登记失败（已记，不阻断装载）",
                extra={"module": info.id, "capability": cap, "error": str(exc)},
            )


def _register_requires_only(info: PluginInfo) -> None:
    """只登记 `requires` 声明（不含 provides）。

    ★ 拆出来是因为二者的**撤销路径不同**：
      - provides 走 `table.provide()`，自带逆（effect 内部）；
      - requires 只是一条「声明」，没有 do/undo 对，
        撤销要走 `table.unregister_requirements()`。
    ⇒ 混在一个函数里时，提供方的逆会被「登记」两次（一次在 effect 内、
      一次在外部），撤一次不够——依赖表会残留。
    """
    from core.plugins.dependency import get_dependency_table

    table = get_dependency_table()
    manifest = info.manifest
    table.register_requirements(
        info.id,
        requires=list(manifest.get("requires") or []),
        optional=list(manifest.get("optionalDependencies") or []),
        deactivation=str(manifest.get("deactivation") or "manual"),
    )


def _unregister_requirements(plugin_id: str) -> None:
    """撤掉某插件的 `requires` 声明（它不在了，需求也不该留着）。"""
    from core.plugins.dependency import get_dependency_table

    get_dependency_table().unregister_requirements(plugin_id)


def _withdraw_provides(plugin_id: str) -> None:
    """撤掉某插件提供的全部能力（disable / uninstall 时用）。

    ★ 与 effect 路径并存不冲突：effect 的逆是幂等的，撤两次也只撤一次。
    """
    from core.plugins.dependency import get_dependency_table

    table = get_dependency_table()
    for cap in list(table.providers_of(plugin_id)):
        try:
            table.withdraw(cap, plugin_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "能力提供撤除失败（已记，不阻断）",
                extra={"module": plugin_id, "capability": cap, "error": str(exc)},
            )


def _notify_dependencies(registry: Any) -> dict[str, list[str]]:
    """依赖表变化后重算，并把结果落到状态机（第五章 · Reactive Coeffect）。

    ★ 这一步是「依赖满足驱动」的落地点：
      能力被撤了，依赖它的插件**不会自己知道**——
      它会继续挂在路由上，继续宣称自己可用，
      直到某天有人真的去调它，才撞上一个莫名其妙的 403。

    ★ 为什么由内核发起而不是插件自己发起：
      插件不知道自己的路由挂没挂、effect 撤了没有，
      让它自己转阶段就会出现「插件说 PAUSED 但路由还挂着」这类不一致。
      ⇒ 阶段转移只能由内核统一做。
    """
    from core.plugins.coeffect import apply_notify_outcome
    from core.plugins.dependency import get_dependency_table

    try:
        outcome = get_dependency_table().notify()
    except Exception as exc:  # noqa: BLE001 —— 通知失败不该阻断主流程
        log.error(
            "依赖表重算失败（已记，不阻断）",
            extra={"error": str(exc)},
        )
        return {"deactivated": [], "reactivated": [], "neutral": []}
    try:
        return apply_notify_outcome(outcome, registry)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "依赖变化落状态机失败（已记，不阻断）",
            extra={"error": str(exc)},
        )
        return {"deactivated": [], "reactivated": [], "neutral": []}


def _transition(plugin_id: str, to: Phase, reason: PhaseReason) -> None:
    """做一次阶段转移（状态机 + 同事务审计）。

    ★ 为什么转移失败**不抛**
    ----------------------
    阶段转移失败意味着「状态机与实际行为不一致」（比如当前阶段是 ACTIVE，
    却要转 LOADING）——这是个真问题，但**抛出它会打断主流程**：
    插件明明已经装载成功，却因为「状态没记对」而向调用方报 500。

    所以：**记 error 级日志（会被 readyz 与日志采集看到），但不抛**。
    这条取舍的前提是「状态不一致**不改变实际行为**」——
    而它确实不改变：路由挂没挂、数据在不在，由 effect 与迁移决定，不由 phase 决定。
    phase 的作用是「让系统知道自己在哪一步」，它错了会误导诊断，
    但不会造成破坏。

    ★ 这也是为什么 `apply_transition` 内部要求「状态行必须存在」——
      行不存在时它直接跳过并告警，而不是凭空建行
      （凭空建行会让「无状态行 ⇒ 按来源取默认」的口径漂移）。
    """
    from core.plugins.phase import IllegalTransition
    from core.plugins.state_store import apply_transition

    try:
        apply_transition(plugin_id, to, reason=reason)
    except IllegalTransition as exc:
        log.error(
            "阶段转移被拒：状态机与实际行为不一致（已记，不打断主流程）",
            extra={
                "plugin_id": plugin_id,
                "from": exc.frm,
                "to": exc.to,
                "reason": reason.value,
            },
        )
    except Exception as exc:  # noqa: BLE001 —— 记录不改变实际行为
        log.error(
            "阶段转移失败（已记，不打断主流程）",
            extra={
                "plugin_id": plugin_id,
                "to": to.value,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )


def note_recovery_attempt(plugin_id: str) -> tuple[bool, int]:
    """报一次启动恢复尝试 → (是否应退避跳过, 已累计连续失败次数)。

    ⚠️ 退避分支里调用方**不得** enable、不得改 enabled、不得摘路由 ——
    卡档缓行纪律：只跳过这一轮，状态原样留着等人。
    """
    with _recovery_lock:
        n = _recovery_failures.get(plugin_id, 0)
        return n >= _RECOVERY_MAX_CONSECUTIVE, n


def note_recovery_result(plugin_id: str, error: str | None) -> None:
    """记一次启动恢复结果：成功清零，失败累加。"""
    with _recovery_lock:
        if error is None:
            _recovery_failures.pop(plugin_id, None)
            _recovery_last_error.pop(plugin_id, None)
        else:
            _recovery_failures[plugin_id] = _recovery_failures.get(plugin_id, 0) + 1
            _recovery_last_error[plugin_id] = error


def recovery_backoff_snapshot() -> dict[str, tuple[int, str]]:
    """退避状态只读快照（供 readyz / 测试查看）。"""
    with _recovery_lock:
        return {
            k: (v, _recovery_last_error.get(k, ""))
            for k, v in _recovery_failures.items()
        }

_BUILTIN_INCLUSION_NOTE = "内置插件随系统发布，不需要 install；可直接 enable/disable。"


class PluginManager:
    """单例式编排器。每调用即时重新发现（发现很便宜，且保证看到磁盘最新状态）。"""

    # ───────────────────────── 发现 ─────────────────────────
    def discover(self) -> DiscoveryResult:
        return discover_mod.discover_plugins()

    def _require(self, plugin_id: str) -> PluginInfo:
        for p in self.discover().plugins:
            if p.id == plugin_id:
                return p
        raise NotFoundError(f"插件不存在或校验未通过：{plugin_id!r}")

    def list_plugins(self) -> list[dict[str, Any]]:
        result = self.discover()
        with kernel_session() as db:
            states = {
                s.id: s
                for s in db.exec(select(PluginState)).all()
            }
        out: list[dict[str, Any]] = []
        for p in result.plugins:
            st = states.get(p.id)
            # 口径统一（2026-09-27 · 主人件①）：无状态行的默认启用态按来源判——
            # builtin/core 随系统发布默认在用；third-party 从未动过 = 未启用
            #（ADR-0002「可禁用」语义的权威态，与 core/app.py 启动恢复同规则）。
            # 旧值恒 True 会造成「MCP 工具已暴露 / 路由未挂载」的幽灵工具
            #（countdown 404 根因之一）。
            enabled = st.enabled if st is not None else p.source != "third-party"
            granted = (
                json.loads(st.granted_permissions)
                if st and st.granted_permissions
                else list(p.manifest.get("permissions", []))
            )
            out.append(
                {
                    "id": p.id,
                    "name": p.manifest.get("name"),
                    "version": p.manifest.get("version"),
                    "kind": p.kind,
                    "source": p.source,
                    "enabled": enabled,
                    "permissions": granted,
                    "slots": p.manifest.get("slots", []),
                    "provides": p.manifest.get("provides", []),
                    "requires": p.manifest.get("requires", []),
                    "last_error": st.last_error if st else None,
                    # ★ 阶段是对外可见的真相（技术报告 §3.9.1）。
                    #   不暴露的话，外部只能看到 enabled 的布尔值，
                    #   分不清「正在加载」「加载失败」「被依赖拖停」——
                    #   而这三种都需要人来处理。
                    "phase": (st.phase if st else None) or Phase.PENDING.value,
                    "phase_changed_at": (
                        st.phase_changed_at.isoformat()
                        if st and st.phase_changed_at
                        else None
                    ),
                    "valid": True,
                    # ★ ISSUE-012（2026-09-23 hermes）：清单必须携带完整 manifest，
                    #   否则前端 `p.manifest.entry/window/slots` 必崩，插件扩展点贡献
                    #   结构化无法挂载（E5 sidecar / D1 dashboard.card / DEG-01 软依赖角标）。
                    "manifest": p.manifest,
                }
            )
        # 也列出校验失败但目录存在的插件（让用户知道为什么没起来）
        for err in result.errors:
            out.append(
                {
                    "directory": str(err.directory),
                    "source": err.source,
                    "valid": False,
                    "error": err.message,
                }
            )
        return out

    def get_plugin(self, plugin_id: str) -> dict[str, Any]:
        for item in self.list_plugins():
            if item.get("id") == plugin_id:
                return item
        raise NotFoundError(f"插件不存在：{plugin_id!r}")

    def slots(self, slot_name: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in self.discover().plugins:
            if slot_name in p.manifest.get("slots", []):
                out.append(
                    {
                        "plugin_id": p.id,
                        "name": p.manifest.get("name"),
                        "enabled": self._enabled(p.id, default_enabled=p.source != "third-party"),
                    }
                )
        return out

    def _enabled(self, plugin_id: str, *, default_enabled: bool = True) -> bool:
        with kernel_session() as db:
            st = db.get(PluginState, plugin_id)
        return st.enabled if st else default_enabled

    # ───────────────────────── 状态落库 ─────────────────────────
    def _upsert_state(
        self,
        info: PluginInfo,
        *,
        enabled: bool,
        last_error: str | None = None,
        phase: str | None = None,
    ) -> None:
        """写插件状态行。

        ★ `phase` 是**状态的唯一真相**（技术报告第三章）；`enabled` 降级为
          派生读出口（`enabled == phase ∈ {ACTIVE, PAUSED}`）。
          两者一起写，是为了**存量读法继续可用**——删列是破坏性且不可逆的，
          而前端 / MCP 工具面 / 测试都在读它。

        ★ 不给 `phase` 时**不动它**：有些调用点只改 `last_error`
          （如激活器的落库通道），不该顺手把阶段改掉。
        """
        manifest = info.manifest
        granted = normalize_permissions(manifest.get("permissions"))
        with kernel_session() as db:
            st = db.get(PluginState, info.id)
            if st is None:
                st = PluginState(id=info.id)
                db.add(st)
            st.version = manifest.get("version", "0.0.0")
            st.kind = info.kind
            st.enabled = enabled
            st.last_error = last_error
            st.granted_permissions = json.dumps(granted, ensure_ascii=False)
            # ★ `phase` **不在这里改**。
            #   阶段转移一律走 `core.plugins.state_store.apply_transition`
            #   （它保证「改状态 + 写审计」**同事务**）。
            #   两处都能改 phase 就是「同一事实两处定义」——迟早分叉，
            #   而分叉了没人会发现。
            #   （`phase` 参数保留是为了将来「按阶段建行」的场景；
            #     当前转移路径不走它。）
            db.commit()

    def note_activation_error(self, plugin_id: str, error: str | None) -> None:
        """写激活器目击到 plugin_state.last_error（ADR-0005 落库通道的宿主侧）。

        由 core/app.py 注入给 PluginActivator.set_persist_hook。状态行不存在时
        不新建 —— 激活器只对**已有注册态**的插件报错，凭空建行会让
        list_plugins 的「无状态行 ⇒ 按来源取默认」口径漂移。
        """
        with kernel_session() as db:
            st = db.get(PluginState, plugin_id)
            if st is None:
                return
            st.last_error = error
            db.commit()

    # ───────────────────────── 挂载 / 摘卸载 ─────────────────────────
    # 挂载统一走 core.plugins.discover.mount_plugin（前置小步：
    # 与 create_app 启动全量挂载共用单一入口，本类不再各写一份）。
    def _unmount(self, info: PluginInfo, registry: Any) -> None:
        if info.id in registry.mounted():
            registry.unmount(info.id)

    # ───────────────────────── 启停 ─────────────────────────
    def enable(self, plugin_id: str, registry: Any) -> dict[str, Any]:
        info = self._require(plugin_id)
        if info.kind == "core":
            # core 永远启用；这里只确保状态正确，不摘挂。
            self._upsert_state(info, enabled=True)
            return self.get_plugin(plugin_id)

        # ★ ADR-0005（2026-10-03）· 可逆启用：每一步都必须携带逆，
        #   失败时按「施加的逆序」回放（Theorem 16）。
        #
        # ★★ 本轮改造：从手写的 `_record` / `_compensate` 累加器
        #    改成第四章的原语 `kernel.effect()`。
        #    两套机制并存 = 同一事实两处定义（逆序规则写两遍、
        #    「什么算全局副作用」两个标准、漏登记一处只在那条路径出事）。
        #    ⇒ 统一到原语，顺序规则只剩一处。
        #
        # ★ 施加顺序 = [迁移(DATA), provides(R), requires(R), 路由(R)]
        #   逆序回放 = 撤路由 → 撤 requires → 撤 provides → 回滚迁移
        #   ⇒ 与 ADR-0005 要求的「先摘路由、后回滚迁移」一致 ✓
        #
        # ★ 为什么**状态写入不算 effect**：
        #   状态（`plugin_state`）不是「需要撤销的全局副作用」，
        #   它是「这个插件现在处于什么状态」这个事实本身。
        #   disable 的语义是「停用」（写 enabled=False），
        #   而 effect 的逆是「回到之前」——两者不是一回事。
        #   失败路径的状态处理在下面的 `except` 分支里显式做。
        prev_state = self._state_snapshot(plugin_id)

        try:
            with get_kernel().effect(
                f"enable:{plugin_id}", kind=EffectKind.RUNTIME, plugin_id=plugin_id
            ) as eff:
                # ── 1. 状态（RUNTIME）────────────────────────────────────
                # 状态写入**进 effect**：失败时自动回滚到调用前
                # （原先有行就恢复原值，原先无行就删行 ⇒ 回到「未安装」）。
                # ★ 为什么状态算 effect：它是「对全局注册表的一次写入」，
                #   不带逆就会留下「状态说已启用、实际没装上」的半提交态。
                #   而 disable 的「停用」语义由**显式写 enabled=False** 表达
                #   （见 disable()），不是靠撤销这条 effect——两件事不是一回事。
                eff.add(
                    do=lambda: self._upsert_state(info, enabled=True, last_error=None),
                    undo=lambda: self._restore_state_or_mark_disabled(
                        plugin_id, prev_state, info
                    ),
                )
                # ★ 进入 LOADING：状态机的意义就是「系统知道自己在哪一步」。
                #   从此刻起，中途失败会被记为 FAILED（而不是「enabled=false
                #   但不知道停在哪」）。
                _transition(plugin_id, Phase.LOADING, PhaseReason.STARTUP)

                from db.engine import get_engine

                engine = get_engine()

                # ── 2. 迁移（DATA）────────────────────────────────────────
                # 逆是「只回滚本次真正执行的那几个版本」。台账幂等，故先取
                # 差分；绝不能调全量 rollback_migrations（会 drop 掉承载历史数据的表）。
                #
                # ★ 差分必须**延迟到撤销时**才计算：run_migrations 若中途失败
                #（如 0001 成功、0002 抛错），`after_versions` 那行根本不会执行，
                #   提前算出的差分会漏掉已执行的 0001。故 lambda 在 undo 时才取
                #   「当前台账 − before」，捕获真正落库的那些版本。
                before_versions = recorded_versions(engine, info.id)

                def _undo_migrations(
                    _engine: Any = engine,
                    _info: PluginInfo = info,
                    _before: set[str] = before_versions,
                ) -> None:
                    """回滚本次真正执行的那几个版本（差分在**撤销时**才算）。

                    ★ 差分延迟到撤销时计算：run_migrations 若中途失败
                    （如 0001 成功、0002 抛错），`after_versions` 那行根本不执行，
                    提前算出的差分会漏掉已执行的 0001。
                    """
                    rollback_versions(
                        _engine,
                        _info,
                        sorted(recorded_versions(_engine, _info.id) - _before),
                    )

                def _do_migrations(_engine: Any = engine, _info: PluginInfo = info) -> None:
                    """执行迁移（effect 的 do 必须返回 None）。"""
                    run_migrations(_engine, _info)

                eff.add(
                    do=_do_migrations,
                    undo=_undo_migrations,
                    # ★ 迁移是 DATA：暂停时**保留**（停用不删数据），
                    #   卸载时才撤销（卸载要删表）。
                    kind=EffectKind.DATA,
                )

                # ── 3. 能力提供（RUNTIME）───────────────────────────────
                # provides 走 `table.provide()`，它内部本身就是 effect ⇒
                # 自带逆。登记它时**不**再单独记一条 undo，
                # 否则同一件事会有两个逆，撤一次不够 ⇒ 依赖表残留。
                _register_dependencies(info)

                # ── 4. 依赖声明（RUNTIME）───────────────────────────────
                # `requires` 只是一条声明（没有 do/undo 对），
                # 撤销要走 `unregister_requirements`。
                eff.add(
                    do=lambda: _register_requires_only(info),
                    undo=lambda: _unregister_requirements(plugin_id),
                )

                # ── 5. 路由挂载（RUNTIME）──────────────────────────────
                # 放在最后：这样逆序回放时**先摘路由**（ADR-0005）。
                eff.add(
                    do=lambda: mount_plugin(info, registry),
                    undo=lambda: self._unmount(info, registry),
                )

                with kernel_session() as db:
                    err = run_lifecycle_hook(info, "enable", db=db)
        except Exception as exc:  # noqa: BLE001
            # ★ 逆序回放由 `kernel.effect()` 的上下文管理器负责：
            #   块内抛异常 → 已登记的部分按施加顺序的逆序撤回来。
            #   （手写 `_compensate` 已被原语取代——顺序规则只有一处。）
            #
            # ★ effect 的逆已经把「状态 / 迁移 / 路由 / 依赖」恢复到调用前
            #   （`kernel.effect` 在异常时先回放再 raise，所以到这里时
            #     回放**已经完成**）。
            #   ⇒ 此处只做**目击**：把错误写进 last_error，
            #     绝不改 enabled（那是状态回滚已经决定的事）。
            #
            #   ★ 为什么「不改 enabled」：可逆性的定义是「失败 ⇒ 回到调用前」。
            #     旧实现在这里写 enabled=False，是**在原样之上加了个猜测**
            #     （猜它本来没启用）——而这个猜测可能与事实相反
            #     （重调边界：调用前就是启用态，写 False 等于擅自停用它）。
            #     现在由 `_restore_state` 按快照精确恢复。
            #
            # ★ 注意：本分支不会因「生命周期钩子抛异常」而进 —— run_lifecycle_hook
            #   自己吞异常并返回错误串（见 lifecycle.py），钩子失败是**软失败**，
            #   走下方 `if err:` 分支（不回滚，语义同旧实现）。
            self.note_activation_error(plugin_id, str(exc))
            # ★ 装载失败 → FAILED。保留现场（last_error + 审计）等人处理，
            #   **不自动重试**（失败原因通常是确定性的，重试一百次结果相同）。
            _transition(plugin_id, Phase.FAILED, PhaseReason.LOAD_ERROR)
            raise
        if err:
            # 钩子未抛异常、只回报了错误串：效果已生效（不回滚，语义同旧实现），
            # 仅把错误落到 last_error 供 health/readyz 目击。
            self._upsert_state(info, enabled=True, last_error=err)
        # ★ 装载全部成功 → ACTIVE。
        #   ★ 语义不只是「标记为可用」，而是「它的 effect 全部已注册、
        #     路由已挂、依赖已就绪」——所以唯一进入路径是经过 LOADING。
        _transition(plugin_id, Phase.ACTIVE, PhaseReason.STARTUP)
        # ★ 依赖重算：它提供的能力可能让别的插件从「依赖丧失」恢复。
        #   放在阶段转移**之后**——这样重算时看到的是最终状态，
        #   不会对着一个「还是 LOADING」的插件做满足性判定。
        _notify_dependencies(registry)
        return self.get_plugin(plugin_id)


    def _state_snapshot(self, plugin_id: str) -> dict[str, Any] | None:
        """取插件状态行的可恢复快照（无行时为 None）。"""
        with kernel_session() as db:
            st = db.get(PluginState, plugin_id)
            if st is None:
                return None
            return {
                "version": st.version,
                "kind": st.kind,
                "enabled": st.enabled,
                "last_error": st.last_error,
                "granted_permissions": st.granted_permissions,
            }

    def _restore_state_or_mark_disabled(
        self,
        plugin_id: str,
        snapshot: dict[str, Any] | None,
        info: PluginInfo,
    ) -> None:
        """恢复状态行；原先无行时留一个 `enabled=False` 的**可诊断行**。

        ★ 为什么不是「无行就删行」：
          ADR-0005 定的语义是「enable 失败 ⇒ 留下可诊断的状态行」
          （`enabled=False` + `last_error`）——
          删行的话，这次失败**不留任何痕迹**，下次启动时系统根本不知道
          「这里曾经失败过」，人也就无从排查。
          既有测试 `test_enable_failure_leaves_no_midstate` 守着这条。

        ★ 而 install 失败是**另一种**语义：回到「未安装」⇒ 不留行。
          两者不矛盾，因为它们回答的问题不同：
            - enable 失败 ⇒ 「装上了，但没起来」⇒ 留行说明状态
            - install 失败 ⇒ 「没装上」⇒ 不留行说明状态
          （install 走的是 `_restore_state`，enable 走的是本函数。）

        ★ 这条逆同时服务 disable：
          disable 用 `replay_runtime` 撤 RUNTIME effect，会执行到本函数
          ——它的效果正是「enabled=False」，与 disable 的语义一致。
          ⇒ 一条逆，两处用，语义不打架。
        """
        self._restore_state(plugin_id, snapshot)
        if snapshot is None:
            self._upsert_state(info, enabled=False, last_error=None)

    def _restore_state(self, plugin_id: str, snapshot: dict[str, Any] | None) -> None:
        """把状态行恢复到快照值；原先无行则删除该行（完整回滚）。"""
        with kernel_session() as db:
            st = db.get(PluginState, plugin_id)
            if snapshot is None:
                if st is not None:
                    db.delete(st)
                    db.commit()
                return
            if st is None:
                st = PluginState(id=plugin_id)
                db.add(st)
            st.version = snapshot["version"]
            st.kind = snapshot["kind"]
            st.enabled = snapshot["enabled"]
            st.last_error = snapshot["last_error"]
            st.granted_permissions = snapshot["granted_permissions"]
            db.commit()

    def disable(self, plugin_id: str, registry: Any) -> dict[str, Any]:
        info = self._require(plugin_id)
        if info.kind == "core":
            raise ForbiddenError(f"内核插件「{plugin_id}」不可禁用（kind=core）")
        # ★ ADR-0005（2026-10-03）：逆序。加载顺序是
        #     [迁移 → 挂路由 → on_enable]，卸载必须严格逆序：
        #     [on_disable → 摘路由 → (迁移回滚)]
        #   旧实现先 _unmount 再跑 on_disable —— 插件撤自己的东西时，它的路由
        #   已经消失了（顺序反了）。迁移回滚仍留在 uninstall：disable 的语义是
        #   「停用」不是「删数据」，表要留着等下次 enable（run_migrations 台账
        #   幂等，不会重跑也不会丢数据）。
        #
        # ★★ 本轮改造：撤除走 `kernel.replay_runtime(plugin_id)`——
        #    它只撤 RUNTIME 类 effect（路由 / 依赖声明 / 能力提供），
        #    DATA 类（迁移）**留在栈上**。
        #    这正是 `EffectKind` 两个取值的意义：
        #      RUNTIME = 暂停时撤（停止对外服务，数据留着）
        #      DATA    = 暂停时保留（表留着等下次启用）
        #    以前这层区分是散落在各个手写分支里的判断，
        #    现在由原语统一执行——**「什么该撤」只由 kind 决定**。
        with kernel_session() as db:
            err = run_lifecycle_hook(info, "disable", db=db)
        # ★ 撤 RUNTIME effect（逆序：路由 → requires → provides）。
        #   路由会被摘掉，能力提供会被撤销，依赖声明会被注销。
        get_kernel().replay_runtime(plugin_id)
        self._upsert_state(info, enabled=False, last_error=err)
        # ★ ACTIVE → PAUSED。
        #   PAUSED 的语义 = 「数据保留、停止对外服务」——与 disable 完全一致
        #   （表留着、迁移不回滚、路由摘掉）。
        #   ★ 转移放在**摘路由之后**：不变量是「阶段非 ACTIVE/PAUSED 时路由必须
        #     摘掉」——先转阶段再摘，中间那一瞬会违反不变量；
        #     而先摘后转，中间那一瞬只是「阶段说 ACTIVE 但路由已摘」，
        #     那是**更弱**的偏差（对外不可达），且窗口极短。
        #   ★ 而 on_disable 必须在摘路由**之前**跑（ADR-0005）：
        #     插件撤自己的东西时，它的路由应该还在。
        _transition(plugin_id, Phase.PAUSED, PhaseReason.MANUAL)
        # ★ 依赖重算：它撤掉的能力可能让依赖者丧失依赖
        #   （deactivation=auto 的会被转 PAUSED；manual 的保持 ACTIVE
        #     但被运行时校验拦下——与升级前行为一致）。
        _notify_dependencies(registry)
        return self.get_plugin(plugin_id)


    # ───────────────────────── 安装 / 卸载（仅 third-party） ─────────────────────────
    def install(self, plugin_id: str, registry: Any) -> dict[str, Any]:
        info = self._require(plugin_id)
        if info.kind in ("core", "builtin"):
            raise ConflictError(
                f"插件「{plugin_id}」是 {info.kind}，{_BUILTIN_INCLUSION_NOTE}"
            )
        # 校验权限声明合法（★ 2026-09-25 先归一化，兼容对象格式）
        #
        # ★ ADR-0005（2026-10-03）：这里**只做结构校验**（拒绝非法权限串）。
        #   真正的授权决策在 uninstall 前的审批环节 —— 对应论文 §6.3 的
        #   capability request：请求应在**组件运行前**被审阅批准，而非运行时才发现。
        for perm in normalize_permissions(info.manifest.get("permissions")):
            if not is_valid_permission(perm):
                raise ForbiddenError(f"插件「{plugin_id}」声明了非法权限：{perm!r}")

        # ★ ADR-0005 · 可逆安装：每一步携带逆，失败按逆序回放。
        #
        # 旧实现与 enable() 同病 —— 四个 effect 全都没有逆：
        #   [建表 → on_install → 状态落 enabled=True → 挂路由]
        # 任何一步失败都会留下中间态：表建了、钩子跑了、状态说已启用，但路由不在，
        # 且前端「安装」按钮看到的是一次 500 —— 再点一次会撞上已存在的表。
        #
        # ★ 本轮改造：走第四章原语 `kernel.effect()`（不再用手写累加器）。
        #   施加顺序 = [迁移(D), install_hook(R), 状态(R), 路由(R), provides(R)]
        #   逆序回放 = provides → 路由 → 状态 → install_hook → 迁移 ✓
        #
        # ⚠️ 与 enable() 的一处不同：install 的迁移逆用**差分**，
        #   与 enable() 一致。理由：install 失败后**可能重试**，
        #   而重试时台账里可能已有部分版本——用全量回滚会删掉
        #   「上次成功、且表里已有历史数据」的表。
        #   （早期版本用全量，是从 enable 的注释里抄的；本轮改正——
        #     两处语义必须一致，且「差分」是更安全的那一个。）
        prev_state = self._state_snapshot(plugin_id)

        try:
            with get_kernel().effect(
                f"install:{plugin_id}", kind=EffectKind.RUNTIME, plugin_id=plugin_id
            ) as eff:
                from db.engine import get_engine

                engine = get_engine()

                # ── 1. 迁移（DATA）────────────────────────────────────────
                before_versions = recorded_versions(engine, info.id)

                def _undo_migrations(
                    _engine: Any = engine,
                    _info: PluginInfo = info,
                    _before: set[str] = before_versions,
                ) -> None:
                    """回滚本次真正执行的那几个版本（差分在**撤销时**才算）。

                    ★ 差分延迟到撤销时计算：run_migrations 若中途失败
                    （如 0001 成功、0002 抛错），`after_versions` 那行根本不执行，
                    提前算出的差分会漏掉已执行的 0001。
                    """
                    rollback_versions(
                        _engine,
                        _info,
                        sorted(recorded_versions(_engine, _info.id) - _before),
                    )

                def _do_migrations(_engine: Any = engine, _info: PluginInfo = info) -> None:
                    """执行迁移（effect 的 do 必须返回 None）。"""
                    run_migrations(_engine, _info)

                eff.add(
                    do=_do_migrations,
                    undo=_undo_migrations,
                    kind=EffectKind.DATA,
                )

                # ── 2. install 钩子（RUNTIME）─────────────────────────────
                with kernel_session() as db:
                    run_lifecycle_hook(info, "install", db=db)
                # 钩子的逆是与它配对的 teardown。run_lifecycle_hook 对
                # 「钩子缺失/不抛」一律 best-effort 吞掉（见 lifecycle.py），故这里
                # 无 on_uninstall 时是无害 no-op —— 与 uninstall() 第 1 步同一调用。
                eff.add(
                    do=lambda: None,
                    undo=lambda: self._run_hook_safe(info, "uninstall"),
                )

                # ── 3. 状态（RUNTIME）─────────────────────────────────────
                # 状态写入进 effect：install 失败 ⇒ 自动回滚到调用前
                # （原先无行 ⇒ 删行 ⇒ 回到「未安装」态）。
                eff.add(
                    do=lambda: self._upsert_state(info, enabled=True, last_error=None),
                    undo=lambda: self._restore_state(plugin_id, prev_state),
                )

                # ── 4. 路由挂载（RUNTIME）─────────────────────────────────
                eff.add(
                    do=lambda: mount_plugin(info, registry),
                    undo=lambda: self._unmount(info, registry),
                )

                # ── 5. 能力提供（RUNTIME）────────────────────────────────
                # provides 走 `table.provide()`，它内部本身就是 effect ⇒ 自带逆。
                _register_dependencies(info)
                eff.add(
                    do=lambda: _register_requires_only(info),
                    undo=lambda: _unregister_requirements(plugin_id),
                )
        except Exception as exc:  # noqa: BLE001
            # ★ 逆序回滚由 `kernel.effect()` 负责（已施加的部分按逆序撤回来）。
            #
            # ★ effect 的逆已经把状态恢复到调用前（原先无行 ⇒ 已删行 ⇒
            #   回到「未安装」态，与 uninstall 后的 vacant 态一致）。
            #   ⇒ 此处只做目击：把错误写进 last_error（仅当原先就有行时——
            #   没有行就没地方写，也不该为此新建一行）。
            if prev_state is not None:
                self.note_activation_error(plugin_id, str(exc))
            raise
        return self.get_plugin(plugin_id)

    def _run_hook_safe(self, info: PluginInfo, stage: str) -> None:
        """在补偿路径上跑钩子：自己吞异常（补偿绝不允许二次上抛）。"""
        try:
            with kernel_session() as db:
                run_lifecycle_hook(info, stage, db=db)
        except Exception as hook_exc:  # noqa: BLE001
            log.warning(
                "插件补偿钩子执行失败",
                extra={
                    "module": info.id,
                    "stage": stage,
                    "error": f"{type(hook_exc).__name__}: {hook_exc}",
                },
            )

    def uninstall(self, plugin_id: str, registry: Any) -> dict[str, Any]:
        info = self._require(plugin_id)
        if info.kind in ("core", "builtin"):
            raise ForbiddenError(
                f"插件「{plugin_id}」是 {info.kind}，不可卸载"
                "（builtin/core 随系统发布，只能禁用）。"
            )
        # ★ 先转 UNLOADING：卸载是**不可逆**的，必须先进入「正在撤销」，
        #   才不会在卸载中途崩掉时留下「不知道在不在卸」的状态。
        #   （技术报告 §3.2：UNLOADING 的存在就是为了表达「卸载到一半」，
        #     启动对账看到这个状态会**强制跑完卸载**。）
        _transition(plugin_id, Phase.UNLOADING, PhaseReason.UNINSTALL)
        # 1) 生命周期 on_uninstall
        with kernel_session() as db:
            run_lifecycle_hook(info, "uninstall", db=db)
        # 2) 撤掉全部 RUNTIME effect（路由 / 依赖声明 / 能力提供）。
        #    逆序由原语保证（施加顺序的逆序）。
        #    ★ 只撤 RUNTIME，不撤 DATA —— DATA 里装的是迁移，
        #    而卸载要**全量**删表（差分只回滚「本次执行的」，
        #    历史迁移建的表会留下），那由第 3 步的 rollback_migrations 负责。
        #    ⇒ 两处分工明确：RUNTIME 交给原语，DATA 交给全量回滚。
        get_kernel().replay_runtime(plugin_id)
        # 3) 回滚迁移（删表）
        from db.engine import get_engine

        engine = get_engine()
        rollback_migrations(engine, info)
        # 4) 清状态表（plugin_state + plugin_setting）
        with kernel_session() as db:
            st = db.get(PluginState, plugin_id)
            if st is not None:
                db.delete(st)
            for row in db.exec(
                select(PluginSetting).where(PluginSetting.plugin_id == plugin_id)
            ).all():
                db.delete(row)
            db.commit()
        # 5) 删文件（插件目录整体移除）
        if info.directory.exists():
            shutil.rmtree(info.directory, ignore_errors=True)
        # ★ 卸载完成 → TERMINATED（终态）。
        #   注意：状态行在第 4 步被删了，所以这次转移**会因「行不存在」被跳过**
        #   （见 `apply_transition` 的处理）。这是有意的：
        #   插件已经不存在了，没有状态可记；而「已卸载」这个事实由
        #   「plugin_state 里没有这一行」本身表达。
        _transition(plugin_id, Phase.TERMINATED, PhaseReason.UNINSTALL)
        return {"id": plugin_id, "uninstalled": True}

    # ───────────────────────── 设置 ─────────────────────────
    def get_settings(self, plugin_id: str) -> dict[str, Any]:
        self._require(plugin_id)
        with kernel_session() as db:
            return read_settings(db, plugin_id)

    def set_settings(self, plugin_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        info = self._require(plugin_id)
        with kernel_session() as db:
            return write_settings(db, info, plugin_id, settings)

    # ───────────────────────── 健康 ─────────────────────────
    def health(self, plugin_id: str) -> dict[str, Any]:
        item = self.get_plugin(plugin_id)
        return {
            "id": plugin_id,
            "enabled": item["enabled"],
            "last_error": item["last_error"],
            "ok": item["enabled"] and item["last_error"] is None,
        }


_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager
