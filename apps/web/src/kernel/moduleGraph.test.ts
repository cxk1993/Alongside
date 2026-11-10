/**
 * moduleGraph（容器吸收推导层）判据。
 *
 * 锁四件事：
 *  ① **推导**：`manifest.tabs[].entry` ⇒ 吸收；无 entry 的 `key` 回退也要生效（auth）；
 *     `manifest.absorbs` ⇒ 无页归口；
 *  ② **三处实测分叉已修**：diary 有页（入口回来）/ countdown 归口 system /
 *     habits 归口 growth（不再与「成长罗盘」重复）；
 *  ③ **健壮**：未知模块、空输入、容器互吞、多容器争抢都不崩且确定性；
 *  ④ **单一入口**：buildModuleGraph 的对象方法与顶层便捷函数同源同结果。
 *
 * 夹具里的 tabs 抄自 `services/api/modules/<id>/manifest.json`（只取 tabs 子集），
 * 后端 manifest 改页签时这里要同步 —— 这正是「唯一真相源」的代价与价值。
 */
import { describe, expect, it } from "vitest";

import { canResolveLoader } from "./ModuleRegistry";
import {
  absorbedBy,
  buildModuleGraph,
  containerOf,
  normalizeTabs,
  isAbsorbed,
  resolveTabPage,
  type DeclaredTab,
  type GraphModuleLike,
} from "./moduleGraph";

/* ───────────────────────── 夹具（对齐真实 manifest） ───────────────────────── */

const SYSTEM_TABS = [
  { key: "catalog", label: "能力目录", entry: "catalog" },
  { key: "mcp", label: "MCP Server", entry: "mcp" },
  { key: "push", label: "推送", entry: "push" },
  { key: "export", label: "导出中心", entry: "export" },
  { key: "query", label: "跨区块查询", entry: "query" },
  { key: "plugins", label: "插件管理", entry: "plugins" },
  { key: "auth", label: "账户与鉴权" }, // 无 entry → 薄壳页，但 key 隐式吸收
];

const KNOWLEDGE_TABS = [
  { key: "review", label: "复盘", entry: "review" },
  { key: "notes", label: "笔记", entry: "notes" },
  { key: "docs", label: "文档", entry: "docs" },
  { key: "diary", label: "日记", entry: "diary" }, // 2026-11-10 写进 manifest（原补页表）
];

const GROWTH_TABS = [
  { key: "habits", label: "习惯", entry: "habits" },
  { key: "persona", label: "人格体系", entry: "persona" },
  { key: "health", label: "健康", entry: "health" },
];

const SCHEDULE_TABS = [
  { key: "calendar", label: "日程表", entry: "calendar" },
  { key: "todo", label: "待办", entry: "todo" },
  { key: "study", label: "学业", entry: "todo" }, // 同一模块的第二页（去重判据）
  { key: "course", label: "课程表", entry: "course" },
];

const leaf = (id: string): GraphModuleLike => ({ id, name: id, kind: "builtin" });

/** 真实仓库的容器 + 子模块子集（够覆盖三处分叉）。 */
function realWorldModules(): GraphModuleLike[] {
  return [
    { id: "system", name: "系统", kind: "container", tabs: SYSTEM_TABS, absorbs: ["countdown"] },
    { id: "knowledge", name: "知识库", kind: "container", tabs: KNOWLEDGE_TABS },
    { id: "growth", name: "成长罗盘", kind: "container", tabs: GROWTH_TABS },
    { id: "schedule", name: "日程待办", kind: "container", tabs: SCHEDULE_TABS },
    { id: "pi-agent", name: "Pi 智能体", kind: "third-party", absorbs: ["agents", "ai-chat"] },
    ...["catalog", "mcp", "push", "export", "query", "plugins", "auth"].map(leaf),
    ...["review", "notes", "docs", "diary"].map(leaf),
    ...["habits", "persona", "health"].map(leaf),
    ...["calendar", "todo", "course"].map(leaf),
    ...["agents", "ai-chat"].map(leaf),
    { id: "countdown", name: "倒计时", kind: "third-party" },
    leaf("finance"), // 独立模块：不该被任何容器吞
    leaf("packs"),
  ];
}

