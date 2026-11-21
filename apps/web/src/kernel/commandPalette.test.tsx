import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { Desktop } from "./Desktop";
import { makeWorkspace, useDesktopStore } from "./store";
import { setToken } from "@/shared/api/client";
import type { ModuleManifest } from "./types";

/**
 * T17 · 命令面板接线测试（真渲染 Desktop，不走 mock 组件）。
 *
 * 这里验的是**接线**而不是纯函数（纯函数在 commands.test.ts）：
 *   a. Dock 默认不占位（T17 主张）；
 *   b. Ctrl+K 出的是命令面板：分组标题 + 模块行都在；
 *   c. 输入 "dock" 回车 → Dock 真回来（这是 Dock 隐藏后的找回路径，必须可用）；
 *   d. 无匹配时是空态文案，不是空白框。
 *
 * 注：这里**不**断言"回车开窗"——那会触发模块入口的 lazy 加载（jsdom 里没有真实
 * entry），断在假信号上没意义；模块命令的 run 已在 commands.test.ts 断言 openModule 被调用。
 */

function mod(id: string, name: string): { manifest: ModuleManifest; enabled: boolean } {
  return {
    manifest: {
      id,
      name,
      kind: "builtin",
      entry: `@/apps/${id}`,
      icon: "grid",
      window: { w: 640, h: 420 },
    } as ModuleManifest,
    enabled: true,
  };
}

function renderDesktop() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Desktop />
    </QueryClientProvider>,
  );
}

describe("T17 · Dock 隐藏 + 命令面板接线", () => {
  beforeEach(() => {
    localStorage.clear();
    setToken("test-token");
    useDesktopStore.setState({
      modules: { notes: mod("notes", "笔记") },
      windows: [],
      workspaces: [makeWorkspace(1)],
      activeWorkspaceId: "ws1",
      bottombarCollapsed: true,
      topbarCollapsed: false,
    });
  });

  it("默认不渲染 Dock，但 Dock 组件本身没被删（toggle 一下就回来）", () => {
    renderDesktop();
    expect(screen.queryByRole("toolbar", { name: "模块坞" })).toBeNull();
    // 退路没断：store 里同一个开关把 Dock 叫回来
    act(() => useDesktopStore.getState().toggleBottombar());
    expect(screen.getByRole("toolbar", { name: "模块坞" })).toBeTruthy();
  });

  it("Ctrl+K → 命令面板：有分组标题，模块行可检索", () => {
    renderDesktop();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const dialog = screen.getByRole("dialog", { name: "全局搜索" });
    expect(dialog).toBeTruthy();
    expect(screen.getByRole("group", { name: "模块" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "动作" })).toBeTruthy();

    const input = screen.getByPlaceholderText("搜索或输入命令…");
    fireEvent.change(input, { target: { value: "笔记" } });
    const rows = screen.getAllByTestId("command-item");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toBe("笔记");
  });

  it("输入 dock 回车 → Dock 真回来（隐藏后的找回路径）", () => {
    renderDesktop();
    expect(screen.queryByRole("toolbar", { name: "模块坞" })).toBeNull();

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = screen.getByPlaceholderText("搜索或输入命令…");
    fireEvent.change(input, { target: { value: "dock" } });
    const rows = screen.getAllByTestId("command-item");
    expect(rows[0].textContent).toBe("显示 Dock（模块坞）");

    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.queryByRole("dialog", { name: "全局搜索" })).toBeNull();
    expect(useDesktopStore.getState().bottombarCollapsed).toBe(false);
    expect(screen.getByRole("toolbar", { name: "模块坞" })).toBeTruthy();
  });

  it("无匹配 → 空态文案（不是空白框）", () => {
    renderDesktop();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = screen.getByPlaceholderText("搜索或输入命令…");
    fireEvent.change(input, { target: { value: "zzz-none" } });
    expect(screen.queryAllByTestId("command-item")).toHaveLength(0);
    expect(screen.getByText("没有匹配的命令或模块")).toBeTruthy();
  });
});