/**
 * 本地皮肤库（Local Skin Library）
 *
 * 目的：让「皮肤」从一次性状态变成**可收藏、可分享、可切换**的东西——
 * 这是皮肤生态的最小闭环；调研里 Catppuccin 能有 200+ 应用，靠的就是皮肤是
 * 「有名字、有版本、能分发的数据」，而不是用户手调出来的一堆值。
 *
 * 存储：localStorage["alongside.skins.v1"] = { version: 1, items: SkinEntry[] }
 *   · 与「当前生效的皮肤」分开存（alongside.skin.v1 那份仍由 skin.ts 管），
 *     这样「应用皮肤」只是从库里挑一份，删库不影响当前外观（抄 VS Code 的三段式）。
 *   · 单项上限 64KB；库上限 50 条 —— 防 localStorage 被塞爆（配额一般 5MB）。
 */
import { exportSkinPack, importSkinPack, type AlongsideSkinApi } from "./skin-runtime";
import { normalizeSkin, type Skin } from "./skin";

export const SKIN_LIBRARY_KEY = "alongside.skins.v1";
export const SKIN_LIBRARY_MAX = 50;
const MAX_ITEM_BYTES = 64 * 1024;

export interface SkinEntry {
  /** 稳定 id（导入同一份包时用它去重） */
  id: string;
  name: string;
  /** 一份完整 Skin（v2 三分区） */
  skin: Skin;
  /** 导入/保存时间 ISO */
  savedAt: string;
  /** 主色，供列表里画色点 */
  accent?: string;
}

interface LibraryFile {
  version: 1;
  items: SkinEntry[];
}

function storage(): Storage | null {
  try {
    return typeof window !== "undefined" && window.localStorage ? window.localStorage : null;
  } catch {
    return null;
  }
}

/** 读库（坏 JSON / 脏项一律静默丢弃，永不抛）。 */
export function loadLibrary(): SkinEntry[] {
  const store = storage();
  if (!store) return [];
  let raw: string | null = null;
  try {
    raw = store.getItem(SKIN_LIBRARY_KEY);
  } catch {
    return [];
  }
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as LibraryFile;
    if (!parsed || !Array.isArray(parsed.items)) return [];
    return parsed.items.filter(
      (it): it is SkinEntry =>
        !!it && typeof it.id === "string" && typeof it.name === "string" && !!it.skin,
    );
  } catch {
    return [];
  }
}

function saveLibrary(items: SkinEntry[]): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(SKIN_LIBRARY_KEY, JSON.stringify({ version: 1, items }));
  } catch {
    /* 配额满：静默降级（内存态仍然可用） */
  }
}