/* ───────────────────────── ① 推导 ───────────────────────── */

describe("buildModuleGraph · 吸收推导", () => {
  it("tabs[].entry ⇒ 子模块被容器吸收（system 七页全部归口）", () => {
    const g = buildModuleGraph(realWorldModules());
    for (const id of ["catalog", "mcp", "push", "export", "query", "plugins"]) {
      expect(g.containerOf(id)).toBe("system");
      expect(g.isAbsorbed(id)).toBe(true);
    }
  });

  it("无 entry 的页签按 key 回退吸收（auth 只有薄壳页，但入口不该单列）", () => {
    const g = buildModuleGraph(realWorldModules());
    expect(g.containerOf("auth")).toBe("system");
  });

  it("页序即吸收序，且同一模块的多页只算一次（schedule 的 study→todo）", () => {
    const g = buildModuleGraph(realWorldModules());
    expect(g.absorbedBy("schedule")).toEqual(["calendar", "todo", "course"]);
  });

  it("独立模块不被吞（finance / packs 仍是 Dock 单按钮）", () => {
    const g = buildModuleGraph(realWorldModules());
    expect(g.isAbsorbed("finance")).toBe(false);
    expect(g.isAbsorbed("packs")).toBe(false);
    expect(g.containerOf("finance")).toBeUndefined();
  });

  it("协议字段 absorbs ⇒ 无页归口（不占页签也照样吸收）", () => {
    const g = buildModuleGraph([
      { id: "sys", kind: "container", tabs: [{ key: "mcp", label: "MCP", entry: "mcp" }] },
      leaf("mcp"),
      leaf("summary"),
      { id: "sys2", kind: "core", absorbs: ["summary"] },
    ]);
    expect(g.containerOf("summary")).toBe("sys2");
    // 无页归口不能凭空造页
    expect(g.tabsOf("sys2")).toEqual([]);
    expect(g.absorbedBy("sys2")).toEqual(["summary"]);
  });
});

/* ───────────────────────── ② 三处分叉 ───────────────────────── */

describe("实测三处分叉已修", () => {
  it("diary：被 knowledge 吸收，且 knowledge 的页签里真的有「日记」页（入口回来）", () => {
    const g = buildModuleGraph(realWorldModules());
    expect(g.containerOf("diary")).toBe("knowledge");
    const labels = g.tabsOf("knowledge").map((t) => t.label);
    expect(labels).toContain("日记");
    expect(g.tabsOf("knowledge").some((t) => t.entry === "diary")).toBe(true);
  });

  it("countdown：纯 API 插件归口 system（不再单列一个点不出东西的按钮）", () => {
    const g = buildModuleGraph(realWorldModules());
    expect(g.containerOf("countdown")).toBe("system");
  });

  it("habits：归口 growth（有页签 ⇒ 必须被吸收，否则底端栏重复入口）", () => {
    const g = buildModuleGraph(realWorldModules());
    expect(g.containerOf("habits")).toBe("growth");
    expect(g.absorbedBy("growth")).toEqual(["habits", "persona", "health"]);
  });

  it("pi-agent：agents / ai-chat 归口（AI 类入口收敛为一个）", () => {
    const g = buildModuleGraph(realWorldModules());
    expect(g.containerOf("agents")).toBe("pi-agent");
    expect(g.containerOf("ai-chat")).toBe("pi-agent");
  });

  it("每个被吸收的模块，其容器页签里要么有对应页、要么是显式无页归口（不允许两头落空）", () => {
    const g = buildModuleGraph(realWorldModules());
    const noPageAllowed = new Set(["countdown", "agents", "ai-chat"]); // 显式 absorbs 归口
    for (const [child, cid] of Object.entries(g.absorbed)) {
      const hasPage = g.tabsOf(cid).some((t) => (t.entry ?? t.key) === child);
      expect(hasPage || noPageAllowed.has(child), `${child} → ${cid}`).toBe(true);
    }
  });
});

