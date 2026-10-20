/** query 窗 · 用户偏好。 */
export interface QueryPrefs {
  lastPreset: string | null;
}

const KEY = "alongside.plugin.query.prefs";

export function loadPrefs(): QueryPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { lastPreset: null };
    const p = JSON.parse(raw) as Partial<QueryPrefs>;
    return { lastPreset: p.lastPreset ?? null };
  } catch {
    return { lastPreset: null };
  }
}

export function savePrefs(p: QueryPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* 忽略 */
  }
}
