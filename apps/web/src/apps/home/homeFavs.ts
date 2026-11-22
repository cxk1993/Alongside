/**
 * ★ 我的常用（「我的今天」主屏的那个小框子）。
 *
 * 主人 2026-11-22 原话：「自选框没有展示出来，用户不能自由选择需要的功能放到那个
 * 小框子里一键启用，也根本就没有那个小框子」。
 *
 * 三条硬口径（来自团队 `23-前端重排·两屏分工.md`）：
 *   1. 存储键 **`alongside.favs.v1`**（新键；旧值不复活 —— 旧布局里的 `alongside.dock-*`
 *      是折叠状态，语义完全不同，拿来复用只会把两种历史混成一种 bug）；
 *   2. **默认 5 个**：日历 / 待办 / 习惯 / 账本 / 笔记 —— 空框子等于没做；
 *   3. 上限 **8**（再多就是第二个 Dock）。
 *
 * 这里只放纯函数：读/写/加/删/挪都可用 localStorage 替身单测，不依赖 React。
 * 未知 id 不在这里过滤（那是渲染层的事：模块清单是运行时才到的，这里无权判死活）。
 */

export const FAVS_KEY = "alongside.favs.v1";
export const FAVS_MAX = 8;

/** 默认预置 5 个（顺序即展示顺序）。 */
export const FAVS_DEFAULT: readonly string[] = ["calendar", "todo", "habits", "finance", "notes"];

/** 把任意读到的值收敛成「干净的 id 数组」：非数组/含非字符串 → 当作无效。 */
export function normalizeFavs(raw: unknown): string[] | null {
  if (!Array.isArray(raw)) return null;
  const out: string[] = [];
  for (const v of raw) {
    if (typeof v !== "string" || !v.trim()) return null;
    if (!out.includes(v)) out.push(v);
  }
  return out.slice(0, FAVS_MAX);
}

/** 读：键不存在/坏值 → 默认 5 个；**显式的空数组尊重用户**（他清空了就真是空的）。 */
export function loadFavs(storage: Pick<Storage, "getItem"> | undefined = safeStorage()): string[] {
  if (!storage) return [...FAVS_DEFAULT];
  try {
    const text = storage.getItem(FAVS_KEY);
    if (text === null) return [...FAVS_DEFAULT];
    const parsed = normalizeFavs(JSON.parse(text));
    return parsed ?? [...FAVS_DEFAULT];
  } catch {
    // localStorage 不可用 / 值不是 JSON：退回默认，不抛错（主屏不该因为存储坏了白屏）
    return [...FAVS_DEFAULT];
  }
}

export function saveFavs(ids: string[], storage: Pick<Storage, "setItem"> | undefined = safeStorage()): void {
  if (!storage) return;
  try {
    storage.setItem(FAVS_KEY, JSON.stringify(ids.slice(0, FAVS_MAX)));
  } catch {
    /* 配额满 / 隐私模式：仅本次会话生效，不阻断 */
  }
}

/** 加：已存在则原样返回（不重复、不改变顺序）；超上限则忽略并返回原数组。 */
export function addFav(list: string[], id: string): string[] {
  if (!id || list.includes(id)) return list;
  if (list.length >= FAVS_MAX) return list;
  return [...list, id];
}

export function removeFav(list: string[], id: string): string[] {
  return list.filter((x) => x !== id);
}

/** 挪一格（delta=-1 左、+1 右）。越界就原样返回 —— 不能因为按到边界把顺序打乱。 */
export function moveFav(list: string[], id: string, delta: number): string[] {
  const i = list.indexOf(id);
  if (i < 0) return list;
  const j = i + delta;
  if (j < 0 || j >= list.length) return list;
  const out = [...list];
  out.splice(j, 0, out.splice(i, 1)[0]);
  return out;
}

/** 拖拽落位：把 id 移到 targetId 的位置（HTML5 DnD 给的是目标元素，不是下标）。 */
export function moveFavTo(list: string[], id: string, targetId: string): string[] {
  const i = list.indexOf(id);
  const j = list.indexOf(targetId);
  if (i < 0 || j < 0 || i === j) return list;
  const out = [...list];
  out.splice(j, 0, out.splice(i, 1)[0]);
  return out;
}

function safeStorage(): Storage | undefined {
  try {
    if (typeof localStorage === "undefined") return undefined;
    return localStorage;
  } catch {
    return undefined; // 隐私模式下访问 localStorage 会抛 SecurityError
  }
}