/* ───────────────────────── ③ 健壮性 ───────────────────────── */

describe("健壮性（清单滞后 / 脏数据不崩）", () => {
  it("空输入 ⇒ 空图，不抛错", () => {
    for (const input of [null, undefined, [], {}]) {
      const g = buildModuleGraph(input);
      expect(g.containers).toEqual([]);
      expect(g.absorbed).toEqual({});
      expect(g.absorbedBy("system")).toEqual([]);
      expect(g.containerOf("diary")).toBeUndefined();
    }
  });

  it("未知模块：tab 指向不存在的模块 ⇒ 静默跳过，不产生幽灵吸收", () => {
    const g = buildModuleGraph([
      { id: "c", kind: "container", tabs: [{ key: "ghost", label: "幽灵", entry: "ghost" }] },
    ]);
    expect(g.absorbedBy("c")).toEqual([]);
    expect(g.isAbsorbed("ghost")).toBe(false);
  });

  it("容器不互吞：tab 指向另一个容器 ⇒ 目标容器保持独立", () => {
    const g = buildModuleGraph([
      { id: "a", kind: "container", tabs: [{ key: "b", label: "B", entry: "b" }] },
      { id: "b", kind: "container", tabs: [{ key: "x", label: "X", entry: "x" }] },
      leaf("x"),
    ]);
    expect(g.isAbsorbed("b")).toBe(false);
    expect(g.containerOf("x")).toBe("b");
  });

  it("多容器争抢同一模块 ⇒ 按容器 id 升序先到先得（确定性，同一输入必得同一结果）", () => {
    const build = () =>
      buildModuleGraph([
        { id: "zeta", kind: "container", tabs: [{ key: "shared", label: "S", entry: "shared" }] },
        { id: "alpha", kind: "container", tabs: [{ key: "shared", label: "S", entry: "shared" }] },
        leaf("shared"),
      ]);
    expect(build().containerOf("shared")).toBe("alpha");
    expect(build().containerOf("shared")).toBe(build().containerOf("shared"));
  });

  it("store 形态输入（Record<id, { manifest }>）与数组输入结果一致", () => {
    const list = realWorldModules();
    const store = Object.fromEntries(list.map((m) => [m.id, { manifest: m, enabled: true }]));
    const a = buildModuleGraph(list);
    const b = buildModuleGraph(store);
    expect(b.absorbed).toEqual(a.absorbed);
    expect(b.containers).toEqual(a.containers);
  });

  it("脏项（无 id / null / 字符串）被忽略", () => {
    const g = buildModuleGraph([
      null,
      undefined,
      { name: "没有 id" } as unknown as GraphModuleLike,
      { id: "ok", kind: "builtin" },
    ]);
    expect(g.containers).toEqual([]);
    expect(g.absorbed).toEqual({});
  });
});

/* ───────────────────────── ④ 单一入口（页签只来自 manifest） ───────────────────────── */

