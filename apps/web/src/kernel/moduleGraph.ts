/**
 * 容器吸收推导层 —— 「谁并进了谁」的**唯一真相源**（容器型模块 = 吸收方）。
 *
 * 为什么要有这一层（2026-11-09）：
 *   吸收关系曾是两处手写表 —— `kernel/Dock.tsx` 的 `DOCK_MERGE`（19 条）与
 *   `apps/system/SystemApp.tsx` 的 `DEFAULT_TABS`（7 页）。两处各写一遍，
 *   实测已经分叉：`diary` 被 Dock 吸收进 knowledge 但 knowledge 的页签里没有它
 *   → 入口消失；`habits` 在 growth 的页签里、却不在 Dock 的吸收表 → 出现两个入口。
 *
 * 本层口径（唯一，2026-11-10 第三轮定稿）：
 *   **吸收声明 = `manifest.absorbs[]` ∪ `manifest.tabs[]`**，两者互补（后端
 *   `core/manifest.py` 明令禁止同一模块同时出现在 `absorbs` 与 `tabs[].entry`：
 *   "entry 已隐含吸收，不必重复写"）：
 *     · `tabs[].entry` 是「**有页的吸收**」：目标模块 id = `tab.entry ?? tab.key`
 *       （`entry` 优先；缺省时 `key` 即模块 id，这样「无前端 App 的后端页」如 `auth`
 *       也能被正确归口）；
 *     · `absorbs[]` 是「**无页归口**」：纯 API / 无窗口 UI 的模块（如 countdown），
 *       或页由容器 App 自渲染的模块（如 pi-agent 的 agents）。
 *
 *   优先级（跨容器争抢同一模块时，**absorbs 优先于 tabs 反推**）：
 *     ① `absorbs` 显式声明（协议字段，权威）
 *     ② `tabs[].entry ?? tab.key`（协议语义内的隐式吸收）
 *   同级内按容器 id 升序先到先得（确定性）。
 *
 *   目标存在 且 不是容器自身 且 kind !== "container" ⇒ 被该容器吸收；
 *   目标模块不存在 ⇒ 不产生幽灵吸收（清单滞后不崩）。
 *
 * ★ 冲突不静默（`conflicts`）：同一模块被多个来源声明时，`absorbed` 仍给出
 *   确定性的单一落点（UI 必须能画出唯一 Dock），但**每一条竞争都记入 conflicts**，
 *   由调用方决定展示/告警。绝不"悄悄择一然后当没发生"。
 *
 * 容器判定：`kind === "container"` ∪ 声明了非空 `absorbs`。
 *
 * ★ 曾经有一张前端补表（`TAB_SUPPLEMENTS` / `ABSORB_SUPPLEMENTS`）：manifest 尚未补齐
 *   时用它兜住三处缺口 —— knowledge 缺「日记」页、system 缺 countdown 归口、pi-agent
 *   缺 agents/ai-chat 归口。2026-11-10 三处都已写进后端 manifest，补表**连同机制一起
 *   删掉**：缺口清单填完就该消失，留一张空表等于给下一个人留「还能再写一次」的后路。
 *   ⇒ 现在页签与吸收都只有 manifest 一个来源，缺了就是缺了（测试会红，而不是被悄悄补上）。
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
  /** 前端入口说明符（`@/apps/<dir>` 等）；空 = 无独立窗口 App。 */
  entry?: string;
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

/** 冲突类别（每一条都对应一种「声明之间互相打架」的真实形态）。 */
export type GraphConflictKind =
  /** 容器在自己的 absorbs 里写了自己（后端已禁止，前端仍要报告而不是假装没看见）。 */
  | "self-absorb"
  /** 同一模块被多个容器的 absorbs 同时声明（谁赢见 resolvedTo）。 */
  | "multi-absorbs"
  /** 模块被某容器 absorbs 声明，同时又出现在某容器的 tabs 里（含同一容器重复声明）。 */
  | "absorbed-and-tabbed"
  /** absorbs 声明了清单里不存在的模块（清单滞后或拼写错误）。 */
  | "unknown-absorb-target"
  /** absorbs 指向另一个容器（容器不互吞，已忽略）。 */
  | "container-absorb";

