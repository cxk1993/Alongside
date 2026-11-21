import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import App from "../App";
import { registerModules } from "@/kernel/ModuleRegistry";
import { makeWorkspace, useDesktopStore, type DesktopState } from "@/kernel/store";
import { setToken } from "@/shared/api/client";
import type { ModuleManifest } from "@/kernel/types";

// ★ 2026-09-27（hermes）：桌面右栏「今日摘要」拉内核 BFF（useQuery），
//   整桌面渲染需 QueryClientProvider（与 main.tsx 一致）。
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
function renderApp() {
  return render(
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>,
  );
}

const mods: ModuleManifest[] = [
  {
    id: "mod-alpha",
    name: "演示模块 · 甲",
    version: "0.1.0",
    kind: "builtin",
    entry: "@mocks/alpha",
    window: { w: 600, h: 400 },
  },
  {
    id: "mod-beta",
    name: "演示模块 · 乙",
    version: "0.1.0",
    kind: "builtin",
    entry: "@mocks/beta",
    window: { w: 600, h: 400 },
  },
  {
    id: "mod-gamma",
    name: "演示模块 · 丙",
    version: "0.1.0",
    kind: "builtin",
    entry: "@mocks/gamma",
    window: { w: 600, h: 400 },
  },
  {
    id: "mod-delta",
    name: "演示模块 · 丁",
    version: "0.1.0",
    kind: "builtin",
    entry: "@mocks/delta",
    window: { w: 600, h: 400, singleton: true },
  },
];

beforeEach(() => {
  localStorage.clear();
  // ★ T30：桌面壳有鉴权门 —— 本文件测的是登录后的桌面行为，先种 token
  setToken("test-token");
  useDesktopStore.setState({
    modules: {},
    windows: [],
    workspaces: [makeWorkspace(1)],
    activeWorkspaceId: "ws1",
    // ★ T17：Dock 默认隐藏（主人 22:27）。**本文件测的就是 Dock 本身**（图标/点击/单例），
    //   所以这里显式展开；Dock 隐藏与命令面板的默认态由 kernel/commandPalette.test.tsx 钉住。
    bottombarCollapsed: false,
  } satisfies Partial<DesktopState> as unknown as DesktopState);
});

describe("桌面集成（mock 模块）", () => {
  it("坞上渲染 4 个模块图标", () => {
    registerModules(mods);
    renderApp();
    expect(screen.getByLabelText("演示模块 · 甲")).toBeTruthy();
    expect(screen.getByLabelText("演示模块 · 乙")).toBeTruthy();
    expect(screen.getByLabelText("演示模块 · 丙")).toBeTruthy();
    expect(screen.getByLabelText("演示模块 · 丁")).toBeTruthy();
  });

  it("点击坞图标开一扇窗", () => {
    registerModules(mods);
    renderApp();
    fireEvent.click(screen.getByLabelText("演示模块 · 甲"));
    expect(useDesktopStore.getState().windows).toHaveLength(1);
    expect(useDesktopStore.getState().windows[0].moduleId).toBe("mod-alpha");
  });

  it("单例模块重复点击不重复开窗（只聚焦）", () => {
    registerModules(mods);
    renderApp();
    fireEvent.click(screen.getByLabelText("演示模块 · 丁"));
    fireEvent.click(screen.getByLabelText("演示模块 · 丁"));
    const opened = useDesktopStore.getState().windows.filter((w) => w.moduleId === "mod-delta");
    expect(opened).toHaveLength(1);
  });
});