describe("单一入口与页签归一化", () => {
  it("顶层便捷函数与对象方法同源同结果", () => {
    const mods = realWorldModules();
    const g = buildModuleGraph(mods);
    expect(containerOf(mods, "diary")).toBe(g.containerOf("diary"));
    expect(absorbedBy(mods, "growth")).toEqual(g.absorbedBy("growth"));
    expect(isAbsorbed(mods, "countdown")).toBe(true);
  });

  it("normalizeTabs：保序 + 同 key 只认第一个 + 不补页", () => {
    expect(normalizeTabs(KNOWLEDGE_TABS).map((t) => t.key)).toEqual([
      "review",
      "notes",
      "docs",
      "diary",
    ]);

    // 同一 key 出现两次 ⇒ 只认 manifest 里靠前的那个（不合并、不改标签）
    const dup = normalizeTabs([
      ...KNOWLEDGE_TABS,
      { key: "diary", label: "我的日记", entry: "diary" },
    ]);
    expect(dup).toHaveLength(4);
    expect(dup.find((t) => t.key === "diary")?.label).toBe("日记");
  });

  it("★ 补页表已删除：manifest 里没有的页，前端不会补出来", () => {
    const bare = KNOWLEDGE_TABS.filter((t) => t.key !== "diary");
    expect(normalizeTabs(bare).map((t) => t.key)).toEqual(["review", "notes", "docs"]);
  });
});

/* ───────────────────────── ⑤ absorbs 成为权威声明（2026-11-10 第三轮） ───────────────────────── */

describe("absorbs 优先级与回退", () => {
  it("★ 真实世界口径：tabs 反推 + absorbs 归口都来自 manifest，零冲突", () => {
    const g = buildModuleGraph(realWorldModules());
    expect(g.containerOf("diary")).toBe("knowledge"); // 有页吸收（tabs）
    expect(g.containerOf("mcp")).toBe("system"); // 有页吸收（tabs）
    expect(g.containerOf("countdown")).toBe("system"); // 无页归口（system.absorbs）
    expect(g.containerOf("agents")).toBe("pi-agent"); // 无页归口（pi-agent.absorbs）
    expect(g.conflicts).toEqual([]); // 真实世界当前无冲突
  });

  it("absorbs 显式声明 ⇒ 无页归口也生效，且排在页序之后", () => {
    const g = buildModuleGraph([
      {
        id: "sys",
        kind: "container",
        tabs: [{ key: "mcp", label: "MCP", entry: "mcp" }],
        absorbs: ["summary"],
      },
      leaf("mcp"),
      leaf("summary"),
    ]);
    expect(g.containerOf("summary")).toBe("sys");
    expect(g.containerOf("mcp")).toBe("sys"); // ★ absorbs 是补充声明，不替代 tabs
    expect(g.tabsOf("sys").map((t) => t.key)).toEqual(["mcp"]); // 无页归口不造页
    expect(g.absorbedBy("sys")).toEqual(["mcp", "summary"]); // 页序在前，无页归口在后
    expect(g.conflicts).toEqual([]);
  });

  it("absorbs 优先于 tabs：A.absorbs[X] + B.tabs[X] ⇒ X 归 A，并报 absorbed-and-tabbed", () => {
    const g = buildModuleGraph([
      { id: "alpha", kind: "container", absorbs: ["shared"] },
      { id: "beta", kind: "container", tabs: [{ key: "shared", label: "S", entry: "shared" }] },
      leaf("shared"),
    ]);
    expect(g.containerOf("shared")).toBe("alpha"); // 显式声明胜过隐式反推
    const c = g.conflicts.find((x) => x.kind === "absorbed-and-tabbed");
    expect(c).toBeTruthy();
    expect(c?.moduleId).toBe("shared");
    expect(c?.containers).toEqual(["alpha", "beta"]);
    expect(c?.resolvedTo).toBe("alpha");
  });

  it("同容器内 absorbs 与 tabs 重复声明 ⇒ 照样报冲突（后端已禁止，前端不静默）", () => {
    const g = buildModuleGraph([
      {
        id: "sys",
        kind: "container",
        tabs: [{ key: "mcp", label: "MCP", entry: "mcp" }],
        absorbs: ["mcp"],
      },
      leaf("mcp"),
    ]);
    expect(g.containerOf("mcp")).toBe("sys");
    const c = g.conflicts.find((x) => x.kind === "absorbed-and-tabbed");
    expect(c?.containers).toEqual(["sys"]);
    expect(c?.resolvedTo).toBe("sys");
  });

  it("一个子被两个容器吸收 ⇒ multi-absorbs + 按容器 id 升序归 alpha（确定性）", () => {
    const g = buildModuleGraph([
      { id: "zeta", kind: "container", absorbs: ["shared"] },
      { id: "alpha", kind: "container", absorbs: ["shared"] },
      leaf("shared"),
    ]);
    expect(g.containerOf("shared")).toBe("alpha");
    const multi = g.conflicts.filter((x) => x.kind === "multi-absorbs");
    expect(multi).toHaveLength(1);
    expect(multi[0].containers).toEqual(["alpha", "zeta"]);
    expect(multi[0].resolvedTo).toBe("alpha");
  });

  it("自引用 ⇒ self-absorb，不吸收自己", () => {
    const g = buildModuleGraph([{ id: "sys", kind: "container", absorbs: ["sys"] }]);
    expect(g.isAbsorbed("sys")).toBe(false);
    const c = g.conflicts.find((x) => x.kind === "self-absorb");
    expect(c?.moduleId).toBe("sys");
    expect(c?.resolvedTo).toBeUndefined();
  });

  it("absorbs 指向未知模块 ⇒ unknown-absorb-target（拼写错误不静默）", () => {
    const g = buildModuleGraph([{ id: "sys", kind: "container", absorbs: ["countdwon"] }]);
    expect(g.isAbsorbed("countdwon")).toBe(false);
    expect(g.conflicts.some((c) => c.kind === "unknown-absorb-target")).toBe(true);
  });

  it("absorbs 指向另一个容器 ⇒ container-absorb，容器不互吞", () => {
    const g = buildModuleGraph([
      { id: "a", kind: "container", absorbs: ["b"] },
      { id: "b", kind: "container" },
    ]);
    expect(g.isAbsorbed("b")).toBe(false);
    expect(g.conflicts.some((c) => c.kind === "container-absorb")).toBe(true);
  });

  it("★ 容器身份只有两个来源：kind:container 或非空 absorbs（补表机制已删）", () => {
    const g = buildModuleGraph([
      { id: "plain", kind: "builtin", tabs: [{ key: "child", label: "C", entry: "child" }] },
      leaf("child"),
    ]);
    expect(g.containers).toEqual([]); // 只有 tabs 不给容器身份
    expect(g.containerOf("child")).toBeUndefined();
  });

  it("conflicts 输出稳定（同一输入两次深度相等）", () => {
    const build = () =>
      buildModuleGraph([
        { id: "a", kind: "container", absorbs: ["x", "a"] },
        { id: "b", kind: "container", absorbs: ["x"] },
        { id: "b2", kind: "container", tabs: [{ key: "x", label: "X", entry: "x" }] },
        leaf("x"),
      ]);
    expect(build().conflicts).toEqual(build().conflicts);
  });
});

