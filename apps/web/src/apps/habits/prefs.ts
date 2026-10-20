/** 习惯窗 · 用户自定义偏好（localStorage，插件私有键）。 */
export interface HabitsPrefs {
  showArchived: boolean;
  compact: boolean;
  sortBy: "default" | "streak" | "name";
}

const KEY = "alongside.plugin.habits.prefs";

export function loadPrefs(): HabitsPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { showArchived: false, compact: false, sortBy: "default" };
    const p = JSON.parse(raw) as Partial<HabitsPrefs>;
    return {
      showArchived: Boolean(p.showArchived),
      compact: Boolean(p.compact),
      sortBy: p.sortBy === "streak" || p.sortBy === "name" ? p.sortBy : "default",
    };
  } catch {
    return { showArchived: false, compact: false, sortBy: "default" };
  }
}

export function savePrefs(p: HabitsPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* 存储异常忽略 */
  }
}
