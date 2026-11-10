/**
 * 容器吸收推导层 —— 「谁并进了谁」的**唯一真相源**（容器型模块 = 吸收方）。
 *
 * 为什么要有这一层（2026-11-09）：
 *   吸收关系曾是两处手写表 —— `kernel/Dock.tsx` 的 `DOCK_MERGE`（19 条）与
 *   `apps/system/SystemApp.tsx` 的 `DEFAULT_TABS`（7 页）。两处各写一遍，
 *   实测已经分叉：`diary` 被 Dock 吸收进 knowledge 但 knowledge 的页签里没有它
 *   → 入口消失；`habits` 在 growth 的页签里、却不在 Dock 的吸收表 → 出现两个入口。
 *
 * 本层口径（唯一）：
 *   **吸收声明 = `manifest.tabs[]` + 协议字段 `manifest.absorbs[]`**（两者互补）：
 *     · `tabs[].entry` 是「**有页的吸收**」：目标模块 id = `tab.entry ?? tab.key`
 *       （`entry` 优先；缺省时 `key` 即模块 id，这样「无前端 App 的后端页」如 `auth`
 *       也能被正确归口）；
 *     · `absorbs[]` 是「**无页归口**」：纯 API / 无窗口 UI 的模块（如 countdown），
 *       或页由容器 App 自渲染的模块（如 pi-agent 的 agents）。
 *       后端 `core/manifest.py` 已校验「不得吸收自己、不得与 tabs[].entry 重复」。
 *     · 目标存在 且 不是容器自身 且 kind !== "container" ⇒ 被该容器吸收；
 *     · 一个模块只归一个容器（多容器争抢时按容器 id 升序先到先得，确定性）；
 *     · 目标模块不存在 ⇒ 静默跳过（清单滞后不崩，也不产生幽灵吸收）。
 *
 * 容器判定：`kind === "container"` ∪ 声明了非空 `absorbs` ∪ 在前端登记表里被登记过。
 *
 * ★ 前端登记表（`TAB_SUPPLEMENTS` / `ABSORB_SUPPLEMENTS`）不是第二真相源，
 *   它们是**已知的 manifest 缺口清单**，每一条都对应后端 manifest 该补的一行：
 *     · `knowledge` 缺「日记」页（diary 被吸收却无页 → 入口消失）；
 *     · `system` 缺 countdown 归口（纯 API、无窗口 UI，不出页）；
 *     · `pi-agent` 缺 tabs（PiAgentApp 内部自渲染「对话/编排」两页）。
 *   后端 manifest 补齐后，对应条目应删除 —— moduleGraph.test.ts 锁了这份清单的
 *   每条都能在当前仓库里找到真实模块，防止它悄悄腐烂成新的硬编码沼泽。
 *
 * 本文件是纯函数层：不读 DOM、不读全局 store、无副作用，可单测。
 */

/** 容器页签声明（与 manifest.tabs 同形，最小面）。 */
export interface DeclaredTab {
  key: string;
  label: string;
  /** 指向的另一模块 id；缺省 = 无独立窗口 App 的后端页（薄壳语义）。 */
  entry?: string;
}

/** 推导只依赖模块清单的最小字段面（ModuleManifest 是其超集）。 */
export interface GraphModuleLike {
  id: string;
  name?: string;
  kind?: string;
  /** 容器页签：每项 entry 隐含「该模块被本容器吸收」。 */
  tabs?: readonly DeclaredTab[];
  /**
   * 被本容器吸收、**不占页签**的模块 id（协议字段，见 contracts/plugin.schema.json）。
   * 与 `tabs[].entry` 互补：entry 是「有页的吸收」，absorbs 是「无页归口」。
   * 后端 `core/manifest.py` 已校验「不得吸收自己 / 不得与 tabs[].entry 重复」。
   */
  absorbs?: readonly string[];
}

/** 可接受的输入形态：数组 / 可迭代 / `Record<id, Manifest>` / store 的 `Record<id, {manifest}>`。 */
export type ModuleInput =
  | Iterable<GraphModuleLike | { manifest?: GraphModuleLike } | null | undefined>
  | Record<string, GraphModuleLike | { manifest?: GraphModuleLike } | null | undefined>
  | null
  | undefined;

