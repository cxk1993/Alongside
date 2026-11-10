/**
 * 容器页装配器判据（kernel/containerPages.tsx）。
 *
 * 这一层是把「被吸收」变成「看得见」的那一步：
 *   manifest.absorbs/tabs 只说明某个子模块被归口，如果容器窗口里没有对应页，
 *   入口就仍然是消失的（这正是重构前 diary 的真实处境）。
 *
 * 锁三件事：
 *  ① diary 的入口解析链通（`@/apps/diary` → PluginModule.Component），
 *     页装配器能把它变成知识库的第 4 页；
 *  ② tabs 顺序 = 页签顺序（不重排、不增删）；无 entry 页落「后端服务」薄壳；
 *  ③ 容器未进注册表 / 未声明 tabs ⇒ 空容器提示页（不白屏、不假渲染）。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import MultitabFrame from "./MultitabFrame";
import { useContainerPages } from "./containerPages";
import { resolveLoader } from "./ModuleRegistry";
import { useDesktopStore } from "./store";
import type { ModuleManifest } from "./types";

function setModules(list: Array<Record<string, unknown>>): void {
  const modules: Record<string, { manifest: ModuleManifest; enabled: boolean }> = {};
  for (const m of list) {
    modules[m.id as string] = { manifest: m as unknown as ModuleManifest, enabled: true };
  }
  useDesktopStore.setState({ modules, windows: [] });
}

const KNOWLEDGE = {
  id: "knowledge",
  name: "知识库",
  version: "0.1.0",
  kind: "container",
  entry: "@/apps/knowledge",
  tabs: [
    { key: "review", label: "复盘", entry: "review" },
    { key: "notes", label: "笔记", entry: "notes" },
    { key: "docs", label: "文档", entry: "docs" },
    // 2026-11-10：日记页已写进真实 manifest（补页表连同机制删除），夹具同步
    { key: "diary", label: "日记", entry: "diary" },
  ],
};

const DIARY = {
  id: "diary",
  name: "日记",
  version: "0.1.0",
  kind: "builtin",
  entry: "@/apps/diary",
};

const AUTH = {
  id: "auth",
  name: "账户与鉴权",
  version: "0.1.0",
  kind: "core",
  entry: "",
};

const SYSTEM = {
  id: "system",
  name: "系统",
  version: "0.1.0",
  kind: "container",
  entry: "@/apps/system",
  tabs: [
    { key: "mcp", label: "MCP Server", entry: "mcp" },
    { key: "auth", label: "账户与鉴权" },
  ],
};

function Host({ containerId }: { containerId: string }) {
  const pages = useContainerPages(containerId);
  return <MultitabFrame ariaLabel="容器页签" persistKey={`t-${containerId}`} pages={pages} />;
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  useDesktopStore.setState({ modules: {}, windows: [] });
});

describe("containerPages · 页装配", () => {
  it("① diary 的 entry 可解析（入口链通，不是只补了个标签）", async () => {
    const mod = await resolveLoader("@/apps/diary")();
    expect((mod.default as { manifestId?: string }).manifestId).toBe("diary");
    expect((mod.default as { Component?: unknown }).Component).toBeTruthy();
  }, 30_000); // 首次 import DiaryApp 要现场编译整条依赖链，5s 默认值不够

  it("①b 知识库窗口出现「日记」页（diary 被吸收 ⇒ 必须有页，入口才真的回来）", () => {
    setModules([KNOWLEDGE, DIARY]);
    render(<Host containerId="knowledge" />);
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["复盘", "笔记", "文档", "日记"]);
  });

  it("② 无 entry 页落「已接入（后端服务）」薄壳，不假装有 UI", () => {
    setModules([SYSTEM, AUTH]);
    render(<Host containerId="system" />);
    // 首屏是 mcp 页（有 entry 但 mcp 未注册 ⇒ 也是薄壳）；auth 页薄壳语义不变
    expect(screen.getByTestId("mtab-tab-auth")).toBeTruthy();
    expect(screen.getByTestId("system-backend-page-mcp")).toBeTruthy();
  });

  it("③ 容器未进注册表 ⇒ 空容器提示页（不白屏）", () => {
    setModules([]);
    // 补页表已删除（2026-11-10）：任何容器「不在注册表 / 没声明 tabs」都落空容器页，
    // 不存在「前端偷偷补一页」这条后路。
    render(<Host containerId="growth" />);
    expect(screen.getByTestId("container-empty-growth")).toBeTruthy();
    render(<Host containerId="knowledge" />);
    expect(screen.getByTestId("container-empty-knowledge")).toBeTruthy();
  });
});

/* ─────────────────── ④ 畸形页签兜底（2026-11-10 第三轮 · N3） ─────────────────── */

const CONTAINER_SCHEDULE = {
  id: "schedule",
  name: "日程待办",
  version: "0.1.0",
  kind: "container",
  entry: "@/apps/schedule",
};

const TODO_MOCK = {
  id: "todo",
  name: "待办",
  version: "0.1.0",
  kind: "builtin",
  entry: "@mocks/alpha",
};

describe("containerPages · 畸形页签兜底", () => {
  it("★ entry 指向未注册模块 ⇒ 薄壳「模块未注册」，不白屏不报错", () => {
    setModules([
      {
        ...CONTAINER_SCHEDULE,
        tabs: [{ key: "ghost", label: "幽灵页", entry: "ghost" }],
      },
    ]);
    render(<Host containerId="schedule" />);
    const shell = screen.getByTestId("system-backend-page-ghost");
    expect(shell.getAttribute("data-page-reason")).toBe("unknown-module");
    expect(shell.textContent).toContain("未注册");
    expect(screen.getByRole("tab").textContent).toBe("幽灵页");
  });

  it("★ key≠entry ⇒ 按 entry 加载真实 App（key 只当页签标识）", async () => {
    setModules([
      {
        ...CONTAINER_SCHEDULE,
        tabs: [{ key: "study", label: "学业", entry: "todo" }],
      },
      TODO_MOCK,
    ]);
    render(<Host containerId="schedule" />);
    // 页签叫「学业」，内容却是 todo 指向的 App —— 这正是 schedule.study 的现状
    expect(screen.getByRole("tab").textContent).toBe("学业");
    expect(await screen.findByText("演示模块 · 甲")).toBeTruthy();
  });

  it("★ entry 指向的 App 文件缺失 ⇒ 薄壳「入口缺失」（不是含糊的「加载失败」）", () => {
    setModules([
      {
        ...CONTAINER_SCHEDULE,
        tabs: [{ key: "study", label: "学业", entry: "study" }],
      },
      // apps/study 只有 StudyApp.tsx，没有 index.tsx ⇒ 目录约定解析不到
      { id: "study", name: "学业", version: "0.1.0", kind: "builtin", entry: "@/apps/study" },
    ]);
    render(<Host containerId="schedule" />);
    const shell = screen.getByTestId("system-backend-page-study");
    expect(shell.getAttribute("data-page-reason")).toBe("missing-app");
    expect(shell.textContent).toContain("入口文件缺失");
  });

  it("★ manifest 自带的页签顺序即展示顺序（不重排、不补页）", () => {
    setModules([
      {
        id: "knowledge",
        name: "知识库",
        version: "0.1.0",
        kind: "container",
        entry: "@/apps/knowledge",
        tabs: [
          { key: "diary", label: "日记本", entry: "diary" },
          { key: "review", label: "复盘", entry: "review" },
        ],
      },
      DIARY,
    ]);
    render(<Host containerId="knowledge" />);
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["日记本", "复盘"]);
  });
});