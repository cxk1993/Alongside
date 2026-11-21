/**
 * 共处工作台插件入口（manifest.entry = "@/apps/collab"）。
 *
 * 两个出口同一张面：普通窗口（Component）+ 桌面级背景面（desktop.widget 插槽）。
 * ★ 贡献的 slot 必须与 manifest.slots 一致（契约硬规则），本插件声明 ["desktop.widget"]。
 */
import type { ComponentType } from "react";
import CollabApp from "./CollabApp";
import "./collab.css";

const PluginModule = {
  manifestId: "collab",
  Component: CollabApp as ComponentType,
  slots: {
    "desktop.widget": CollabApp as ComponentType,
  },
};

export default PluginModule;
export { CollabApp };
