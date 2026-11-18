/**
 * 插件提供的皮肤（皮肤插件 v1）。
 *
 * ★ 为什么单独一个存储域，不和「我的皮肤」混在一张表里
 * --------------------------------------------------
 * 两者**归属不同**，混在一处会同时坏掉三件事：
 *   1. 停用/卸载插件要能整组清掉它的皮肤 —— 混在用户列表里就得逐条辨认来源，
 *      某个字段漏了就把用户自己存的皮肤删了；
 *   2. 用户库有条数上限（50）—— 插件皮肤若占额度，装三个皮肤插件就能挤掉
 *      用户自己存的皮肤；
 *   3. 用户能重命名/删除自己存的条目 —— 对插件提供的条目做这两件事毫无意义
 *      （下次同步就回来了），却会让 UI 出现「删了又回来」的怪行为。
 * ⇒ 分开存：插件的是**只读派生数据**，随插件来、随插件走；用户的是**用户资产**。
 *
 * ★ 为什么同步是「整组覆盖」而不是「增量合并」
 * ------------------------------------------
 * 真相在后端（`GET /api/v1/plugins` 的 `manifest.skinPack`）。前端这份是缓存。
 * 缓存跟随真相的方式只有一种是对的：**拿到全量就整组重写**。增量合并会在
 * 「插件被卸载时它的皮肤留在库里」这种边界上出错，而那种残留恰恰是最难查的
 * ——用户会看到一份来源已经不存在的皮肤。
 *
 * ★ 正在使用的那份皮肤，来源没了怎么办
 * ------------------------------------
 * 必须回退默认，不能「停在一份来源已消失的皮肤上」。理由：皮肤的可信度来自
 * 它的来源（插件包、作者、版本）。来源消失后继续套用，等于让一个已经不在
 * 系统里的东西继续定义界面——出问题时无从追溯，也无法靠重装恢复一致。
 * 这与「删除库项不改变当前外观」不冲突：**删除是用户主动、来源仍在**；
 * **来源消失是系统被动、来源已不在**。两者的正确反应本来就不该一样。
 */
import { normalizeSkin, type Skin } from "./skin";
import type { AlongsideSkinApi } from "./skin-runtime";
import { getCurrentSkin } from "./skin-runtime";

/** 插件皮肤缓存键（与用户库 alongside.skins.v1 分离）。 */
export const SKIN_PLUGIN_KEY = "alongside.skinPlugins.v1";

export interface PluginSkinEntry {
  /** `plugin:<插件 id>` —— 一个插件在 v1 只贡献一份皮肤 */
  id: string;
  pluginId: string;
  /** 展示名：皮肤自报的名字优先，其次插件名 */
  name: string;
  skin: Skin;
  /** 主色，供列表画色点 */
  accent?: string;
}

/** 后端下发的插件清单里，我们关心的那几个字段。 */
export interface PluginSkinSource {
  id: string;
  name?: string | null;
  enabled?: boolean;
  manifest?: {
    skinPack?: unknown;
    skinError?: string | null;
  } | null;
}

export interface SyncResult {
  added: number;
  removed: number;
  /** 插件侧报的皮肤问题（后端 skinError 原样带回，便于在界面上提示） */
  errors: string[];
  /** 因为来源消失而回退成默认皮肤 */
  resetApplied: boolean;
}

function storage(): Storage | null {
  try {
    return typeof window !== "undefined" && window.localStorage ? window.localStorage : null;
  } catch {
    return null;
  }
}

function accentOf(skin: Skin): string | undefined {
  return (
    skin.tokens?.theme?.["--accent"] ??
    skin.tokens?.dark?.["--accent"] ??
    skin.tokens?.light?.["--accent"]
  );
}

/** 读缓存（坏 JSON / 脏项静默丢弃，永不抛）。 */
export function loadPluginSkins(): PluginSkinEntry[] {
  const store = storage();
  if (!store) return [];
  let raw: string | null = null;
  try {
    raw = store.getItem(SKIN_PLUGIN_KEY);
  } catch {
    return [];
  }
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as { version?: number; items?: unknown };
    if (!parsed || !Array.isArray(parsed.items)) return [];
    return parsed.items.filter(
      (it): it is PluginSkinEntry =>
        !!it &&
        typeof (it as PluginSkinEntry).id === "string" &&
        typeof (it as PluginSkinEntry).pluginId === "string" &&
        typeof (it as PluginSkinEntry).name === "string" &&
        !!(it as PluginSkinEntry).skin,
    );
  } catch {
    return [];
  }
}

function savePluginSkins(items: PluginSkinEntry[]): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(SKIN_PLUGIN_KEY, JSON.stringify({ version: 1, items }));
  } catch {
    /* 配额满：静默降级（本就有后端真相，下次同步会重建） */
  }
}

