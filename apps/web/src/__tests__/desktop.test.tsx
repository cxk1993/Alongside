import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  /**
   * ★ ISSUE-017 回归（2026-11-22）：`modules.json` 退役后的 hydrate 两条硬前提。
   *   ① modules 为空时 hydrate 不许跑（否则 `reg && reg.enabled` 会把窗口全过滤掉 = 刷新丢布局）；
   *   ② 模块首次非空后只 hydrate 一次（插件启停会让 modules 换引用，无守卫会把桌面重置回快照）。
   */
  it("ISSUE-017：等模块非空才 hydrate，且只跑一次（插件 refresh 不弹回窗口）", async () => {
    // 造一份持久化快照：注册 → 开窗 → 设几何（写入 localStorage）
    const st = useDesktopStore.getState();
    st.registerModule(mods[0]);
    st.openWindow("mod-alpha");
    const id = useDesktopStore.getState().windows[0].instanceId;
    st.setGeo(id, { x: 120, y: 160 });

    // 模拟刷新：内存清空（保留 localStorage）
    useDesktopStore.setState({
      modules: {},
      windows: [],
      workspaces: [makeWorkspace(1)],
      activeWorkspaceId: "ws1",
    });

    renderApp();
    // modules 空 → 不许恢复任何窗口
    expect(useDesktopStore.getState().windows).toHaveLength(0);

    // 后端清单回来（modules 非空）→ 恢复一次
    act(() => registerModules(mods));
    await waitFor(() => expect(useDesktopStore.getState().windows).toHaveLength(1));
    expect(useDesktopStore.getState().windows[0].geo.x).toBe(120);

    // 用户拖窗（新的几何）；随后插件启停触发 modules 引用变化 → 不许重置回快照
    act(() => {
      useDesktopStore.getState().setGeo(id, { x: 300, y: 220 });
    });
    act(() => registerModules(mods));
    expect(useDesktopStore.getState().windows[0].geo.x).toBe(300);
  });
});
