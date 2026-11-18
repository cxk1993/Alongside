/**
 * 皮肤运行时契约（Skin Contract v1.1 的 L2 + L3 实现）
 *
 * 目标：让**第三方皮肤插件**能读写皮肤，而不是只能盲写 <html> 内联变量。
 *
 * 设计依据（调研见 demo重构/开源调研-皮肤与图标-20261118/）：
 *   · 抄 VS Code：**名字由内核定**，插件只能给注册表里的令牌赋值（本文件的 allowlist 由
 *     validateSkinPack 兜底，拒绝含 var( / url( / !important 的值）。
 *   · 抄 Obsidian：皮肤包是**数据不是代码**（JSON token 表），所以不需要给插件执行权限。
 *   · 抄 VS Code 三段式：**卸载/重置皮肤不得改变用户已选外观**——reset 明确清空而不是回退到某个内置。
 *
 * 对外面（window.AlongsideSkin）：
 *   get()                    → 当前生效的 Skin（无皮肤时返回默认）
 *   apply(patch | Skin)      → 合并/整体替换，走「校验 → 应用 → 持久化 → 广播」
 *   reset()                  → 清空皮肤，回到令牌默认
 *   export()                 → 皮肤包 JSON 字符串（可分享）
 *   import(json)             → 导入皮肤包；坏包返回 {ok:false, errors}
 *   subscribe(fn)            → 订阅变更，返回退订函数（面板与插件共用同一真相）
 *
 * 事件：window 上广播 `alongside:skin-changed`，detail = Skin | null（reset 时为 null）。
 *   ★ 为什么不用 manifest.emits：后端 EventBus 的 emits 是「服务端声明式发布」，
 *     前端皮肤变更走浏览器事件；等内核有前端事件桥再接，不在 manifest 里作虚假声明。
 */
import {
  applySkin,
  clearSkin,
  defaultSkin,
  loadSkin,
  saveSkin,
  normalizeSkin,
  validateSkinPack,
  type Skin,
  type SkinValidation,
} from "./skin";

export const SKIN_EVENT = "alongside:skin-changed";
export const SKIN_API_VERSION = 1;

type Listener = (skin: Skin | null) => void;

const listeners = new Set<Listener>();
/** 进程内唯一真相：面板、插件 API、事件三处都读它 */
let current: Skin | null = null;
let booted = false;

function ensureBooted(): void {
  if (booted) return;
  booted = true;
  // 启动时把已持久化的皮肤读进内存（initSkin 已在 main.tsx 首帧前应用过变量）
  try {
    const stored = loadSkin();
    current = stored && Object.keys(stored.tokens ?? {}).length > 0 ? stored : null;
  } catch {
    current = null;
  }
}

