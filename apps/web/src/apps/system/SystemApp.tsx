/**
 * 系统窗 · V6 系统编排件（主人⑫：能力目录/MCP/推送/账户与鉴权 合并一窗多页）。
 *
 * 形态：真实模块（manifest entry=@/apps/system）→ Dock 自动出「系统」按钮，
 * 零 Dock.tsx 改动（模块注册表驱动，一切皆插件口径）。
 *
 * ★ 页签**只从 manifest.tabs 来**（2026-11-09 重构）：
 *   `services/api/modules/system/manifest.json` 的 `tabs: [{ key, label, entry? }]`
 *   就是这张窗的页清单 —— 也是「哪些子模块被本容器吸收」的唯一声明
 *   （Dock 侧推导见 `kernel/moduleGraph.ts`）。
 *   本文件不再持有 `DEFAULT_TABS` / `KNOWN_APPS`：旧的两份手写表与 Dock 的
 *   DOCK_MERGE 已实测分叉（diary 丢入口、habits 双入口）。
 *   - entry 页 = 该模块 App（内核按 `@/apps/<dir>` 目录约定懒加载，无手写映射表）；
 *   - 无 entry 页（如 auth）= 薄壳「已接入（后端服务）」语义。
 */
import MultitabFrame from "@/kernel/MultitabFrame";
import { useContainerPages } from "@/kernel/containerPages";

export default function SystemApp() {
  const pages = useContainerPages("system");

  return <MultitabFrame ariaLabel="系统页签" persistKey="system" pages={pages} />;
}
