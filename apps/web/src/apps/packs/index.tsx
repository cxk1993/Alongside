/**
 * 整合包管理入口（独立 Dock 窗口）。
 *
 * ★ 为什么是独立窗口而不是并进「系统窗多页」：
 *   本版 Dock 的显隐唯一杠杆是 `Dock.tsx` 的 `DOCK_MERGE`（坑谱 #15：
 *   `manifest.slots` / `tabs[].entry` 都不产生隐藏效果）。既然不合并，
 *   它自然单独出一个底端栏按钮——**零改已有业务文件**。
 */
import type { ComponentType } from "react";
import PacksApp from "./PacksApp";
import "./packs.css";

const PacksModule = {
  manifestId: "packs",
  Component: PacksApp as ComponentType,
  slots: {},
};

export default PacksModule;
export { PacksApp };
