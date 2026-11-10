import { useDesktopStore } from "./store";
import type { ModuleManifest, PluginModule } from "./types";

/**
 * 模块注册表（T02 实现 · T14 洁癖约束）。
 *
 * 职责：
 *   - 把 modules.json / 后端插件清单注册进桌面 store。
 *   - 把 entry 解析成 React.lazy 可用的 loader。
 *
 * ★ 内核洁癖：本文件**不认识任何业务插件名**。
 *   业务入口一律按**目录约定**用 `import.meta.glob` 自动发现：
 *     `src/apps/<插件目录>/index.tsx`
 *   加新插件 = 多一个目录 + manifest 登记 entry，**不必改本文件**。
 *   （硬编码 `@/apps/calendar` 之类映射表会让 check_kernel_purity 失败，
 *   且直接违反「不改内核就能加插件」的总纲判据。）
 *
 * 演示 mock 仍保留显式表：它们属于内核自测夹具，不是业务插件。
 * 入口抛错由 WindowFrame 错误边界兜底（只该窗口占位，桌面不白屏）。
 */

type ModLoader = () => Promise<{ default: PluginModule }>;

/** 相对本文件（src/kernel → src/apps、src/shared）。Vite 可静态分析、可分包。 */
const APP_ENTRY_GLOB = import.meta.glob("../apps/*/index.tsx");
const MOCK_ENTRY_GLOB = import.meta.glob("../shared/mocks/*.tsx");

function fromGlob(map: Record<string, () => Promise<unknown>>, key: string): ModLoader | undefined {
  const fn = map[key];
  if (!fn) return undefined;
  return () => fn() as Promise<{ default: PluginModule }>;
}

/** 内核演示夹具（非业务）：entry 说明符 → glob 键。 */
const DEMO_ENTRY_KEYS: Record<string, string> = {
  "@mocks/alpha": "../shared/mocks/alpha.tsx",
  "@mocks/beta": "../shared/mocks/beta.tsx",
  "@mocks/gamma": "../shared/mocks/gamma.tsx",
  "@mocks/delta": "../shared/mocks/delta.tsx",
  "@mocks/broken": "../shared/mocks/broken.tsx",
};

function demoLoader(entry: string): ModLoader | undefined {
  const key = DEMO_ENTRY_KEYS[entry];
  return key ? fromGlob(MOCK_ENTRY_GLOB, key) : undefined;
}

/**
 * 把 entry 解析成 loader；找不到返回 undefined。
 * 支持约定路径：
 *   `@/apps/<dir>` / `@/apps/<dir>/index.tsx` / `/src/apps/<dir>/index.tsx`
 *   `@mocks/<name>`
 */
function findLoader(entry: string): ModLoader | undefined {
  const demo = demoLoader(entry);
  if (demo) return demo;

  const appMatch =
    /^(?:@\/?apps\/|\/src\/apps\/)([^/]+?)(?:\/index\.tsx)?$/.exec(entry) ??
    /^\.\.\/apps\/([^/]+?)(?:\/index\.tsx)?$/.exec(entry);
  if (appMatch) {
    const loader = fromGlob(APP_ENTRY_GLOB, `../apps/${appMatch[1]}/index.tsx`);
    if (loader) return loader;
  }

  const mockMatch = /^@mocks\/([^/]+)$/.exec(entry);
  if (mockMatch) {
    const loader = fromGlob(MOCK_ENTRY_GLOB, `../shared/mocks/${mockMatch[1]}.tsx`);
    if (loader) return loader;
  }

  return undefined;
}

/**
 * 把 entry 解析成 loader。找不到时返回 rejected Promise（由窗口错误边界展示）。
 *
 * ★ 需要「先判断再渲染」的场景（容器页签装配）用 `canResolveLoader`，
 *   否则会先渲染一页「加载失败」而不是诚实地说「入口缺失」。
 */
export function resolveLoader(entry: string): ModLoader {
  const loader = findLoader(entry);
  if (loader) return loader;
  return () => Promise.reject(new Error(`未找到插件入口：${entry}`));
}

/** 该 entry 是否真有可加载的 App 文件（不执行 import，只看 glob 键）。 */
export function canResolveLoader(entry: string): boolean {
  return findLoader(entry) !== undefined;
}

export interface PluginHost {
  register(manifest: ModuleManifest): void;
  unregister(id: string): void;
  open(id: string, opts?: unknown): void;
  enable(id: string): void;
  disable(id: string): void;
}

export const pluginHost: PluginHost = {
  register: (m) => useDesktopStore.getState().registerModule(m),
  unregister: (id) => useDesktopStore.getState().unregisterModule(id),
  open: (id) => useDesktopStore.getState().openWindow(id),
  enable: (id) => useDesktopStore.getState().enableModule(id),
  disable: (id) => useDesktopStore.getState().disableModule(id),
};

/** 从清单数组批量注册（Desktop 在启动时调用）。 */
export function registerModules(list: ModuleManifest[]): void {
  const store = useDesktopStore.getState();
  list.forEach((m) => store.registerModule(m));
}