/** 一条冲突记录。`absorbed` 仍给出唯一落点，这里只负责「把竞争关系说出来」。 */
export interface GraphConflict {
  kind: GraphConflictKind;
  /** 争议的模块 id。 */
  moduleId: string;
  /** 涉事容器 id（升序，已去重）。 */
  containers: readonly string[];
  /** 最终吸收落点（`absorbed[moduleId]`；无 = undefined）。 */
  resolvedTo: string | undefined;
  /** 人读说明（含已采取的确定性处理，避免调用方猜）。 */
  message: string;
}

/** 推导结果（只读视图 + 查询方法，行为等价于对同一份输入重复推导）。 */
export interface ModuleGraph {
  /** 全部容器 id（升序）。 */
  readonly containers: readonly string[];
  /** 子模块 id → 容器 id（唯一落点）。 */
  readonly absorbed: Readonly<Record<string, string>>;
  /** 容器 id → 其吸收的子模块 id（先页签顺序，后无页归口，已去重）。 */
  readonly byContainer: Readonly<Record<string, readonly string[]>>;
  /** 容器 id → 页签（`manifest.tabs` 归一化后的结果，无任何前端补齐）。 */
  readonly tabs: Readonly<Record<string, readonly DeclaredTab[]>>;
  /** 声明之间的竞争关系（空数组 = 全部一致）。 */
  readonly conflicts: readonly GraphConflict[];
  containerOf(moduleId: string): string | undefined;
  absorbedBy(containerId: string): readonly string[];
  isAbsorbed(moduleId: string): boolean;
  tabsOf(containerId: string): readonly DeclaredTab[];
}

/** 一个页签最终该渲染什么。 */
export type TabPageMode =
  /** 加载模块声明的 App（entry 可解析）。 */
  | "app"
  /** 模块存在但无前端入口 → 「已接入（后端服务）」薄壳。 */
  | "backend"
  /** 页签指向的模块不在清单里 → 「模块未注册」薄壳（绝不白屏）。 */
  | "unknown-module"
  /** 模块声明了入口但仓库里没有对应 App 文件 → 「入口缺失」薄壳（绝不白屏）。 */
  | "missing-app";

