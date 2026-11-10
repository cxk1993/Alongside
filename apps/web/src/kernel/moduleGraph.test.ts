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

import {
  absorbedBy,
  buildModuleGraph,
  containerOf,
  effectiveTabsOf,
  isAbsorbed,
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
    { id: "system", name: "系统", kind: "container", tabs: SYSTEM_TABS },
    { id: "knowledge", name: "知识库", kind: "container", tabs: KNOWLEDGE_TABS },
    { id: "growth", name: "成长罗盘", kind: "container", tabs: GROWTH_TABS },
    { id: "schedule", name: "日程待办", kind: "container", tabs: SCHEDULE_TABS },
    { id: "pi-agent", name: "Pi 智能体", kind: "third-party" },
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

/* ───────────────────────── ④ 单一入口 / 补页 ───────────────────────── */

describe("单一入口与补页登记", () => {
  it("顶层便捷函数与对象方法同源同结果", () => {
    const mods = realWorldModules();
    const g = buildModuleGraph(mods);
    expect(containerOf(mods, "diary")).toBe(g.containerOf("diary"));
    expect(absorbedBy(mods, "growth")).toEqual(g.absorbedBy("growth"));
    expect(isAbsorbed(mods, "countdown")).toBe(true);
  });

  it("effectiveTabsOf：manifest.tabs 优先，补页只补不覆盖", () => {
    const merged = effectiveTabsOf("knowledge", KNOWLEDGE_TABS);
    expect(merged.map((t) => t.key)).toEqual(["review", "notes", "docs", "diary"]);

    // manifest 自己声明了 diary ⇒ 以它为准（补页不覆盖），仍只有一页
    const declared = effectiveTabsOf("knowledge", [
      ...KNOWLEDGE_TABS,
      { key: "diary", label: "我的日记", entry: "diary" },
    ]);
    expect(declared).toHaveLength(4);
    expect(declared.find((t) => t.key === "diary")?.label).toBe("我的日记");
  });

  it("没有登记的容器不在补页范围（不误伤）", () => {
    expect(effectiveTabsOf("growth", GROWTH_TABS)).toEqual(GROWTH_TABS);
  });
});
