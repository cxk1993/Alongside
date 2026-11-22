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
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { SIDE_COLLAPSE_KEY } from "./DesktopSidebar";

import { DesktopSidebar } from "./DesktopSidebar";
import { registerContributions, unregisterContributions } from "../slots/contributions";
import { useDesktopStore } from "../store";

// r6 起今日摘要行清单来自 BFF providers —— 需要 mock api（此前静态四源不依赖网络）。
vi.mock("@/shared/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/shared/api/client")>("@/shared/api/client");
  return { ...actual, api: { get: vi.fn() } };
});
import { api } from "@/shared/api/client";

// 摘要卡需要 QueryClient（BFF 拉取）。用模块级 client，rerender 间保持同一实例。
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
function Providers({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
function renderSide(ui: ReactElement) {
  return render(ui, { wrapper: Providers });
}

/**
 * ★ 2026-11-22（汐瑶 · 收壳 P0-b）：侧栏默认**收起**。
 * 本文件多数用例测的是「栏里有什么」，不是「默认是什么」——
 * 所以显式把该侧置为展开态，别让默认值变化把内容用例连坐。
 */
function expand(side: "left" | "right"): void {
  localStorage.setItem(SIDE_COLLAPSE_KEY[side], "0");
}

function collapse(side: "left" | "right"): void {
  localStorage.setItem(SIDE_COLLAPSE_KEY[side], "1");
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
    // 默认空 providers；K5 等用例自行覆盖 mockImplementation
    vi.mocked(api.get).mockResolvedValue({ providers: [] });
  });
  afterEach(() => {
    cleanup();
    unregisterContributions("demo-side");
  });

  it("K1 · 默认收起 + 折叠态持久化（刷新保持）", () => {
    const { container, rerender } = renderSide(<DesktopSidebar side="left" />);
    const expanded = () =>
      container
        .querySelector("[data-testid='desktop-sidebar-left']")
        ?.getAttribute("aria-expanded");
    // ★ 收壳后的新默认：不存过 = 收起（主墙是主区）
    expect(expanded()).toBe("false");
    // 展开 → 键写 0
    fireEvent.click(screen.getByLabelText("展开左侧栏"));
    expect(expanded()).toBe("true");
    expect(localStorage.getItem(SIDE_COLLAPSE_KEY.left)).toBe("0");
    // 再收起 → 键写 1；重渲染（模拟刷新）保持收起
    fireEvent.click(screen.getByLabelText("收起左侧栏"));
    expect(expanded()).toBe("false");
    expect(localStorage.getItem(SIDE_COLLAPSE_KEY.left)).toBe("1");
    rerender(<DesktopSidebar side="left" />);
    expect(expanded()).toBe("false");
  });

  it("K1b · 旧键（alongside.dock-left.collapsed=0 展开）不再生效", () => {
    // 反证「改了看不见」的根因已被切断：旧快照里的展开值不许把新默认拉回去。
    localStorage.setItem("alongside.dock-left.collapsed", "0");
    const { container } = renderSide(<DesktopSidebar side="left" />);
    expect(
      container
        .querySelector("[data-testid='desktop-sidebar-left']")
        ?.getAttribute("aria-expanded"),
    ).toBe("false");
  });

  it("K2 · 收起态可由把手展开（把手与 aria 一致）", () => {
    // ★ 实况更正：`.desktop-sidebar.is-collapsed` 是 display:none，收起后整栏连同把手
    //   一起不可见，找回入口是罗盘（Desktop 的收放罗盘）/ 命令面板。
    //   所以这里钉住的是「展开态下把手存在且能收起」，不谎称折叠态仍有可见把手。
    expand("left");
    const { container } = renderSide(<DesktopSidebar side="left" />);
    expect(container.querySelector(".desktop-sidebar__toggle")).toBeTruthy();
    expect(screen.getByLabelText("收起左侧栏")).toBeTruthy();
  });

  it("K4 · 折叠态 Escape **不**展开（Esc 是关浮层的通用键，不许顺带撑开侧栏）", () => {
    collapse("left");
    const { container } = renderSide(<DesktopSidebar side="left" />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(
      container
        .querySelector("[data-testid='desktop-sidebar-left']")
        ?.getAttribute("aria-expanded"),
    ).toBe("false");
    expect(localStorage.getItem(SIDE_COLLAPSE_KEY.left)).toBe("1");
  });

  it("K5 · 右栏内置今日摘要（行清单 = BFF providers 动态派生 + 计数）", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === "/api/v1/summary/today") {
        return {
          providers: [
            { id: "calendar", label: "日程", status: "ok", data: { items: [{ text: "x" }] } },
            { id: "todo", label: "待办", status: "ok", data: { items: [] } },
            { id: "diary", label: "日记", status: "ok", data: { items: [] } },
            { id: "review", label: "复盘", status: "ok", data: { items: [] } },
          ],
        };
      }
      return { providers: [] };
    });
    expand("right");
    const { container } = renderSide(<DesktopSidebar side="right" />);
    expect(container.querySelector("[data-testid='desktop-today-summary']")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("待办")).toBeTruthy());
    expect(screen.getByText("复盘")).toBeTruthy();
    // ★ 计数位与 providers 一一对应（拿不到时显示 '—'，不阻塞入口可用性）
    //   testid 用索引（中性），不在 kernel 测试里引入业务词。
    expect(screen.getAllByTestId(/^today-count-/)).toHaveLength(4);
  });

  it("K6 · 无贡献零渲染不占位（左栏无贡献时只有框架无卡）", () => {
    expand("left");
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
    expand("left");
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
    expand("right");
    const { container } = renderSide(<DesktopSidebar side="right" />);
    expect(container.querySelector("[data-testid='demo-right-card']")).toBeTruthy();
    expect(screen.getByText("右栏卡")).toBeTruthy();
  });
});