/** 页签解析结果（纯数据，containerPages 据此决定渲染什么）。 */
export interface TabPage {
  /** 页签目标模块 id（`tab.entry ?? tab.key`）。 */
  moduleId: string;
  mode: TabPageMode;
  /** mode === "app" 时的前端入口说明符。 */
  appEntry?: string;
  /** 薄壳文案用的补充说明。 */
  reason?: string;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

function asNonEmptyString(v: unknown): string | undefined {
  if (typeof v !== "string") return undefined;
  const s = v.trim();
  return s === "" ? undefined : s;
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

/** 归一化 absorbs：只留非空字符串、去重、保序（脏数据不参与推导）。 */
function normalizedAbsorbs(m: GraphModuleLike): string[] {
  const raw: unknown = m.absorbs;
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const v of raw) {
    const id = asNonEmptyString(v);
    if (id && !out.includes(id)) out.push(id);
  }
  return out;
}

/** 归一化一个页签：key 必须有；label 缺失回退 key；entry 空串归一为缺省。 */
function normalizeTab(raw: unknown): DeclaredTab | null {
  if (!isRecord(raw)) return null;
  const key = asNonEmptyString(raw.key);
  if (!key) return null;
  const tab: DeclaredTab = {
    key,
    label: asNonEmptyString(raw.label) ?? key,
  };
  const entry = asNonEmptyString(raw.entry);
  if (entry) tab.entry = entry;
  return tab;
}

/** 页签目标模块 id：entry 优先，缺省回退 key（两处消费共用，避免各写一遍又分叉）。 */
export function tabTargetId(tab: DeclaredTab): string {
  return asNonEmptyString(tab.entry) ?? tab.key;
}

/** 容器页签 = `manifest.tabs` 归一化（畸形项丢弃，同 key 只认第一个）。 */
export function normalizeTabs(manifestTabs?: readonly DeclaredTab[] | null): DeclaredTab[] {
  const out: DeclaredTab[] = [];
  const seen = new Set<string>();
  for (const raw of manifestTabs ?? []) {
    const tab = normalizeTab(raw);
    if (!tab) continue;
    if (seen.has(tab.key)) continue; // 同 key 只认第一个（manifest 内去重）
    seen.add(tab.key);
    out.push(tab);
  }
  return out;
}

/**
 * 解析一个页签该渲染什么（纯函数 · containerPages 的唯一判定入口）。
 *
 * ★ 关键区分（协议语义，不能混）：
 *   · `tab.entry` **缺省** = 「本页没有独立前端 App」——这是显式声明，直接落
 *     「已接入（后端服务）」薄壳（如 system 的 auth 页）；**不**去查 `key` 是否
 *     恰好是个未注册模块，否则会把「设计如此的后端页」误报成清单缺漏。
 *     （吸收推导仍用 `entry ?? key` 把 auth 归口 system —— 归口与渲染是两件事。）
 *   · ⚠️ **已知数据陷阱**：`schedule` 的 `{ key: "study", entry: "todo" }` 会解析成
 *     `todo`（第二个待办页）。真实学业页是 `apps/study/StudyApp.tsx`（非模块、无
 *     `index.tsx`）⇒ 协议缺「容器内联页」态。在补上该态之前，`schedule` 不迁
 *     `useContainerPages`（见 moduleGraph.test.ts 「迁移前置条件」）。
 *   · `tab.entry` **写了**却指向不存在的模块 → `unknown-module`（清单滞后/笔误，
 *     薄壳页说明原因，不白屏不报错）。
 *   · 模块存在但自身 `entry` 为空（纯 API）→ `backend`。
 *   · 模块声明了 entry 但 App 文件缺失 → 由调用方用 `canResolveLoader` 判为
 *     `missing-app`（本函数不 import 注册表，保持纯函数可单测）。
 */
export function resolveTabPage(tab: DeclaredTab, modules: ModuleInput): TabPage {
  const explicitEntry = asNonEmptyString(tab.entry);

  // ① 缺省 entry：明确的「后端服务页」语义，不猜 key 对应的模块
  if (!explicitEntry) {
    return {
      moduleId: tab.key,
      mode: "backend",
      reason: `页签「${tab.key}」未声明前端入口（后端服务页）`,
    };
  }

  // ② 显式 entry：目标模块必须存在
  const target = normalizeModules(modules).get(explicitEntry);
  if (!target) {
    return {
      moduleId: explicitEntry,
      mode: "unknown-module",
      reason: `页签「${tab.key}」指向模块「${explicitEntry}」，但注册清单里没有它`,
    };
  }

  const appEntry = asNonEmptyString(target.entry);
  if (!appEntry) {
    return {
      moduleId: explicitEntry,
      mode: "backend",
      reason: `模块「${explicitEntry}」没有前端入口（纯 API 模块）`,
    };
  }
  return { moduleId: explicitEntry, mode: "app", appEntry };
}

/** 吸收声明来源，数值越小越权威。 */
const SOURCE_RANK = { absorbs: 0, tabs: 1 } as const;
type ClaimSource = keyof typeof SOURCE_RANK;

interface Claim {
  childId: string;
  containerId: string;
  source: ClaimSource;
  /** 容器内声明顺序（页序 / absorbs 数组序）。 */
  seq: number;
}

function uniqSorted(ids: readonly string[]): string[] {
  return [...new Set(ids)].sort();
}

/**
 * 从模块清单推导吸收关系。
 *
 * @example
 *   const g = buildModuleGraph(useDesktopStore.getState().modules);
 *   g.containerOf("diary")   // → "knowledge"
 *   g.absorbedBy("growth")   // → ["habits", "persona", "health"]
 *   g.isAbsorbed("finance")  // → false（独立模块）
 *   g.conflicts              // → []（声明一致）或竞争关系清单
 */
export function buildModuleGraph(modules: ModuleInput): ModuleGraph {
  const byId = normalizeModules(modules);

  // 容器 = manifest 自报 kind:container ∪ 声明了非空 absorbs 的模块
  //（补表已删：没有第二来源能给容器身份兜底，缺了就是缺了）。
  const containerIds = new Set<string>();
  for (const m of byId.values()) {
    if (m.kind === "container") containerIds.add(m.id);
    else if (normalizedAbsorbs(m).length > 0) containerIds.add(m.id);
  }
  const containers = [...containerIds].sort();

  const tabs: Record<string, DeclaredTab[]> = {};
  const absorbsOf: Record<string, string[]> = {};
  /** 参与推导的全部声明。 */
  const claims: Claim[] = [];
  /** 仅 absorbs 声明（冲突检测用：它是对外声明）。 */
  const absorbsClaims: Claim[] = [];
  /** 仅 tabs 声明（含声明了 absorbs 的容器的页签，用于检测重复声明）。 */
  const tabClaims: Claim[] = [];

  for (const cid of containers) {
    const container = byId.get(cid);
    if (!container) continue;

    const declared = normalizeTabs(container.tabs);
    tabs[cid] = declared;
    declared.forEach((tab, i) => {
      const childId = tabTargetId(tab);
      if (!childId) return;
      const claim: Claim = { childId, containerId: cid, source: "tabs", seq: i };
      claims.push(claim);
      tabClaims.push(claim);
    });

    const absorbs = normalizedAbsorbs(container);
    absorbsOf[cid] = absorbs;
    absorbs.forEach((childId, i) => {
      const claim: Claim = { childId, containerId: cid, source: "absorbs", seq: i };
      claims.push(claim);
      absorbsClaims.push(claim);
    });
  }

  // 优先级：absorbs > tabs；同级按容器 id 升序；容器内按声明顺序。
  const ordered = [...claims].sort((a, b) => {
    const rank = SOURCE_RANK[a.source] - SOURCE_RANK[b.source];
    if (rank !== 0) return rank;
    if (a.containerId !== b.containerId) return a.containerId < b.containerId ? -1 : 1;
    return a.seq - b.seq;
  });

  const winner = new Map<string, Claim>();
  const absorbed: Record<string, string> = {};
  for (const claim of ordered) {
    if (winner.has(claim.childId)) continue;
    if (claim.childId === claim.containerId) continue; // 不自吞（冲突里报告）
    const child = byId.get(claim.childId);
    if (!child) continue; // 未知模块：不产生幽灵吸收（冲突里报告）
    if (child.kind === "container") continue; // 容器不互吞（防嵌套成环）
    winner.set(claim.childId, claim);
    absorbed[claim.childId] = claim.containerId;
  }

  // byContainer：容器自己的顺序（页序 → absorbs → 补表），只留自己抢到的。
  const claimsByContainer = new Map<string, Claim[]>();
  const push = (cid: string, claim: Claim): void => {
    const list = claimsByContainer.get(cid);
    if (list) list.push(claim);
    else claimsByContainer.set(cid, [claim]);
  };
  for (const c of tabClaims) push(c.containerId, c);
  for (const c of absorbsClaims) push(c.containerId, c);

  const byContainer: Record<string, string[]> = {};
  for (const cid of containers) {
    const kids: string[] = [];
    for (const claim of claimsByContainer.get(cid) ?? []) {
      if (winner.get(claim.childId)?.containerId !== cid) continue;
      if (!kids.includes(claim.childId)) kids.push(claim.childId);
    }
    byContainer[cid] = kids;
  }

  const conflicts = detectConflicts(byId, absorbed, absorbsOf, tabClaims);

  return {
    containers,
    absorbed,
    byContainer,
    tabs,
    conflicts,
    containerOf: (moduleId: string) => absorbed[moduleId],
    absorbedBy: (containerId: string) => byContainer[containerId] ?? [],
    isAbsorbed: (moduleId: string) => Boolean(absorbed[moduleId]),
    tabsOf: (containerId: string) => tabs[containerId] ?? [],
  };
}

/** 冲突检测：只报告，不改写吸收结果（结果已按优先级确定）。 */
function detectConflicts(
  byId: Map<string, GraphModuleLike>,
  absorbed: Readonly<Record<string, string>>,
  absorbsOf: Readonly<Record<string, readonly string[]>>,
  tabClaims: readonly Claim[],
): GraphConflict[] {
  const out: GraphConflict[] = [];
  const tabsByChild = new Map<string, string[]>();
  for (const claim of tabClaims) {
    const list = tabsByChild.get(claim.childId);
    if (list) list.push(claim.containerId);
    else tabsByChild.set(claim.childId, [claim.containerId]);
  }

  // 以「被声明的模块」为主键聚合：一个模块只产生一组冲突记录（不是每个声明方各报一遍）。
  const absorbsByTarget = new Map<string, string[]>();
  for (const [containerId, targets] of Object.entries(absorbsOf)) {
    for (const targetId of targets) {
      const list = absorbsByTarget.get(targetId);
      if (list) list.push(containerId);
      else absorbsByTarget.set(targetId, [containerId]);
    }
  }

  for (const [targetId, rawDeclarers] of absorbsByTarget) {
    const declaredBy = uniqSorted(rawDeclarers);
    const tabbedBy = uniqSorted(tabsByChild.get(targetId) ?? []);
    const resolvedTo = absorbed[targetId];
    const others = declaredBy.filter((cid) => cid !== targetId);

    if (declaredBy.includes(targetId)) {
      out.push({
        kind: "self-absorb",
        moduleId: targetId,
        containers: [targetId],
        resolvedTo,
        message: `模块「${targetId}」在 absorbs 里声明了自己 —— 已忽略该条。`,
      });
    }

    const target = byId.get(targetId);
    if (!target) {
      out.push({
        kind: "unknown-absorb-target",
        moduleId: targetId,
        containers: declaredBy,
        resolvedTo,
        message: `容器「${declaredBy.join(" / ")}」的 absorbs 声明了清单里不存在的模块「${targetId}」—— 已跳过（清单滞后或拼写错误）。`,
      });
    } else if (target.kind === "container" && !declaredBy.includes(targetId)) {
      out.push({
        kind: "container-absorb",
        moduleId: targetId,
        containers: declaredBy,
        resolvedTo,
        message: `容器「${declaredBy.join(" / ")}」的 absorbs 指向另一个容器「${targetId}」—— 容器不互吞，已忽略。`,
      });
    }

    if (others.length > 1) {
      out.push({
        kind: "multi-absorbs",
        moduleId: targetId,
        containers: declaredBy,
        resolvedTo,
        message: `模块「${targetId}」被多个容器同时写进 absorbs（${others.join(" / ")}）—— 按容器 id 升序归「${resolvedTo ?? "无"}」。`,
      });
    }

    if (tabbedBy.length > 0) {
      out.push({
        kind: "absorbed-and-tabbed",
        moduleId: targetId,
        containers: uniqSorted([...declaredBy, ...tabbedBy]),
        resolvedTo,
        message: `模块「${targetId}」被「${declaredBy.join(" / ")}」的 absorbs 吸收，同时又出现在「${tabbedBy.join(" / ")}」的 tabs 里 —— 未静默择一：落点按优先级（absorbs > tabs）为「${resolvedTo ?? "无"}」。`,
      });
    }
  }

  // 稳定排序：kind → moduleId，保证同一输入每次输出一致（测试可锁）。
  return out.sort(
    (a, b) => (a.kind < b.kind ? -1 : a.kind > b.kind ? 1 : 0) || (a.moduleId < b.moduleId ? -1 : 1),
  );
}

/* ───────────────────────── 一次性便捷函数（同源，不另存状态） ───────────────────────── */

/** 某模块被哪个容器吸收（无 → undefined）。 */
export function containerOf(modules: ModuleInput, moduleId: string): string | undefined {
  return buildModuleGraph(modules).containerOf(moduleId);
}

/** 某容器吸收了哪些模块（先页签顺序，后无页归口）。 */
export function absorbedBy(modules: ModuleInput, containerId: string): readonly string[] {
  return buildModuleGraph(modules).absorbedBy(containerId);
}

/** 某模块是否已被容器吸收。 */
export function isAbsorbed(modules: ModuleInput, moduleId: string): boolean {
  return buildModuleGraph(modules).isAbsorbed(moduleId);
}