/** 推导结果（只读视图 + 查询方法，行为等价于对同一份输入重复推导）。 */
export interface ModuleGraph {
  /** 全部容器 id（升序）。 */
  readonly containers: readonly string[];
  /** 子模块 id → 容器 id。 */
  readonly absorbed: Readonly<Record<string, string>>;
  /** 容器 id → 其吸收的子模块 id（按 tabs 顺序，已去重）。 */
  readonly byContainer: Readonly<Record<string, readonly string[]>>;
  /** 容器 id → 生效页签（manifest.tabs + 前端补页）。 */
  readonly tabs: Readonly<Record<string, readonly DeclaredTab[]>>;
  containerOf(moduleId: string): string | undefined;
  absorbedBy(containerId: string): readonly string[];
  isAbsorbed(moduleId: string): boolean;
  tabsOf(containerId: string): readonly DeclaredTab[];
}

/**
 * ★ 前端补页登记（manifest 缺口：容器有吸收声明但页签里没有该页）。
 * 一条 = 后端 manifest 该补的一行 tabs 项。补齐后删除。
 */
const TAB_SUPPLEMENTS: Readonly<Record<string, readonly DeclaredTab[]>> = {
  // diary 早已被归口进知识库（原 DOCK_MERGE: diary → knowledge），但
  // services/api/modules/knowledge/manifest.json 的 tabs 只有 review/notes/docs
  // → 菜单里没有日记入口。补一页，让「吸收」与「可见」一致。
  knowledge: [{ key: "diary", label: "日记", entry: "diary" }],
};

/**
 * ★ 前端归口登记（manifest 缺 `absorbs` 时的兜底）。
 * 语义与协议字段 `absorbs` 完全一致（吸收但**不占页签**）：优先读 manifest.absorbs，
 * 这里只补 manifest 尚未声明的缺口。后端补 absorbs 后应删除对应条目。
 */
const ABSORB_SUPPLEMENTS: Readonly<Record<string, readonly string[]>> = {
  // countdown：kind=third-party 且 entry=""（纯 API 插件，无窗口 UI）。
  // 单列只会点出一个薄壳页，故归口到系统窗（与 auth 同族的「后端服务」语义）。
  system: ["countdown"],
  // ai-chat：entry="" 已由 pi-agent 的「对话」页接管；agents（AI 编排）是
  // pi-agent 窗口的第 2 页。pi-agent 的 manifest 尚未声明 tabs（前端自渲染），
  // 故在此登记归口 —— 后端补 tabs/absorbs 后可删。
  "pi-agent": ["agents", "ai-chat"],
};

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

/** 把一个输入项归一成模块清单（兼容 store 的 `{ manifest }` 包装）。 */
function normalizeOne(v: unknown): GraphModuleLike | null {
  if (!isRecord(v)) return null;
  const wrapped = v.manifest;
  const cand: unknown = isRecord(wrapped) ? wrapped : v;
  if (!isRecord(cand) || typeof cand.id !== "string" || cand.id === "") return null;
  return cand as unknown as GraphModuleLike;
}

function toEntries(modules: ModuleInput): unknown[] {
  if (!modules) return [];
  if (Array.isArray(modules)) return modules;
  if (typeof (modules as Iterable<unknown>)[Symbol.iterator] === "function") {
    return [...(modules as Iterable<unknown>)];
  }
  if (isRecord(modules)) return Object.values(modules);
  return [];
}

function normalizeModules(modules: ModuleInput): Map<string, GraphModuleLike> {
  const byId = new Map<string, GraphModuleLike>();
  for (const raw of toEntries(modules)) {
    const m = normalizeOne(raw);
    if (m && !byId.has(m.id)) byId.set(m.id, m);
  }
  return byId;
}

