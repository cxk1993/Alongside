import { pluginHost } from "../ModuleRegistry";
import type {
  ModuleApiSpec,
  ModuleManifest,
  ModuleTabSpec,
  PermissionDecl,
  PluginLifecycleHooks,
} from "../types";
import type { PluginInfo, PluginManifest } from "./types";

/**
 * 前端插件注册表（T14 · 内核侧）。
 *
 * 职责：把后端 /api/v1/plugins 返回的插件清单同步进桌面 store，
 * 使"装了什么 / 启没启"对内核一视同仁——Dock/TopBar 直接读 store.modules，
 * 不认识任何具体插件。禁用即把 store 里该模块 enabled 置 false（Dock 自动置灰），
 * 启用即恢复。这与 T02 既有的 ModuleRegistry/store 接口完全对接，不改内核一行业务代码。
 *
 * ★ 2026-11-09 补漏：manifestToModule 曾只构造 9 个字段，把后端契约
 *   （contracts/plugin.schema.json，36 个顶层字段）其余 27 个全丢了 ——
 *   容器型模块拿不到 tabs/absorbs，能力图拿不到 provides/requires，调试时也看不到
 *   riskLevel/schedule/origin。现按三层处理：
 *     A 必须有（运行时要读）→ 显式规范化
 *     B 应保留（强类型）    → 显式规范化，类型齐全
 *     C 可透传（不解析）    → 原样挂在模块顶层，并整体存入 raw 快照
 */

const FALLBACK_WINDOW = { w: 640, h: 420 } as const;

/** A/B 组：映射时显式规范化的键；其余键按 C 组原样透传。 */
const NORMALIZED_KEYS = new Set<string>([
  // A 组：运行时要读
  "id",
  "name",
  "version",
  "kind",
  "icon",
  "description",
  "entry",
  "window",
  "api",
  "slots",
  "tabs",
  "absorbs",
  "provides",
  // B 组：应保留（强类型）
  "requires",
  "optionalDependencies",
  "permissions",
  "kernelApi",
  "minKernel",
  "defines",
  "emits",
  "consumes",
  "lifecycle",
  // 由本函数自己产出，不从 manifest 抄
  "raw",
]);

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function asString(v: unknown): string | undefined {
  return typeof v === "string" ? v : undefined;
}

/** 字符串数组：非数组 → undefined；数组里只收字符串项（脏数据不抛错）。 */
function asStringArray(v: unknown): string[] | undefined {
  if (!Array.isArray(v)) return undefined;
  return v.filter((x): x is string => typeof x === "string");
}

/** 容器页签：逐项校验 key/label，坏项丢弃（Tab 立面不因脏数据崩）。 */
function asTabs(v: unknown): ModuleTabSpec[] | undefined {
  if (!Array.isArray(v)) return undefined;
  const tabs: ModuleTabSpec[] = [];
  for (const item of v) {
    if (!isRecord(item)) continue;
    const key = asString(item.key);
    const label = asString(item.label);
    if (!key || !label) continue;
    const entry = asString(item.entry);
    tabs.push(entry ? { key, label, entry } : { key, label });
  }
  return tabs;
}

/** api：base 是「后端模块」判定的唯一依据；缺它就不算 API 面（其余键原样带）。 */
function asApi(v: unknown): ModuleApiSpec | undefined {
  if (!isRecord(v) || typeof v.base !== "string") return undefined;
  return { ...v } as unknown as ModuleApiSpec;
}

/** permissions：旧格式（string[]）或新格式（声明式对象）都原样保留。 */
function asPermissions(v: unknown): PermissionDecl | undefined {
  if (Array.isArray(v)) return v.filter((x): x is string => typeof x === "string");
  if (isRecord(v)) return { ...v } as unknown as PermissionDecl;
  return undefined;
}

function asLifecycle(v: unknown): PluginLifecycleHooks | undefined {
  return isRecord(v) ? ({ ...v } as unknown as PluginLifecycleHooks) : undefined;
}

