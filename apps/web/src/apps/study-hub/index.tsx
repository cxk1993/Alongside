import type { ComponentType } from "react";

import StudyHubApp from "./StudyHubApp";

/**
 * 学习软件聚合入口。加载器按 manifest.entry = "@/apps/study-hub" 找到本文件，
 * 再取 PluginModule.Component 渲染窗口。
 */
const PluginModule = {
  manifestId: "study-hub",
  Component: StudyHubApp as ComponentType,
  slots: {},
};

export default PluginModule;
export { StudyHubApp };