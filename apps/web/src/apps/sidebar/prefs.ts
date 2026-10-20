/** 侧栏窗 · 用户偏好。 */
export interface SidebarPrefs {
  groupFilter: string;
  hideDisabled: boolean;
}

const KEY = "alongside.plugin.sidebar.prefs";

export function loadPrefs(): SidebarPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { groupFilter: "", hideDisabled: false };
    const p = JSON.parse(raw) as Partial<SidebarPrefs>;
    return {
      groupFilter: p.groupFilter ?? "",
      hideDisabled: Boolean(p.hideDisabled),
    };
  } catch {
    return { groupFilter: "", hideDisabled: false };
  }
}

export function savePrefs(p: SidebarPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* 忽略 */
  }
}