export function manifestToModule(p: PluginInfo): ModuleManifest {
  const manifest = isRecord(p.manifest) ? (p.manifest as PluginManifest) : undefined;

  // C 组：未显式解析的协议字段原样带上（riskLevel/schedule/origin/author/x_* …）。
  // 显式字段在返回值里排在其后，manifest 里的同名脏值顶不掉规范化结果。
  const passthrough: Record<string, unknown> = {};
  if (manifest) {
    for (const [key, value] of Object.entries(manifest)) {
      if (!NORMALIZED_KEYS.has(key) && value !== undefined) passthrough[key] = value;
    }
  }

  return {
    // —— C 组：可透传（不解析）——
    ...passthrough,

    // —— A 组：必须有（运行时要读）——
    id: p.id,
    name: p.name,
    version: p.version,
    kind: p.kind,
    icon: p.icon ?? asString(manifest?.icon),
    description: p.description ?? asString(manifest?.description),
    // ISSUE-012 防御：manifest 缺失时给安全默认值（空 entry / 兜底窗口 / 空 slots），
    // 使模块仍可注册（Dock 可见）但无任何 UI 贡献，绝不抛错。
    entry: asString(manifest?.entry) ?? "",
    window: manifest?.window ?? { ...FALLBACK_WINDOW },
    // ★ 根因修复（2026-09-25 · 令 · 主人真机报「plugins 模块清单缺少 entry 字段」）：
    //   必须透传 api —— WindowFrame 用 `manifest.api.base` 判定「后端模块（合法无 UI）」
    //   与「真缺失」。此前三容器（system/knowledge/growth）走 modules.json 静态注册带着
    //   api 才显示正常；而后端同步的 core/builtin 插件（plugins/summary/query/export/auth）
    //   丢了 api → 明明有 API 却被判「该模块尚未接入 · 清单缺少 entry 字段」。
    api: asApi(manifest?.api),
    slots: asStringArray(manifest?.slots) ?? [],
    // 容器型模块：页签 + 被吸收模块（SystemApp 直接读 manifest.tabs）。
    tabs: asTabs(manifest?.tabs),
    absorbs: asStringArray(manifest?.absorbs),
    provides: asStringArray(manifest?.provides),

    // —— B 组：应保留（强类型，供调试 / 未来消费）——
    requires: asStringArray(manifest?.requires),
    optionalDependencies: asStringArray(manifest?.optionalDependencies),
    permissions: asPermissions(manifest?.permissions),
    kernelApi: asString(manifest?.kernelApi),
    minKernel: asString(manifest?.minKernel),
    defines: asStringArray(manifest?.defines),
    emits: asStringArray(manifest?.emits),
    consumes: asStringArray(manifest?.consumes),
    lifecycle: asLifecycle(manifest?.lifecycle),

    // 原始快照：未被解析字段的唯一真相（缺 manifest 时给空对象，不抛错）。
    raw: manifest ? { ...manifest } : {},
  };
}

/**
 * 把后端插件清单同步进桌面模块注册表，并按启用态开/关。
 *
 * ISSUE-012 防御：后端 `list_plugins()` 曾漏掉 `manifest` 字段（已由令55 修），
 * 契约测试断言前，前端先做到「manifest 缺失不崩」——
 * 无 manifest 项跳过（记 warning，不进 store），其余项照常注册。
 */
export function syncPluginsToStore(plugins: PluginInfo[]): void {
  for (const p of plugins) {
    if (!p.manifest) {
      // ★ 数据异常保护：缺 manifest 的项不能进 store（Dock 会渲染出无入口的幽灵模块），
      // 也绝不能让它把整个同步循环打断（历史上它会让贡献注册整体挂不上）。
      console.warn(
        `[plugins] 插件「${p.id}」缺少 manifest，已跳过同步（后端契约应保证该字段存在）`,
      );
      continue;
    }
    pluginHost.register(manifestToModule(p));
    if (p.enabled) {
      pluginHost.enable(p.id);
    } else {
      pluginHost.disable(p.id);
    }
  }
}
