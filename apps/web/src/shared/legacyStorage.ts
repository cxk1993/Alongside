/**
 * 品牌改名（旧名 lifeos → 新名 alongside）的**本地存储兼容层**。
 *
 * ★ 为什么需要：浏览器里的键名/库名一律带品牌前缀。直接换前缀 = 用户的登录态、
 *   窗口布局、壁纸配置、页签位置在升级瞬间全部「蒸发」—— 改名不该让人以为系统坏了。
 *
 * 两条规矩：
 *   1. **只读不写回旧名**：迁移把旧键**复制**到新键，旧键留着，回滚旧版本时数据还在。
 *   2. **失败不阻断**：隐私模式、配额满、老浏览器不支持 —— 一律静默降级，
 *      最坏结果就是「用回默认值」，绝不能让应用起不来。
 */

/** 旧品牌期的 localStorage 前缀。 */
export const LEGACY_STORAGE_PREFIX = "lifeos.";

/** 现品牌名的 localStorage 前缀。 */
export const STORAGE_PREFIX = "alongside.";

/**
 * 把 `lifeos.*` 的 localStorage 键复制到 `alongside.*`（幂等，新键已存在则不覆盖）。
 *
 * 正常路径由 `index.html` 的内联脚本在 bundle 之前跑（那是唯一能保证早于
 * 任何模块求值的位置）；这里保留同名实现，供测试与非 HTML 入口复用。
 */
export function migrateLegacyLocalStorage(
  storage: Storage | undefined = typeof localStorage === "undefined" ? undefined : localStorage,
): void {
  if (!storage) return;
  try {
    // 先收集再写：边遍历边 setItem 会改变 length，导致漏键。
    const legacyKeys: string[] = [];
    for (let i = 0; i < storage.length; i += 1) {
      const key = storage.key(i);
      if (key && key.startsWith(LEGACY_STORAGE_PREFIX)) legacyKeys.push(key);
    }
    for (const legacyKey of legacyKeys) {
      const newKey = STORAGE_PREFIX + legacyKey.slice(LEGACY_STORAGE_PREFIX.length);
      if (storage.getItem(newKey) === null) {
        const value = storage.getItem(legacyKey);
        if (value !== null) storage.setItem(newKey, value);
      }
    }
  } catch {
    /* 存储不可用 → 静默跳过（见文件头第 2 条规矩）。 */
  }
}

/**
 * 解析 IndexedDB 库名（改名兼容）。
 *
 * IndexedDB **没有改名 API**，而 `open()` 会顺手把不存在的库建出来 —— 贸然用新名
 * 等于凭空造一个空库、把旧库里的数据晾在一边（用户的壁纸就「没了」）。
 * 所以先问浏览器「现在有哪些库」：新库还没建、旧库还在 → 继续用旧库，
 * 数据照常读得到；等哪天迁移把数据搬进新库，这段自然失效。
 */
export async function resolveDbName(preferred: string, legacy: string): Promise<string> {
  try {
    const factory = indexedDB as IDBFactory & { databases?: () => Promise<{ name?: string }[]> };
    if (typeof factory.databases !== "function") return preferred;
    const existing = (await factory.databases()).map((db) => db.name);
    if (!existing.includes(preferred) && existing.includes(legacy)) return legacy;
  } catch {
    /* 不支持 / 被拒绝 → 用新名 */
  }
  return preferred;
}