/** 比较两份皮肤是否同一份（用于判断「当前使用的是不是这份插件皮肤」）。 */
function sameSkin(a: Skin | null, b: Skin): boolean {
  if (!a) return false;
  try {
    return JSON.stringify(normalizeSkin(a)) === JSON.stringify(normalizeSkin(b));
  } catch {
    return false;
  }
}

/**
 * 用后端的插件清单整组重建插件皮肤缓存。
 *
 * 只收 `enabled !== false` 且有 `manifest.skinPack` 的插件；后端若给了
 * `skinError`（文件坏了/缺了），收进 errors 让界面提示，但**不阻断**其它插件。
 * 幂等：同样的输入反复调用不会产生重复条目。
 */
export function syncPluginSkins(sources: PluginSkinSource[]): SyncResult {
  const before = loadPluginSkins();
  const next: PluginSkinEntry[] = [];
  const errors: string[] = [];
  for (const src of sources ?? []) {
    if (!src || typeof src.id !== "string") continue;
    if (src.enabled === false) continue;
    const err = src.manifest?.skinError;
    if (typeof err === "string" && err) {
      errors.push(`${src.name || src.id}：${err}`);
      continue;
    }
    const pack = src.manifest?.skinPack;
    if (!pack || typeof pack !== "object") continue;
    // 后端已做外壳与分区的浅检；这里再用运行时同一个 normalizeSkin 归一，
    // 保证「插件皮肤」与「用户皮肤」在后续应用路径上完全是同一种东西。
    try {
      const skin = normalizeSkin((pack as { skin?: unknown }).skin ?? pack);
      if (!skin) continue;
      next.push({
        id: `plugin:${src.id}`,
        pluginId: src.id,
        name: (skin.meta?.name || src.name || src.id).slice(0, 40),
        skin,
        accent: accentOf(skin),
      });
    } catch (exc) {
      const why = exc instanceof Error ? exc.message : String(exc);
      errors.push(`${src.name || src.id}：皮肤无法归一（${why}）`);
    }
  }

  const nextIds = new Set(next.map((it) => it.id));
  const removedEntries = before.filter((it) => !nextIds.has(it.id));

  // 正在用的皮肤若来自「这次消失的插件」，回退默认（见文件头第二条注释）。
  let resetApplied = false;
  const current = getCurrentSkin();
  if (current) {
    for (const gone of removedEntries) {
      if (sameSkin(current, gone.skin)) {
        resetApplied = true;
        break;
      }
    }
  }

  savePluginSkins(next);
  return {
    added: next.filter((it) => !before.some((old) => old.id === it.id)).length,
    removed: removedEntries.length,
    errors,
    resetApplied,
  };
}

/** 应用一份插件皮肤；失败时原样透出原因，不改动当前外观。 */
export function applyPluginSkin(
  id: string,
  api: AlongsideSkinApi,
): { ok: boolean; errors: string[]; warnings: string[] } {
  const entry = loadPluginSkins().find((it) => it.id === id);
  if (!entry) return { ok: false, errors: ["插件皮肤已不在（插件可能被停用）"], warnings: [] };
  return api.apply(entry.skin);
}

/**
 * 导出一份插件皮肤为**可分享的皮肤包字符串**。
 *
 * ★ 为什么插件皮肤也要能导出
 * -------------------------
 * 「只读」说的是「不能改不能删」，不是「不能带走」。皮肤是**数据不是隐私**：
 * 它只有颜色与圆角，没有用户信息。装上插件顺手存一份自己的主题，是合理期待；
 * 停用插件后还能继续用自己存的那份，也正是「库与来源分离」的价值。
 *
 * ★ 格式必须与用户库一致（alongsideSkin:1 外壳）
 * -------------------------------------------
 * 导出两种格式等于让「用户存的」和「插件带的」不能互相导入 ——
 * 那正是这一整套设计要消灭的分叉。
 */
export function exportPluginSkin(id: string): string | null {
  const entry = loadPluginSkins().find((it) => it.id === id);
  if (!entry) return null;
  const payload = {
    alongsideSkin: 1,
    exportedAt: new Date().toISOString(),
    meta: { ...(entry.skin.meta ?? {}), name: entry.name },
    skin: { ...entry.skin, meta: { ...(entry.skin.meta ?? {}), name: entry.name } },
  };
  return JSON.stringify(payload, null, 2);
}

/** 清空缓存（登出/回退用）。 */
export function clearPluginSkins(): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(SKIN_PLUGIN_KEY);
  } catch {
    /* 忽略 */
  }
}
