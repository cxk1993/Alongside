/**
 * 运行时远程入口加载器（r5 · 第三方插件 UI 热插拔的前端一半）。
 *
 * 背景：内置插件的 entry 是 `@/apps/<dir>`，由 ModuleRegistry 的构建期 glob 解析；
 * 第三方插件的代码不在内核树里，后端在清单里把它的 entry 改写成**资产 URL**
 * （根相对 `/plugin-assets/<id>/index.js`）。本模块负责把这类 URL 形状的 entry
 * 在运行时真正加载成 `{ default: PluginModule }`。
 *
 * ★ 为什么是 fetch→Blob→import，而不是直接 `import(url)`：
 *   资产端点需登录鉴权（Authorization: Bearer），浏览器原生 `import(url)` 携带不了
 *   自定义请求头，直接 import 会 401。故先用与 api client 同一来源的 token fetch 到
 *   模块源码，再经 Blob URL 交给原生 ESM 执行。Blob 模块与宿主同 realm，其裸说明符
 *   （`import React from "react"`）仍由文档的 import map 解析到共享 kernel-runtime，
 *   React 单例不受影响（见 vite.config.ts 的 import map 注入）。
 *
 * ★ 内核洁癖：本文件只认 **URL 形状**，不认识任何插件 id / 业务词 / 具体端点前缀。
 *   entry 是不是远程、加载哪个 URL，完全由后端清单决定，内核不做任何映射表。
 */
import { getAuthToken } from "@/shared/api/client";
import type { PluginModule } from "../types";

/**
 * URL 形状判定：绝对（http/https）、协议相对（//host/…）、根相对（/plugin-assets/…）都认。
 * ★ 根相对必须认 —— 后端改写出来的 entry 正是根相对（勘察 REMOTE_RE 只认前两种，是漏的）。
 * 不匹配 `@/apps/…`、`@apps/…`、`@mocks/…`、`../apps/…` 等内置说明符（它们不以 / 或协议开头）；
 * ★ 另排除根相对的 `/src/apps/…`（Vite 源码图的构建期内置命名空间）——
 *   这样即使某个内置 entry 的 glob 键缺失，也仍走「入口缺失」原语义，不会漂成远程乐观。
 */
export const REMOTE_URL_RE = /^(?:https?:\/\/|\/\/|\/(?!src\/apps\/))/;

export function isRemoteEntry(entry: string): boolean {
  return REMOTE_URL_RE.test(entry);
}

/** 把 entry 规整为绝对 URL（根相对/协议相对按当前 origin 补全）。 */
export function resolveRemoteUrl(entry: string): string {
  return new URL(entry, window.location.origin).href;
}

/** 带鉴权取回模块源码；失败时抛出**含 HTTP 状态**的诚实错误（供错误边界展示，G7 的便宜部分）。 */
async function fetchPluginSource(url: string): Promise<string> {
  const token = getAuthToken();
  const headers: Record<string, string> = { Accept: "*/*" };
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(url, { headers });
  } catch {
    throw new Error(`插件 UI 资产请求失败（网络不可用）：${url}`);
  }
  if (!res.ok) {
    // 404 = 插件未启用/未安装/无 UI；401 = 未登录。都把状态说出来，不白屏不静默。
    throw new Error(`插件 UI 资产不可用（HTTP ${res.status}）：${url}`);
  }
  return res.text();
}

/**
 * 原生 ESM 执行缝。默认用带 @vite-ignore 的原生 import（Vite 不静态分析、原样保留）。
 * 测试可注入替身，避免依赖真实 Blob 模块执行。
 */
type Importer = (url: string) => Promise<unknown>;
const nativeImporter: Importer = (url) => import(/* @vite-ignore */ url);
let importModule: Importer = nativeImporter;

/** 测试 seam：注入/还原模块执行器（传 null 还原为原生 import）。 */
export function __setRemoteImporterForTest(fn: Importer | null): void {
  importModule = fn ?? nativeImporter;
}

/** 源码 → Blob URL → 原生 import；用完即 revoke，避免对象 URL 泄漏。 */
async function importPluginSource(code: string): Promise<unknown> {
  const blob = new Blob([code], { type: "text/javascript" });
  const blobUrl = URL.createObjectURL(blob);
  try {
    return await importModule(blobUrl);
  } finally {
    URL.revokeObjectURL(blobUrl);
  }
}

/**
 * 加载一个远程 entry，返回与内置入口同形的 `{ default: PluginModule }`。
 * 不做长期缓存：热插拔语义下每次开窗取最新资产（后端 Cache-Control: no-cache），
 * 「装上/换掉立刻生效」优先于省一次请求。Component 形状校验由消费方
 * （WindowFrame / containerPages）沿用既有逻辑负责。
 */
export async function loadRemoteModule(entry: string): Promise<{ default: PluginModule }> {
  const url = resolveRemoteUrl(entry);
  const code = await fetchPluginSource(url);
  const mod = await importPluginSource(code);
  if (
    !mod ||
    typeof mod !== "object" ||
    !("default" in mod) ||
    (mod as { default?: unknown }).default == null
  ) {
    throw new Error(`插件 UI 模块缺少 default 导出：${url}`);
  }
  return mod as { default: PluginModule };
}