/* ───────────────────────── ⑥ 页签解析（畸形数据不白屏） ───────────────────────── */

describe("resolveTabPage · 三类畸形数据", () => {
  it("entry 指向的模块不存在 ⇒ unknown-module（薄壳语义，不抛错）", () => {
    const p = resolveTabPage({ key: "ghost", label: "幽灵", entry: "ghost" }, realWorldModules());
    expect(p.mode).toBe("unknown-module");
    expect(p.moduleId).toBe("ghost");
    expect(p.reason).toContain("ghost");
  });

  it("模块存在但无 entry ⇒ backend（纯 API 薄壳）", () => {
    const p = resolveTabPage({ key: "countdown", label: "倒计时" }, realWorldModules());
    expect(p.mode).toBe("backend");
    expect(p.moduleId).toBe("countdown");
  });

  it("★ tab.key 与 tab.entry 不一致 ⇒ 按 entry 解析目标（key 只当页签标识）", () => {
    const mods: GraphModuleLike[] = [
      { id: "study", kind: "builtin", entry: "" },
      { id: "todo", kind: "builtin", entry: "@/apps/todo" },
    ];
    const p = resolveTabPage({ key: "study", label: "学业", entry: "todo" }, mods);
    expect(p.moduleId).toBe("todo"); // 不是 "study"
    expect(p.mode).toBe("app");
    expect(p.appEntry).toBe("@/apps/todo");
  });

  it("entry 为空串 ⇒ 回退 key（auth 这类无 App 的后端页）", () => {
    const p = resolveTabPage({ key: "auth", label: "账户", entry: "" }, realWorldModules());
    expect(p.moduleId).toBe("auth");
    expect(p.mode).toBe("backend");
  });

  it("模块声明了 entry 但文件不存在 ⇒ canResolveLoader 预检为 missing-app", () => {
    const p = resolveTabPage(
      { key: "study", label: "学业", entry: "study" },
      [{ id: "study", kind: "builtin", entry: "@/apps/study" }],
    );
    expect(p.mode).toBe("app");
    expect(p.appEntry).toBe("@/apps/study");
    // apps/study 目录只有 StudyApp.tsx，没有 index.tsx ⇒ 不能按目录约定加载
    expect(canResolveLoader("@/apps/study")).toBe(false);
    expect(canResolveLoader("@/apps/todo")).toBe(true);
  });
});

