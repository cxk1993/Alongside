import { useMemo } from "react";

import { buildModuleGraph } from "./moduleGraph";
import { pluginHost } from "./ModuleRegistry";
import { useDesktopStore } from "./store";

/**
 * 底部模块坞。列出已注册模块；点图标 → 开窗（singleton 重复点=聚焦）。
 * 模块有打开的窗口时，图标亮起小圆点；被禁用时置灰且不可点。
 *
 * ★ 合并入口（主人 ⑤/⑫ 2026-09-25；2026-11-09 重构）：
 *   被容器吸收的模块**不单独出按钮**，统一由**容器模块**承载
 * （点容器 → 窗口内多页/页签展示，这正是主人要的「一个按钮打开，窗口内分页」）。
 *
 * ★ **吸收关系的唯一真相源是 manifest.tabs**（推导见 `kernel/moduleGraph.ts`）：
 *   容器型模块在 manifest 里声明 `tabs: [{ key, label, entry? }]`，每个 tab 指向的
 *   子模块即被该容器吸收。本文件**不持有任何「子 id → 容器 id」手写表**
 *   （旧 `DOCK_MERGE` 已删 —— 它与 SystemApp 的 `DEFAULT_TABS` 各写一遍并已分叉：
 *   diary 丢入口、habits 双入口）。
 *   注：Dock 遍历 store 全部模块、**从不读 manifest.slots**（坑谱 #15），
 *   所以显隐只能在吸收关系里表达，不能靠「摘 desktop.dock 槽」。
 *
 * ★ fail-safe：容器模块**不存在或未启用** ⇒ 保留子模块原按钮（绝不因合并丢入口）。
 */

export function Dock() {
  const modules = useDesktopStore((s) => s.modules);
  const windows = useDesktopStore((s) => s.windows);
  const openIds = new Set(windows.map((w) => w.moduleId));

  // 纯推导：同一份 manifest 永远推出同一张图（不读 DOM、不存状态）。
  const graph = useMemo(() => buildModuleGraph(modules), [modules]);

  /** 某容器是否可用（存在 + 启用）——不可用则不合并，保原按钮。 */
  const containerReady = (target: string | undefined) =>
    Boolean(target && modules[target]?.enabled);

  /** 子模块是否已被「可用容器」吸收。 */
  const isMerged = (id: string) => containerReady(graph.containerOf(id));

  return (
    <div className="dock" role="toolbar" aria-label="模块坞">
      {Object.values(modules)
        .filter((reg) => !isMerged(reg.manifest.id))
        .map((reg) => {
          const m = reg.manifest;
          const open = openIds.has(m.id);
          // 该容器吸收了几个子模块（用于角标提示，让主人知道"合并入口"里有什么）
          const absorbed = graph.absorbedBy(m.id).filter((child) => Boolean(modules[child]));
          const cls = [
            "dock__item",
            open ? "dock__item--open" : "",
            reg.enabled ? "" : "dock__item--disabled",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <button
              key={m.id}
              type="button"
              className={cls}
              aria-label={
                absorbed.length > 0
                  ? `${m.name}（含 ${absorbed.length} 个并入模块）`
                  : m.name
              }
              title={absorbed.length > 0 ? `含：${absorbed.join(" / ")}` : m.name}
              disabled={!reg.enabled}
              onClick={() => {
                if (reg.enabled) pluginHost.open(m.id);
              }}
            >
              {open ? <span className="dock__dot" aria-hidden="true" /> : null}
              <span className="dock__icon">{m.icon ?? m.name.slice(0, 1)}</span>
              <span className="dock__label">{m.name}</span>
              {absorbed.length > 0 ? (
                <span className="dock__merged-badge" aria-hidden="true">
                  +{absorbed.length}
                </span>
              ) : null}
            </button>
          );
        })}
    </div>
  );
}