/** 容器生效页签 = manifest.tabs + 前端补页（同 key 以 manifest 为准）。 */
export function effectiveTabsOf(
  containerId: string,
  manifestTabs?: readonly DeclaredTab[] | null,
): DeclaredTab[] {
  const out: DeclaredTab[] = [];
  const seen = new Set<string>();
  for (const tab of [...(manifestTabs ?? []), ...(TAB_SUPPLEMENTS[containerId] ?? [])]) {
    if (!tab || typeof tab.key !== "string" || tab.key === "") continue;
    if (seen.has(tab.key)) continue; // manifest 声明优先，补页不覆盖
    seen.add(tab.key);
    out.push(tab);
  }
  return out;
}

/**
 * 从模块清单推导吸收关系。
 *
 * @example
 *   const g = buildModuleGraph(useDesktopStore.getState().modules);
 *   g.containerOf("diary")   // → "knowledge"
 *   g.absorbedBy("growth")   // → ["habits", "persona", "health"]
 *   g.isAbsorbed("finance")  // → false（独立模块）
 */
export function buildModuleGraph(modules: ModuleInput): ModuleGraph {
  const byId = normalizeModules(modules);

  // 容器 = manifest 自报 kind:container ∪ 声明了 absorbs 的模块 ∪ 前端登记表里出现的 id
  //（登记表里的 id 必须真实存在，否则不算）。
  const containerIds = new Set<string>();
  for (const m of byId.values()) {
    if (m.kind === "container") containerIds.add(m.id);
    else if (m.absorbs && m.absorbs.length > 0) containerIds.add(m.id);
  }
  for (const id of [...Object.keys(TAB_SUPPLEMENTS), ...Object.keys(ABSORB_SUPPLEMENTS)]) {
    if (byId.has(id)) containerIds.add(id);
  }
  const containers = [...containerIds].sort();

  const tabs: Record<string, DeclaredTab[]> = {};
  const absorbed: Record<string, string> = {};
  const byContainer: Record<string, string[]> = {};

  for (const cid of containers) {
    const container = byId.get(cid);
    if (!container) continue;
    const declared = effectiveTabsOf(cid, container.tabs);
    tabs[cid] = declared;

    const kids: string[] = [];
    const claim = (childId: string | undefined): void => {
      if (!childId || childId === cid) return; // 不自吞
      const child = byId.get(childId);
      if (!child) return; // 未知模块：静默跳过（清单滞后不崩）
      if (child.kind === "container") return; // 容器不互相吞（防嵌套成环）
      if (absorbed[childId]) return; // 先到先得（容器 id 升序，确定性）
      absorbed[childId] = cid;
      if (!kids.includes(childId)) kids.push(childId);
    };

    for (const tab of declared) claim(tab.entry ?? tab.key);
    // 显式归口：协议字段 absorbs 优先，其次前端兜底登记（同一条 claim 去重）。
    for (const childId of container.absorbs ?? []) claim(childId);
    for (const childId of ABSORB_SUPPLEMENTS[cid] ?? []) claim(childId);

    byContainer[cid] = kids;
  }

  return {
    containers,
    absorbed,
    byContainer,
    tabs,
    containerOf: (moduleId: string) => absorbed[moduleId],
    absorbedBy: (containerId: string) => byContainer[containerId] ?? [],
    isAbsorbed: (moduleId: string) => Boolean(absorbed[moduleId]),
    tabsOf: (containerId: string) => tabs[containerId] ?? [],
  };
}

/* ───────────────────────── 一次性便捷函数（同源，不另存状态） ───────────────────────── */

/** 某模块被哪个容器吸收（无 → undefined）。 */
export function containerOf(modules: ModuleInput, moduleId: string): string | undefined {
  return buildModuleGraph(modules).containerOf(moduleId);
}

/** 某容器吸收了哪些模块（按页签顺序）。 */
export function absorbedBy(modules: ModuleInput, containerId: string): readonly string[] {
  return buildModuleGraph(modules).absorbedBy(containerId);
}

/** 某模块是否已被容器吸收。 */
export function isAbsorbed(modules: ModuleInput, moduleId: string): boolean {
  return buildModuleGraph(modules).isAbsorbed(moduleId);
}
