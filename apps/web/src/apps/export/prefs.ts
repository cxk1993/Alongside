/** export 窗 · 用户偏好。 */
export interface ExportPrefs {
  lastProfile: string | null;
}

const KEY = "alongside.plugin.export.prefs";

export function loadPrefs(): ExportPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { lastProfile: null };
    const p = JSON.parse(raw) as Partial<ExportPrefs>;
    return { lastProfile: p.lastProfile ?? null };
  } catch {
    return { lastProfile: null };
  }
}

export function savePrefs(p: ExportPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* 忽略 */
  }
}
