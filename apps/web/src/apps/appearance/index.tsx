/**
 * 外观与皮肤插件入口（manifest.entry = "@/apps/appearance"）。
 *
 * 内核的模块加载器按 `import.meta.glob("../apps/<id>/index.tsx")` 发现入口，
 * 因此这个文件必须默认导出 { manifestId, Component, slots }。
 */
import type { ComponentType } from "react";

import AppearanceApp from "./AppearanceApp";
import "./appearance.css";

const PluginModule = {
  manifestId: "appearance",
  Component: AppearanceApp as ComponentType,
  slots: {},
};

export default PluginModule;

export { AppearanceApp };
