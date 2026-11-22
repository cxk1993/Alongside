import type { ModuleReg } from "../../kernel/store";

/**
 * ★ P0-c（2026-11-22）：能力层入口的**纯函数层**。
 *
 * 背景（主人 10:06 原话）：「前段很糟糕，像是半改没改，既不彻底，也不方便」。
 *   「不方便」这半边是**入口丢了**——壳收掉之后（Dock 退场、左栏模块导航收成一块），
 *   21 个模块只剩 Ctrl+K 一条路：键盘路径在，一眼看得见的入口没了。
 *   新用户不会先按 Ctrl+K；他要的是「这机器能干什么」直接摊在眼前。
 *
 * 纪律（本文件只做三件事，绝不多做）：
 *   1. **零新契约**：只读 `store.modules`（内核已注册的清单），不发明端点、不加 manifest 字段。
 *   2. **只列已启用**：停用模块列出来，点一下没反应，比没有这条更糟。
 *   3. **provides 只读展示**：manifest.provides 是「能力名」（如 `calendar.event.read`），
 *      内核没有把它映射成可调用端点。所以这里只显示「它提供几项能力 / 哪几项」，
 *      **不生成「执行」按钮**——那会是本项目最忌讳的假按钮。
 *
 * 为什么不去 import `kernel/commands.ts`（那里已经有模块条目）：那是内核侧、且归壳手术那边改。
 * 插件侧 import 内核纯函数会把「改壳」和「改 collab」耦合在一起，两边测试互相炸。
 * 复制这点推导逻辑（10 行）换边界干净，划算。
 */

export interface CapabilityEntry {
  /** 模块 id（= manifest.id = openWindow 的入参）。 */
  id: string;
  /** 人读的名字；manifest 没写 name 时退回 id（不编造）。 */
  name: string;
  /** 该模块声明提供的能力名（只读）。 */
  provides: string[];
  /** 检索词：id / name / 能力名 / 中文「打开」。 */
  keywords: string[];
  /** 占一个常驻位：当前只有「收编了多个子入口」的分组父项会置 true。 */
  quick?: boolean;
}

/** collab 自己：在主墙上点「collab」会在自己身上再开一扇窗，纯噪声。 */
const SELF_ID = "collab";

/**
 * ★ 2026-11-22（主人 16:56「散在底部栏很难看」）：学习类插件收进「学习软件」一个入口。
 *
 * 能力条不是模块清单的镜像——它是「人想干什么」的入口。7 个学习插件各占一格，
 * 用户看到的是七个陌生名字；收进 study-hub 后，点一次「学习软件」再选，才是正常的信息层级。
 *
 * 降级规则：父模块（study-hub）不存在或停用时，子模块照常单独出现——
 * 不能因为分组把入口弄丢（宁散勿丢）。
 */
const GROUPED_INTO: Record<string, string> = {
  mooc: "study-hub",
  ucampus: "study-hub",
  welearn: "study-hub",
  yuketang: "study-hub",
  zhidao: "study-hub",
  "sdu-feeds": "study-hub",
  "pi-agent": "study-hub",
};

/**
 * 从注册表推导能力条目。
 * 顺序 = 注册顺序（后端清单的编排顺序，dashboard 打头），**不重排**：
 * 按 id 字母序会把「日历 / 待办」这类天天用的排到后面，且顺序随新增插件漂移。
 */
export function buildCapabilities(modules: Record<string, ModuleReg>): CapabilityEntry[] {
  const out: CapabilityEntry[] = [];
  const parentAvailable = (id: string) => {
    const parent = modules[id];
    return Boolean(parent && parent.enabled);
  };
  for (const reg of Object.values(modules)) {
    if (!reg || !reg.enabled) continue;
    const m = reg.manifest;
    if (!m || !m.id) continue;
    if (m.id === SELF_ID) continue;
    // 学习插件收进 study-hub（父不存在/停用时照常单列，入口不丢）。
    const parent = GROUPED_INTO[m.id];
    if (parent && parentAvailable(parent)) continue;
    const provides = Array.isArray(m.provides)
      ? m.provides.filter((p): p is string => typeof p === "string" && p.length > 0)
      : [];
    const name = typeof m.name === "string" && m.name ? m.name : m.id;
    out.push({
      id: m.id,
      name,
      provides,
      keywords: [m.id, name, ...provides, "打开", "模块"],
    });
  }
  // 分组后，把子插件的名字并进父条目的检索词：搜「雨课堂」也能找到「学习软件」。
  const byId = new Map(out.map((e) => [e.id, e]));
  for (const [childId, parentId] of Object.entries(GROUPED_INTO)) {
    const parentEntry = byId.get(parentId);
    const childReg = modules[childId];
    if (!parentEntry || !childReg || !childReg.enabled) continue;
    const childName = typeof childReg.manifest?.name === "string" ? childReg.manifest.name : childId;
    parentEntry.keywords.push(childId, childName);
    parentEntry.quick = true;
  }
  return out;
}

/** 检索：空查询 = 全量；命中 name / id / 能力名任一即算。大小写不敏感。 */
export function filterCapabilities(entries: CapabilityEntry[], query: string): CapabilityEntry[] {
  const q = query.trim().toLowerCase();
  if (!q) return entries;
  return entries.filter((e) => e.keywords.some((k) => k.toLowerCase().includes(q)));
}

/**
 * 常驻只放前 N 个（横向一行），其余走「全部」。
 *
 * ★ 2026-11-22：`quick` 的条目优先占位 —— 分组父项（如「学习软件」）收编了多个入口，
 * 它不在前 8 就等于「分了组但人看不见」。其余仍保持注册顺序。
 */
export function quickCapabilities(entries: CapabilityEntry[], n = 8): CapabilityEntry[] {
  const pinned = entries.filter((e) => e.quick);
  const rest = entries.filter((e) => !e.quick);
  return [...pinned, ...rest].slice(0, Math.max(0, n));
}

/**
 * 收集全部模块**声明过的事件 topic**（`manifest.emits` 的并集）。
 *
 * 给「它正在做什么」订阅用：服务端按 fnmatch 过滤，**客户端的命名事件监听是精确匹配**，
 * 所以必须先把声明过的 topic 名全列出来挂 addEventListener —— 没法用一条通配代替。
 * 这也意味着：插件发了没声明的事件（服务端会拒），或声明了却没发（我们收不到），
 * 界面都只会少几条，不会错几条。诚实的方向。
 */
export function collectTopics(modules: Record<string, ModuleReg>): string[] {
  const set = new Set<string>();
  for (const reg of Object.values(modules)) {
    if (!reg || !reg.enabled) continue;
    const emits = reg.manifest?.emits;
    if (!Array.isArray(emits)) continue;
    for (const t of emits) if (typeof t === "string" && t) set.add(t);
  }
  return [...set].sort();
}