/** 订阅变更（useSyncExternalStore 友好）。 */
export function subscribeSkin(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** 当前皮肤快照（无自定义皮肤返回 null，而不是 defaultSkin——调用方据此区分「用户没选」）。 */
export function getCurrentSkin(): Skin | null {
  ensureBooted();
  return current;
}

function broadcast(skin: Skin | null): void {
  current = skin;
  for (const fn of listeners) {
    try {
      fn(skin);
    } catch {
      /* 订阅方自己炸不能影响别人 */
    }
  }
  if (typeof window !== "undefined" && typeof CustomEvent === "function") {
    window.dispatchEvent(new CustomEvent(SKIN_EVENT, { detail: skin }));
  }
}

/**
 * 提交一份皮肤：校验 → 应用 → 持久化 → 广播。
 * 校验不过时**什么都不改**（不写变量、不落盘、不广播），把 errors 原样带回。
 */
export function commitSkin(input: unknown): SkinValidation & { skin?: Skin } {
  ensureBooted();
  const normalized = normalizeSkin(input);
  const result = applySkin(normalized);
  if (!result.ok) return result;
  saveSkin(normalized);
  broadcast(normalized);
  return { ...result, skin: normalized };
}

/** 局部更新：拿当前（或默认）皮肤做底，深合并 patch 的三分区后再提交。 */
export function patchSkin(
  patch: Partial<Skin> & { tokens?: Skin["tokens"] },
): SkinValidation & { skin?: Skin } {
  ensureBooted();
  const base = current ?? defaultSkin();
  const merged: Skin = {
    version: 2,
    base: patch.base ?? base.base,
    tokens: {
      theme: { ...(base.tokens.theme ?? {}), ...(patch.tokens?.theme ?? {}) },
      light: { ...(base.tokens.light ?? {}), ...(patch.tokens?.light ?? {}) },
      dark: { ...(base.tokens.dark ?? {}), ...(patch.tokens?.dark ?? {}) },
    },
    meta: patch.meta ?? base.meta,
  };
  return commitSkin(merged);
}

/** 清空皮肤（回令牌默认），并广播 null。 */
export function resetSkin(): void {
  ensureBooted();
  clearSkin();
  broadcast(null);
}

/** 导出皮肤包（可分享格式；与 shadcn registry-item 的三分区同构）。 */
export function exportSkinPack(): string {
  ensureBooted();
  const skin = current ?? defaultSkin();
  return JSON.stringify(
    {
      alongsideSkin: 1,
      exportedAt: new Date().toISOString(),
      meta: skin.meta ?? { name: "我的皮肤" },
      skin,
    },
    null,
    2,
  );
}

/** 导入皮肤包；同时接受「裸 Skin」与「带 alongsideSkin 外壳的包」。 */
export function importSkinPack(raw: string): SkinValidation & { skin?: Skin } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    return { ok: false, errors: [`不是合法 JSON：${(e as Error).message}`], warnings: [] };
  }
  const candidate =
    parsed && typeof parsed === "object" && "skin" in (parsed as Record<string, unknown>)
      ? (parsed as { skin: unknown }).skin
      : parsed;
  const check = validateSkinPack(candidate);
  if (!check.ok) return check;
  return commitSkin(candidate);
}

export interface AlongsideSkinApi {
  version: number;
  get: () => Skin;
  apply: (patch: unknown) => { ok: boolean; errors: string[]; warnings: string[] };
  reset: () => void;
  export: () => string;
  import: (raw: string) => { ok: boolean; errors: string[]; warnings: string[] };
  subscribe: (fn: Listener) => () => void;
}

/**
 * 挂 window.AlongsideSkin。幂等（热重载/多次调用安全）。
 * 返回卸载函数（测试用）。
 */
export function installSkinApi(): () => void {
  if (typeof window === "undefined") return () => {};
  ensureBooted();
  const prev = (window as unknown as { AlongsideSkin?: AlongsideSkinApi }).AlongsideSkin;
  const api: AlongsideSkinApi = {
    version: SKIN_API_VERSION,
    get: () => getCurrentSkin() ?? defaultSkin(),
    apply: (patch: unknown) => {
      const looksLikeSkin =
        patch &&
        typeof patch === "object" &&
        ("version" in (patch as Record<string, unknown>) ||
          "tokens" in (patch as Record<string, unknown>));
      const r = looksLikeSkin ? commitSkin(patch) : patchSkin(patch as Partial<Skin>);
      return { ok: r.ok, errors: r.errors, warnings: r.warnings };
    },
    reset: () => resetSkin(),
    export: () => exportSkinPack(),
    import: (raw: string) => {
      const r = importSkinPack(raw);
      return { ok: r.ok, errors: r.errors, warnings: r.warnings };
    },
    subscribe: (fn: Listener) => subscribeSkin(fn),
  };
  (window as unknown as { AlongsideSkin?: AlongsideSkinApi }).AlongsideSkin = api;
  return () => {
    (window as unknown as { AlongsideSkin?: AlongsideSkinApi }).AlongsideSkin = prev;
  };
}

/** 测试用：重置内存态（不清 localStorage，调用方自己清）。 */
export function _resetSkinRuntime(): void {
  current = null;
  booted = false;
  listeners.clear();
}
