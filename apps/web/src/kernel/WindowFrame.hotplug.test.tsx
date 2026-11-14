/**
 * r5-P0（G3）· WindowFrame 热插拔响应的 DOM 层验证。
 * 判据：store 里覆盖同 id 的 manifest.entry 后，该窗**重渲染并加载新入口** ——
 *       响应式读 manifest（旧 getState 快照做不到）+ 边界按 entry 加 key（清掉旧失败态）。
 * 没有这两处，「装上没生效」会是查不出的悬案。
 */
import { beforeEach, describe, expect, it } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";

import { WindowFrame } from "./WindowFrame";
import { DEFAULT_WORKSPACE_ID, makeWorkspace, useDesktopStore, type WindowState } from "./store";
import type { ModuleManifest } from "./types";

const BASE: ModuleManifest = {
  id: "m1",
  name: "热插窗",
  version: "0.1.0",
  kind: "builtin",
  entry: "@/apps/__hotplug_none__",
  window: { w: 800, h: 600, minW: 360, minH: 240 },
};

function seed(entry: string): void {
  const w: WindowState = {
    instanceId: "m1#1",
    moduleId: "m1",
    z: 11,
    minimized: false,
    maximized: false,
    geo: { x: 0, y: 48, w: 800, h: 600 },
    pinned: false,
    fixedGeometry: false,
    workspaceId: DEFAULT_WORKSPACE_ID,
  };
  useDesktopStore.setState({
    modules: { m1: { manifest: { ...BASE, entry }, enabled: true } },
    windows: [w],
    workspaces: [makeWorkspace(1, { topZ: 11, seq: 1 })],
    activeWorkspaceId: DEFAULT_WORKSPACE_ID,
  });
}

beforeEach(() => {
  localStorage.clear();
});

describe("WindowFrame · r5-P0 热插拔响应", () => {
  it("entry 由缺失换成可加载入口 → 窗口重渲染并渲染新入口（旧失败态被清掉）", async () => {
    seed("@/apps/__hotplug_none__");
    render(<WindowFrame instanceId="m1#1" />);

    // 初始：入口缺失 → 错误边界诚实占位（不白屏）
    await waitFor(() => expect(screen.getByText(/未找到插件入口/)).toBeTruthy());

    // 运行时覆盖同 id 的 manifest.entry（模拟热插拔装上/换入口）
    await act(async () => {
      useDesktopStore.getState().registerModule({ ...BASE, entry: "@mocks/alpha" });
    });

    // 新入口被加载并渲染；旧的失败占位消失
    await waitFor(() => expect(screen.getByText("演示模块 · 甲")).toBeTruthy());
    expect(screen.queryByText(/未找到插件入口/)).toBeNull();
    expect(screen.queryByText("该模块尚未接入")).toBeNull();
  });

  it("manifest 未变时不重复加载（同 entry 稳定）", async () => {
    seed("@mocks/alpha");
    render(<WindowFrame instanceId="m1#1" />);
    await waitFor(() => expect(screen.getByText("演示模块 · 甲")).toBeTruthy());
    // 触发一次无关 store 更新（聚焦），内容仍在、不闪回占位
    await act(async () => {
      useDesktopStore.getState().focusWindow("m1#1");
    });
    expect(screen.getByText("演示模块 · 甲")).toBeTruthy();
  });

  it("运行时停用该模块 → 窗口诚实显示「已停用」，不留过期界面（N4 判据）", async () => {
    seed("@mocks/alpha");
    render(<WindowFrame instanceId="m1#1" />);
    await waitFor(() => expect(screen.getByText("演示模块 · 甲")).toBeTruthy());

    await act(async () => {
      useDesktopStore.getState().disableModule("m1");
    });

    await waitFor(() => expect(screen.getByText("该模块已停用")).toBeTruthy());
    // 过期组件不再渲染（诚实消失，而不是留一张点了没反应的界面）
    expect(screen.queryByText("演示模块 · 甲")).toBeNull();
  });
});
