/**
 * sidebar 插件入口（manifest.entry = "@/apps/sidebar"）。
 */
import type { ComponentType } from "react";
import SidebarApp from "./SidebarApp";
import SidebarDock from "./SidebarDock";
import "./sidebar.css";

/**
 * ★ 2026-11-22（汐瑶）补上 `slots` 贡献 —— 这是「声明了没人实现」的那个洞：
 *   后端 manifest 声明 `slots: ["desktop.dock-left","desktop.dock-right"]`，
 *   前端这里一直是 `slots: {}` ⇒ 宿主 DesktopSidebar 的 items.length 恒 0
 *   ⇒ 内置导航永远占满左栏，用户自定义条目「加不进/看不到」。
 *
 * 只挂 `desktop.dock-left`：左侧是「导航/结构」惯例位；
 * 右侧已由 `DesktopTodaySummary`（今日摘要）占着，再塞同批条目会重复。
 * 若将来两侧都要显示，加一行 `"desktop.dock-right": SidebarDock as ComponentType` 即可。
 */
const PluginModule = {
  manifestId: "sidebar",
  Component: SidebarApp as ComponentType,
  slots: {
    "desktop.dock-left": SidebarDock as ComponentType,
  },
};

export default PluginModule;
export { SidebarApp };