/* ───────────────────────── ⑦ 页签归一化（畸形数据不崩） ───────────────────────── */

describe("normalizeTabs · 归一化", () => {
  it("畸形 tabs：缺 label 回退 key、空 entry 丢弃、缺 key 丢弃、null 丢弃", () => {
    const tabs = normalizeTabs([
      { key: "a" } as DeclaredTab,
      { key: "b", label: "B", entry: "" },
      { key: "", label: "无 key" } as DeclaredTab,
      null as unknown as DeclaredTab,
    ]);
    expect(tabs).toEqual([
      { key: "a", label: "a" },
      { key: "b", label: "B" },
    ]);
  });

  it("★ 页签顺序按 manifest 原样（不重排、不与任何补表合并）", () => {
    const tabs = normalizeTabs([
      { key: "diary", label: "日记本", entry: "diary" },
      { key: "review", label: "复盘", entry: "review" },
    ]);
    expect(tabs.map((t) => t.label)).toEqual(["日记本", "复盘"]);
    expect(tabs).toHaveLength(2);
  });
});

/* ─────────────── ⑧ 迁移前置条件（审计 2026-11-10 14:20 实测结论，锁成判据） ─────────────── */

describe("★ 容器迁 useContainerPages 的前置条件（不满足就是静默丢功能）", () => {
  it("schedule.study 的真实数据会解析成 TodoApp —— 数据待修，不是设计", () => {
    const mods: GraphModuleLike[] = [
      { id: "schedule", kind: "container", tabs: SCHEDULE_TABS },
      { id: "todo", kind: "builtin", entry: "@/apps/todo" },
    ];
    const study = SCHEDULE_TABS.find((t) => t.key === "study");
    expect(study).toBeTruthy();

    const p = resolveTabPage(study!, mods);
    expect(p.moduleId).toBe("todo"); // ← 真实数据的解析落点：第二个待办页
    expect(p.appEntry).toBe("@/apps/todo");

    // 真实学业页不是模块：清单里没有 study，apps/study 也没有 index.tsx ⇒ 写成 entry:"study" 同样加载不了
    expect(mods.some((m) => m.id === "study")).toBe(false);
    expect(canResolveLoader("@/apps/study")).toBe(false);
  });

  it("pi-agent 吸收了 agents/ai-chat 但 manifest 零页签 —— 迁容器页会落「未声明页签」空壳", () => {
    const g = buildModuleGraph(realWorldModules());
    expect(g.absorbedBy("pi-agent")).toEqual(["agents", "ai-chat"]);
    expect(g.tabsOf("pi-agent")).toEqual([]);
  });
});
