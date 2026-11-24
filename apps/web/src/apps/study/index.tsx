/**
 * 学业页的模块入口（2026-11-24）。
 *
 * 背景：`apps/study/StudyApp.tsx` 一直只被 `schedule` 容器内联引用（手写 pages），
 * 而 `kernel/containerPages.tsx` 与 `moduleGraph` 的目录约定是 `src/apps/<dir>/index.tsx`
 * —— 缺这个文件，任何按 `entry: "@/apps/study"` 挂载都会落到「入口缺失」薄壳。
 * 补上它属于纯加法：schedule 仍走自己的手写页，现有行为零变化。
 */
import type { ComponentType } from "react";

import StudyApp from "./StudyApp";

const PluginModule = {
  manifestId: "study",
  Component: StudyApp as ComponentType,
  slots: {},
};

export default PluginModule;
export { StudyApp };
