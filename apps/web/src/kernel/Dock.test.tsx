/**
 * ★ Dock 归口判据（2026-11-09 重构：吸收关系从 manifest.tabs/absorbs 推导）。
 *
 * 背景：Dock 显隐的杠杆只有「吸收关系」，**不是 `manifest.slots`** ——
 *   Dock 是 `Object.values(modules).map(...)`（遍历 store 全部模块），从不读 slots，
 *   所以「摘 desktop.dock 槽」永远不产生隐藏效果（坑谱 #15）。
 *
 * 旧的 `DOCK_MERGE`（19 条手写表）已删除：它与 `SystemApp.DEFAULT_TABS` 分叉，
 * 造成 diary 丢入口 / habits 双入口。现在唯一真相源是容器 manifest 的
 * `tabs[]`（有页的吸收）与 `absorbs[]`（无页归口），推导见 `kernel/moduleGraph.ts`。
 *
 * 本文件锁五件事：
 *  ① **机制事实**：没有 `desktop.dock` 槽的模块仍会出现（"摘槽隐藏"永远无效）；
 *  ② **归口生效**：tabs/absorbs 指向的模块不单独出按钮（容器在且启用时）；
 *  ③ **fail-safe**：容器不存在 / 未启用 / 未声明 tabs ⇒ 保留原按钮（绝不丢入口）；
 *  ④ **三处分叉已修**：diary 与 countdown 不单列、habits 不重复出现；
 *  ⑤ 角标数量 = 真实被吸收且已注册的模块数。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Dock } from "./Dock";
import { useDesktopStore } from "./store";
import type { ModuleManifest, ModuleTabSpec } from "./types";

interface MiniMod {
  id: string;
  name?: string;
  kind?: string;
  slots?: string[];
  tabs?: ModuleTabSpec[];
  absorbs?: string[];
  enabled?: boolean;
}

function setModules(list: MiniMod[]): void {
  const modules: Record<string, { manifest: ModuleManifest; enabled: boolean }> = {};
  for (const m of list) {
    modules[m.id] = {
      manifest: {
        id: m.id,
        name: m.name ?? m.id,
        version: "0.1.0",
        kind: m.kind ?? "builtin",
        ...(m.slots ? { slots: m.slots } : {}),
        ...(m.tabs ? { tabs: m.tabs } : {}),
        ...(m.absorbs ? { absorbs: m.absorbs } : {}),
      } as unknown as ModuleManifest,
      enabled: m.enabled ?? true,
    };
  }
  useDesktopStore.setState({ modules, windows: [] });
}

const SYSTEM_TABS: ModuleTabSpec[] = [
  { key: "catalog", label: "能力目录", entry: "catalog" },
  { key: "mcp", label: "MCP Server", entry: "mcp" },
  { key: "push", label: "推送", entry: "push" },
  { key: "export", label: "导出中心", entry: "export" },
  { key: "query", label: "跨区块查询", entry: "query" },
  { key: "plugins", label: "插件管理", entry: "plugins" },
  { key: "auth", label: "账户与鉴权" },
];

afterEach(() => {
  cleanup();
  useDesktopStore.setState({ modules: {}, windows: [] });
});

describe("Dock · 吸收归口（manifest 推导）", () => {
  it("① 机制事实：**没有** desktop.dock 槽的模块仍会出现（所以'摘槽隐藏'永远无效）", () => {
    setModules([{ id: "somemod", name: "某模块" }]); // ← 故意不给 slots
    render(<Dock />);
    expect(screen.getByLabelText("某模块")).toBeTruthy();
  });

  it("② 归口生效：tabs 命中的模块不单独出按钮，角标数 = 被吸收数", () => {
    setModules([
      { id: "system", name: "系统", kind: "container", tabs: SYSTEM_TABS },
      { id: "query", name: "跨模块查询" },
      { id: "plugins", name: "插件管理" },
      { id: "export", name: "导出中心" },
      { id: "catalog", name: "能力目录" },
    ]);
    render(<Dock />);
    expect(screen.getByLabelText(/^系统/)).toBeTruthy();
    expect(screen.queryByLabelText("跨模块查询")).toBeNull();
    expect(screen.queryByLabelText("插件管理")).toBeNull();
    expect(screen.queryByLabelText("导出中心")).toBeNull();
    expect(screen.queryByLabelText("能力目录")).toBeNull();
    // 系统吸收了 4 个已注册模块（catalog/export/query/plugins）
    expect(screen.getByText("+4")).toBeTruthy();
  });

  it("③ fail-safe：容器不存在 ⇒ 保留原按钮（绝不因合并丢入口）", () => {
    setModules([{ id: "query", name: "跨模块查询" }]);
    render(<Dock />);
    expect(screen.getByLabelText("跨模块查询")).toBeTruthy();
  });

  it("③b fail-safe：容器存在但**未启用** ⇒ 同样保留原按钮", () => {
    setModules([
      { id: "system", name: "系统", kind: "container", tabs: SYSTEM_TABS, enabled: false },
      { id: "query", name: "跨模块查询" },
    ]);
    render(<Dock />);
    expect(screen.getByLabelText("跨模块查询")).toBeTruthy();
  });

  it("③c fail-safe：容器在但**未声明 tabs/absorbs**（老清单）⇒ 不吸收，按钮全留", () => {
    setModules([
      { id: "system", name: "系统", kind: "container" },
      { id: "query", name: "跨模块查询" },
    ]);
    render(<Dock />);
    expect(screen.getByLabelText("跨模块查询")).toBeTruthy();
  });

  it("④ habits 不再重复：growth 的 tabs 含 habits ⇒ 底端栏只剩「成长罗盘」", () => {
    setModules([
      {
        id: "growth",
        name: "成长罗盘",
        kind: "container",
        tabs: [
          { key: "habits", label: "习惯", entry: "habits" },
          { key: "persona", label: "人格体系", entry: "persona" },
          { key: "health", label: "健康", entry: "health" },
        ],
      },
      { id: "habits", name: "习惯" },
      { id: "persona", name: "人格体系" },
      { id: "health", name: "健康" },
    ]);
    render(<Dock />);
    expect(screen.getByLabelText(/^成长罗盘/)).toBeTruthy();
    expect(screen.queryByLabelText("习惯")).toBeNull();
  });

  it("④ diary：被知识库吸收 ⇒ 不单列；countdown 走 absorbs ⇒ 不单列", () => {
    setModules([
      {
        id: "knowledge",
        name: "知识库",
        kind: "container",
        tabs: [
          { key: "review", label: "复盘", entry: "review" },
          { key: "notes", label: "笔记", entry: "notes" },
          { key: "docs", label: "文档", entry: "docs" },
        ],
      },
      { id: "diary", name: "日记" },
      { id: "system", name: "系统", kind: "container", tabs: SYSTEM_TABS, absorbs: ["countdown"] },
      { id: "countdown", name: "倒计时" },
    ]);
    render(<Dock />);
    expect(screen.queryByLabelText("日记")).toBeNull();
    expect(screen.queryByLabelText("倒计时")).toBeNull();
  });

  it("⑤ 覆盖面：主人点名的并入面全部归口 ⇒ 只剩四个容器按钮", () => {
    const expected = [
      "catalog",
      "mcp",
      "push",
      "auth",
      "export",
      "query",
      "plugins",
      "review",
      "notes",
      "docs",
      "diary",
      "persona",
      "health",
      "habits",
      "calendar",
      "todo",
      "course",
      "countdown",
    ];
    setModules([
      { id: "system", name: "系统", kind: "container", tabs: SYSTEM_TABS },
      {
        id: "knowledge",
        name: "知识库",
        kind: "container",
        tabs: [
          { key: "review", label: "复盘", entry: "review" },
          { key: "notes", label: "笔记", entry: "notes" },
          { key: "docs", label: "文档", entry: "docs" },
        ],
      },
      {
        id: "growth",
        name: "成长罗盘",
        kind: "container",
        tabs: [
          { key: "habits", label: "习惯", entry: "habits" },
          { key: "persona", label: "人格体系", entry: "persona" },
          { key: "health", label: "健康", entry: "health" },
        ],
      },
      {
        id: "schedule",
        name: "日程待办",
        kind: "container",
        tabs: [
          { key: "calendar", label: "日程表", entry: "calendar" },
          { key: "todo", label: "待办", entry: "todo" },
          { key: "course", label: "课程表", entry: "course" },
        ],
      },
      ...expected.map((id) => ({ id, name: id })),
    ]);
    render(<Dock />);
    // 只剩四个容器（其余全部被归口）
    expect(screen.getAllByRole("button")).toHaveLength(4);
    expect(screen.getByLabelText(/^系统/)).toBeTruthy();
    expect(screen.getByLabelText(/^知识库/)).toBeTruthy();
    expect(screen.getByLabelText(/^成长罗盘/)).toBeTruthy();
    expect(screen.getByLabelText(/^日程待办/)).toBeTruthy();
  });
});
