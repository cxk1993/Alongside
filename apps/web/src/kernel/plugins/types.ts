import type { ComponentType } from "react";
import type { ModuleKind, ModuleManifest } from "../types";

/**
 * 前端插件协议类型（对齐后端 contracts/plugin.schema.json，并补充插槽贡献类型）。
 *
 * 内核只认"一个模块"，其余插件字段（provides/requires/slots/emits…
 * permissions/lifecycle）已在 kernel/types.ts 的 ModuleManifest/PluginProtocolFields
 * 中强类型化，这里只做扩展点枚举的窄化，不再重复声明，避免两处形状漂移。
 * 插件入口默认导出形如 `{ manifestId, Component, slots? }`（见 kernel/types.ts 的 PluginModule）。
 */

/** 15 个已知扩展点（与总纲 §1.3.4 一一对应；扩展点是内核资产，第三方不得新增）。 */
export type SlotName =
  | "desktop.dock"
  | "desktop.widget"
  | "dashboard.card"
  | "topbar.action"
  | "calendar.block.renderer"
  | "calendar.overlay"
  | "inspector.panel"
  | "settings.page"
  | "search.provider"
  | "ai.tool"
  | "notification.channel"
  | "command.palette"
  | "window.sidecar"
  // U3（桌面级）：左右侧边栏（E5 window.sidecar 的桌面级兄弟，schema 已会签 14 枚举）
  | "desktop.dock-left"
  | "desktop.dock-right";

export interface PluginManifest extends ModuleManifest {
  /** 扩展点窄化：manifest.slots 只能从已知 15 个里挑。 */
  slots?: SlotName[];
}

export type PluginSource = "core" | "builtin" | "third-party";

export interface PluginInfo {
  id: string;
  name: string;
  version: string;
  kind: ModuleKind;
  enabled: boolean;
  source: PluginSource;
  description?: string;
  icon?: string;
  manifest: PluginManifest;
}

/** 一个插件往某个扩展点挂的具体内容（一张卡片、一个动作、一个工具……）。 */
export interface SlotContribution {
  slot: SlotName;
  pluginId: string;
  component: ComponentType;
  /** 排序权重，越大越靠后。 */
  order?: number;
  title?: string;
  /** E5：sidecar 附着目标窗口 moduleId；缺省 = 所有窗口。 */
  attachTo?: string;
}

/** 注入到 React 树的插件上下文（由 PluginProvider 提供）。 */
export interface PluginContextValue {
  plugins: PluginInfo[];
  loading: boolean;
  error?: string;
  enable: (id: string) => Promise<void>;
  disable: (id: string) => Promise<void>;
  install: (id: string) => Promise<void>;
  uninstall: (id: string) => Promise<void>;
  refresh: () => Promise<void>;
  getContributions: (slot: SlotName) => SlotContribution[];
}
