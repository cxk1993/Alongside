import { create } from "zustand";

import { reapplySkin } from "./skin";

/**
 * 主题控制器（设计令牌驱动）。
 *
 * 约定：<html data-theme="dark|light"> 决定当前配色。
 * 默认暗色；初始值优先级：localStorage > prefers-color-scheme > dark。
 * 切换后写入 localStorage，刷新后记住。
 *
 * ★ 与皮肤的关系：皮肤有 `tokens.light` / `tokens.dark` 两个分区，
 *   换主题时必须按新分区重算一次，否则分区形同虚设 —— 所以切完 `data-theme`
 *   立刻 `reapplySkin()`（无皮肤时它是空操作）。
 */

export type Theme = "dark" | "light";

const THEME_KEY = "alongside.theme";

function systemPrefersLight(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-color-scheme: light)").matches;
}

export function readStoredTheme(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

export function initialTheme(): Theme {
  return readStoredTheme() ?? (systemPrefersLight() ? "light" : "dark");
}

/** 在 React 渲染前调用，避免主题闪烁。 */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", theme);
  // 皮肤若有亮暗分区，按新主题重算（无皮肤 = 空操作）。
  reapplySkin();
}

export function setThemeAttr(theme: Theme): void {
  applyTheme(theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* localStorage 不可用时静默降级，主题仍生效 */
  }
}

interface ThemeState {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggle: () => void;
}

export const useTheme = create<ThemeState>((set, get) => ({
  theme: initialTheme(),
  setTheme: (theme) => {
    setThemeAttr(theme);
    set({ theme });
  },
  toggle: () => {
    const next: Theme = get().theme === "dark" ? "light" : "dark";
    setThemeAttr(next);
    set({ theme: next });
  },
}));

/** 应用启动钩子：确定初始主题并写到 <html>。 */
export function initTheme(): void {
  const t = initialTheme();
  applyTheme(t);
  useTheme.setState({ theme: t });
}
