/**
 * SidebarDock（桌面侧栏槽贡献）单测。
 *
 * 覆盖五件「会让它悄悄坏掉」的事：
 *   1. 空 / 加载中 → 不渲染任何东西（不占位）
 *   2. enabled=false 的条目被过滤
 *   3. link 渲染成 <a href>，且带 rel=noopener noreferrer
 *   4. note 渲染成 <details>，正文在展开区
 *   5. 分组标题出现、组内 pinned 排前面
 *
 * 网络全 mock，不依赖后端。
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import SidebarDock from "./SidebarDock";

const state: { items: unknown[]; reject?: boolean } = { items: [] };

vi.mock("@/shared/api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    get: vi.fn(async () => {
      if (state.reject) throw new Error("boom");
      return { items: state.items, count: state.items.length };
    }),
  },
}));

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

function item(over: Record<string, unknown> = {}) {
  return {
    id: "x1",
    type: "link",
    label: "GitHub",
    icon: "",
    abbr: "GH",
    href: "https://github.com",
    target: "_blank",
    body: "",
    node_ref: "",
    description: "",
    group: "",
    order: 0,
    enabled: true,
    pinned: false,
    ...over,
  };
}

describe("SidebarDock", () => {
  it("空列表：不渲染（不占位）", async () => {
    state.items = [];
    const { container } = render(wrap(<SidebarDock />));
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector(".sidebar-dock")).toBeNull();
  });

  it("加载失败：静默降级，不渲染、不抛错", async () => {
    state.items = [];
    state.reject = true;
    const { container } = render(wrap(<SidebarDock />));
    await new Promise((r) => setTimeout(r, 30));
    expect(container.querySelector(".sidebar-dock")).toBeNull();
    state.reject = false;
  });

  it("link：渲染 <a href> 且 rel 安全", async () => {
    state.items = [item()];
    render(wrap(<SidebarDock />));
    const a = await screen.findByRole("link", { name: /GitHub/ });
    expect(a.getAttribute("href")).toBe("https://github.com");
    expect(a.getAttribute("rel")).toBe("noopener noreferrer");
    expect(a.getAttribute("target")).toBe("_blank");
  });

  it("enabled=false 的条目不出现", async () => {
    state.items = [
      item({ id: "a", label: "可见" }),
      item({ id: "b", label: "隐藏", enabled: false }),
    ];
    render(wrap(<SidebarDock />));
    await screen.findByText("可见");
    expect(screen.queryByText("隐藏")).toBeNull();
  });

  it("note：渲染成 details，正文在内部", async () => {
    state.items = [
      item({ id: "n1", type: "note", label: "便签A", href: "", abbr: "", body: "今天要交报告" }),
    ];
    const { container } = render(wrap(<SidebarDock />));
    await screen.findByText("便签A");
    const details = container.querySelector("details.sidebar-dock__note");
    expect(details).not.toBeNull();
    expect(details?.textContent).toContain("今天要交报告");
  });

  it("分组标题出现，且 pinned 在同组排前面", async () => {
    state.items = [
      item({ id: "g1", label: "普通项", group: "开发", order: 0, pinned: false }),
      item({ id: "g2", label: "置顶项", group: "开发", order: 5, pinned: true }),
    ];
    const { container } = render(wrap(<SidebarDock />));
    await screen.findByText("开发");
    const labels = [...container.querySelectorAll(".sidebar-dock__label")].map((e) => e.textContent);
    expect(labels.indexOf("置顶项")).toBeLessThan(labels.indexOf("普通项"));
  });
});