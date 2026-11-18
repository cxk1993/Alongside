/**
 * 图标集契约（IconSet）
 *
 * 设计依据：`demo重构/开源调研-皮肤与图标-20261118/`（C 路图标系统调研 + D 路宿主换肤调研）。
 *
 * 三条硬约束（都有实证支撑）：
 *   1. **名字由内核定，插件只能给值** —— 抄 VS Code 的 `--vscode-<id>` 范式：
 *      皮肤/图标插件无权发明新语义名，只能覆盖已注册的键，冲突从根上消失。
 *   2. **同步渲染，不许异步取图标** —— Lucide 的 DynamicIcon 是 useState+useEffect，
 *      一屏 10+ 图标会「图标后到」；Dock 是首屏元素，必须同步。
 *   3. **未知名必须优雅降级** —— 现状是中文单字兜底（manifest.icon 里确实有「课/记/程」），
 *      这条行为不能丢。
 */
import type { ReactElement } from "react";

export interface IconSet {
  /** 集合 id（默认 "lucide"；第三方集合建议用 `<pluginId>:<name>`） */
  id: string;
  /** 人类可读名 */
  name: string;
  /** 许可证声明（必须非空：调研里 Iconify 的 280 个集合有 5 个 GPL、2 个 NC 不可用） */
  license: string;
  /** 语义名 → 图形。键必须是内核语义名，值可以是任意 React 元素（多 path 用 Fragment） */
  icons: Record<string, ReactElement>;
}