/** 从皮肤里抽一个稳定指纹：name + 规范化后的 token 表（排序） */
function fingerprintOf(skin: Skin): string {
  const norm = normalizeSkin(skin);
  const flat = Object.entries(norm.tokens ?? {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([zone, table]) => `${zone}:${JSON.stringify(table ?? {})}`)
    .join("|");
  const seed = `${norm.meta?.name ?? ""}#${flat}`;
  // 简易稳定哈希（djb2），不引入依赖
  let h = 5381;
  for (let i = 0; i < seed.length; i += 1) h = (h * 33) ^ seed.charCodeAt(i);
  return `skin-${(h >>> 0).toString(36)}-${seed.length.toString(36)}`;
}

function accentOf(skin: Skin): string | undefined {
  return (
    skin.tokens?.theme?.["--accent"] ??
    skin.tokens?.dark?.["--accent"] ??
    skin.tokens?.light?.["--accent"]
  );
}

/** 保存一份皮肤进库（同指纹覆盖，保留最早保存时间）。返回条目。 */
export function saveToLibrary(skin: Skin, nameHint?: string): SkinEntry {
  const items = loadLibrary();
  // 没名字时用主色生成一个可读默认名（「自定义 · #22c55e」），别叫「未命名」——
  // 库里一排「未命名皮肤」等于没名字。
  const fallbackName = accentOf(skin) ? `自定义 · ${accentOf(skin)}` : "未命名皮肤";
  const name = (nameHint || skin.meta?.name || fallbackName).trim().slice(0, 40);
  const id = fingerprintOf(skin);
  const existing = items.findIndex((it) => it.id === id);
  const entry: SkinEntry = {
    id,
    name,
    skin,
    savedAt: existing >= 0 ? items[existing].savedAt : new Date().toISOString(),
    accent: accentOf(skin),
  };
  const next =
    existing >= 0 ? items.map((it, i) => (i === existing ? entry : it)) : [entry, ...items];
  saveLibrary(next.slice(0, SKIN_LIBRARY_MAX));
  return entry;
}

export function removeFromLibrary(id: string): void {
  saveLibrary(loadLibrary().filter((it) => it.id !== id));
}

export function renameInLibrary(id: string, name: string): void {
  const clean = name.trim().slice(0, 40);
  if (!clean) return;
  saveLibrary(loadLibrary().map((it) => (it.id === id ? { ...it, name: clean } : it)));
}

/** 应用库里的一份皮肤；返回结果原样透出（校验失败时说明原因）。 */
export function applyFromLibrary(
  id: string,
  api: AlongsideSkinApi,
): { ok: boolean; errors: string[]; warnings: string[] } {
  const entry = loadLibrary().find((it) => it.id === id);
  if (!entry) return { ok: false, errors: ["皮肤不在库里（可能已被删除）"], warnings: [] };
  return api.apply(entry.skin);
}

/** 从文本导入进库（先校验，过了才入库）。接受皮肤包或裸 Skin。 */
export function importToLibrary(raw: string): {
  ok: boolean;
  errors: string[];
  warnings: string[];
  entry?: SkinEntry;
} {
  if (raw.length > MAX_ITEM_BYTES) {
    return { ok: false, errors: [`皮肤包过大（>${MAX_ITEM_BYTES / 1024}KB）`], warnings: [] };
  }
  // 用同一个校验器（validateSkinPack 在 importSkinPack 内部跑）
  const check = importSkinPack(raw);
  if (!check.ok || !check.skin)
    return { ok: check.ok, errors: check.errors, warnings: check.warnings };
  const entry = saveToLibrary(check.skin);
  return { ok: true, errors: [], warnings: check.warnings, entry };
}

/** 导出库里一份皮肤为可分享的皮肤包字符串。 */
export function exportFromLibrary(id: string): string | null {
  const entry = loadLibrary().find((it) => it.id === id);
  if (!entry) return null;
  // 直接复用运行时导出格式（含 alongsideSkin 外壳）
  const payload = {
    alongsideSkin: 1,
    exportedAt: new Date().toISOString(),
    meta: { ...(entry.skin.meta ?? {}), name: entry.name },
    skin: { ...entry.skin, meta: { ...(entry.skin.meta ?? {}), name: entry.name } },
  };
  return JSON.stringify(payload, null, 2);
}

/** 「把当前皮肤存进库」：读运行时当前值，无自定义时先导出默认皮肤再存。 */
export function saveCurrentToLibrary(api: AlongsideSkinApi, name?: string): SkinEntry {
  return saveToLibrary(api.get(), name);
}

/** 文件名净化：Windows/macOS/Linux 都不接受的字符一律换成 -。
 *  不用正则——字符类里的转义在各解析器/ESLint 规则下行为不一致（踩过一次）。 */
export function safeFileName(input: string): string {
  // 用码点集合而不是字符串字面量：反斜杠在字符串与正则里的转义规则不同，
  // 这里刻意避开所有转义（92=\ 47=/ 58=: 42=* 63=? 34=" 60=< 62=> 124=|）
  const forbidden = new Set([92, 47, 58, 42, 63, 34, 60, 62, 124]);
  const out = Array.from(input)
    .map((ch) => (forbidden.has(ch.codePointAt(0) ?? 0) ? "-" : ch))
    .join("")
    .trim();
  return out || "skin";
}

/** 供 UI 用：把当前皮肤导出成文件内容（带文件名建议）。 */
export function exportCurrentPack(api: AlongsideSkinApi): { filename: string; content: string } {
  const skin = api.get();
  const name = safeFileName(skin.meta?.name ?? "alongside-skin");
  return { filename: `${name}.skin.json`, content: exportSkinPack() };
}
