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
    // 用 growth（无前端补页登记）验证「无 tabs 且无补页」的真·空容器；
    // knowledge 有 diary 补页，即使 manifest 缺失也会出一页 —— 那是预期行为。
    render(<Host containerId="growth" />);
    expect(screen.getByTestId("container-empty-growth")).toBeTruthy();
  });
});