/**
 * U3 · 桌面级双侧栏视图侧测试（判据 K1-K8）。
 * 折叠持久化 / 折叠态把手 / 内置今日摘要 / 无贡献零渲染 / 贡献渲染。
 *
 * ★ 2026-09-27（hermes）：右栏「今日」摘要改为走内核 BFF 取真实计数
 *   （原先是零信息四钮），因此渲染需要 QueryClientProvider。
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { DesktopSidebar } from "./DesktopSidebar";
import { registerContributions, unregisterContributions } from "../slots/contributions";
import { useDesktopStore } from "../store";

// 摘要卡需要 QueryClient（BFF 拉取）。用模块级 client，rerender 间保持同一实例。
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
function Providers({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
function renderSide(ui: ReactElement) {
  return render(ui, { wrapper: Providers });
}

function resetStore(): void {
  useDesktopStore.setState((s) => ({
    ...s,
    modules: {},
    windows: [],
    activeWorkspaceId: "ws-1",
    workspaces: [{ id: "ws-1", name: "工作区 1", pinZ: 0, topZ: 0, topPinZ: 0, seq: 0 }],
  }));
}

describe("U3 · DesktopSidebar（桌面级双侧栏）", () => {
  beforeEach(() => {
    resetStore();
    localStorage.clear();
  });
  afterEach(() => {
    cleanup();
    unregisterContributions("demo-side");
  });

  it("K1 · 折叠状态 localStorage 持久化（刷新保持）", () => {
    const { container, rerender } = renderSide(<DesktopSidebar side="left" />);
    // 初始展开
    expect(
      container
        .querySelector("[data-testid='desktop-sidebar-left']")
        ?.getAttribute("aria-expanded"),
    ).toBe("true");
    // 点把手折叠
    fireEvent.click(screen.getByLabelText("收起左侧栏"));
    expect(
      container
        .querySelector("[data-testid='desktop-sidebar-left']")
        ?.getAttribute("aria-expanded"),
    ).toBe("false");
    expect(localStorage.getItem("alongside.dock-left.collapsed")).toBe("1");
    // 重渲染（模拟刷新）→ 保持折叠
    rerender(<DesktopSidebar side="left" />);
    expect(
      container
        .querySelector("[data-testid='desktop-sidebar-left']")
        ?.getAttribute("aria-expanded"),
    ).toBe("false");
  });

  it("K2 · 折叠态仍保留可见把手（toggle obvious and persistent）", () => {
    const { container } = renderSide(<DesktopSidebar side="left" />);
    fireEvent.click(screen.getByLabelText("收起左侧栏"));
    expect(container.querySelector(".desktop-sidebar__toggle")).toBeTruthy();
    expect(screen.getByLabelText("展开左侧栏")).toBeTruthy();
  });

  it("K4 · 折叠态 Escape 展开", () => {
    const { container } = renderSide(<DesktopSidebar side="left" />);
    fireEvent.click(screen.getByLabelText("收起左侧栏"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(
      container
        .querySelector("[data-testid='desktop-sidebar-left']")
        ?.getAttribute("aria-expanded"),
    ).toBe("true");
  });

  it("K5 · 右栏内置今日摘要（四源入口 + 计数）", async () => {
    const { container } = renderSide(<DesktopSidebar side="right" />);
    expect(container.querySelector("[data-testid='desktop-today-summary']")).toBeTruthy();
    expect(screen.getByText("待办")).toBeTruthy();
    expect(screen.getByText("复盘")).toBeTruthy();
    // ★ 2026-09-27：四源各带一个计数位（拿不到时显示 '?'，不阻塞入口可用性）
    //   testid 用索引（中性），不在 kernel 测试里引入业务词。
    expect(screen.getAllByTestId(/^today-count-/)).toHaveLength(4);
  });

  it("K6 · 无贡献零渲染不占位（左栏无贡献时只有框架无卡）", () => {
    const { container } = renderSide(<DesktopSidebar side="left" />);
    expect(container.querySelector(".desktop-sidebar__slots")?.children.length ?? 0).toBe(0);
    expect(container.querySelector("[data-testid='desktop-today-summary']")).toBeNull();
  });

  it("K5b · 左栏内置模块导航（左=导航/结构，点击开窗）", () => {
    useDesktopStore.getState().registerModule({
      id: "demo-app",
      name: "演示应用",
      version: "0.0.1",
      kind: "builtin",
      icon: "D",
      description: "demo",
      entry: "",
      window: { w: 400, h: 300 },
    } as never);
    const openWindow = vi
      .spyOn(useDesktopStore.getState(), "openWindow")
      .mockImplementation(() => {});
    const { container } = renderSide(<DesktopSidebar side="left" />);
    expect(container.querySelector("[data-testid='desktop-side-nav']")).toBeTruthy();
    expect(screen.getByText("演示应用")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("打开演示应用"));
    expect(openWindow).toHaveBeenCalledWith("demo-app");
    openWindow.mockRestore();
  });

  it("K7 · 贡献渲染（挂 desktop.dock-right 的卡出现且独立兜错）", () => {
    // 守卫：贡献必须与 store 中 manifest.slots 声明一致 + 插件启用（总纲 §1.3.4 硬规则）
    useDesktopStore.getState().registerModule({
      id: "demo-side",
      name: "演示侧栏",
      version: "0.0.1",
      kind: "builtin",
      icon: "d",
      description: "demo",
      entry: "",
      window: { w: 400, h: 300 },
      slots: ["desktop.dock-right"],
    } as never);
    registerContributions("demo-side", [
      {
        slot: "desktop.dock-right",
        pluginId: "demo-side",
        component: () => <div data-testid="demo-right-card">右栏卡</div>,
      },
    ]);
    const { container } = renderSide(<DesktopSidebar side="right" />);
    expect(container.querySelector("[data-testid='demo-right-card']")).toBeTruthy();
    expect(screen.getByText("右栏卡")).toBeTruthy();
  });
});
