import { beforeEach, describe, expect, it } from "vitest";

import { activeWorkspace, makeWorkspace, useDesktopStore, type DesktopState } from "@/kernel/store";
import type { ModuleManifest } from "@/kernel/types";

const alpha: ModuleManifest = {
  id: "mod-alpha",
  name: "甲",
  version: "0.1.0",
  kind: "builtin",
  entry: "@mocks/alpha",
  window: { w: 600, h: 400, minW: 360, minH: 240 },
};

const delta: ModuleManifest = {
  id: "mod-delta",
  name: "丁",
  version: "0.1.0",
  kind: "builtin",
  entry: "@mocks/delta",
  window: { w: 600, h: 400, minW: 360, minH: 240, singleton: true },
};

const STORE_KEY = "alongside.windows.v1";

function reset(): void {
  localStorage.clear();
  useDesktopStore.setState({
    modules: {},
    windows: [],
    workspaces: [makeWorkspace(1)],
    activeWorkspaceId: "ws1",
  } satisfies Partial<DesktopState> as unknown as DesktopState);
}

describe("窗口系统 store", () => {
  beforeEach(reset);

  it("开窗：注册后 openWindow 生成一扇窗口，几何取自 manifest", () => {
    useDesktopStore.getState().registerModule(alpha);
    useDesktopStore.getState().openWindow("mod-alpha");
    const s = useDesktopStore.getState();
    expect(s.windows).toHaveLength(1);
    expect(s.windows[0].moduleId).toBe("mod-alpha");
    expect(s.windows[0].geo.w).toBe(600);
    expect(s.windows[0].geo.h).toBe(400);
  });

  it("聚焦：z-index 递增，最后聚焦的窗口在最前", () => {
    const st = useDesktopStore.getState();
    st.registerModule(alpha);
    st.openWindow("mod-alpha"); // z=11
    st.openWindow("mod-alpha"); // 非单例，再开一扇 z=12
    const ids = useDesktopStore.getState().windows.map((w) => w.instanceId);
    expect(ids).toHaveLength(2);
    useDesktopStore.getState().focusWindow(ids[0]);
    const s = useDesktopStore.getState();
    const top = s.windows.reduce((a, b) => (b.z > a.z ? b : a));
    expect(top.instanceId).toBe(ids[0]);
    expect(top.z).toBe(activeWorkspace(s).topZ);
  });

  it("关闭：closeWindow 移除对应窗口", () => {
    const st = useDesktopStore.getState();
    st.registerModule(alpha);
    st.openWindow("mod-alpha");
    const id = useDesktopStore.getState().windows[0].instanceId;
    st.closeWindow(id);
    expect(useDesktopStore.getState().windows).toHaveLength(0);
  });

  it("单例：singleton 模块重复开窗只聚焦，不重复", () => {
    const st = useDesktopStore.getState();
    st.registerModule(delta);
    st.openWindow("mod-delta");
    st.openWindow("mod-delta");
    const s = useDesktopStore.getState();
    expect(s.windows.filter((w) => w.moduleId === "mod-delta")).toHaveLength(1);
    // 第二次点应该把它提到最前
    expect(s.windows[0].z).toBe(activeWorkspace(s).topZ);
  });

  it("最小化 / 还原", () => {
    const st = useDesktopStore.getState();
    st.registerModule(alpha);
    st.openWindow("mod-alpha");
    const id = useDesktopStore.getState().windows[0].instanceId;
    st.minimizeWindow(id);
    expect(useDesktopStore.getState().windows[0].minimized).toBe(true);
    st.restoreWindow(id);
    expect(useDesktopStore.getState().windows[0].minimized).toBe(false);
  });

  it("最大化：记住 _prev 几何，还原后回到原位置尺寸", () => {
    const st = useDesktopStore.getState();
    st.registerModule(alpha);
    st.openWindow("mod-alpha");
    const id = useDesktopStore.getState().windows[0].instanceId;
    const before = { ...useDesktopStore.getState().windows[0].geo };
    st.toggleMaximize(id);
    const maxed = useDesktopStore.getState().windows[0];
    expect(maxed.maximized).toBe(true);
    expect(maxed._prev).toEqual(before);
    expect(maxed.geo.w).toBe(window.innerWidth);
    st.toggleMaximize(id);
    const restored = useDesktopStore.getState().windows[0];
    expect(restored.maximized).toBe(false);
    expect(restored.geo).toEqual(before);
  });

  it("几何持久化：开窗并移动后写入 localStorage，hydrate 可恢复", () => {
    const st = useDesktopStore.getState();
    st.registerModule(alpha);
    st.openWindow("mod-alpha");
    const id = useDesktopStore.getState().windows[0].instanceId;
    st.setGeo(id, { x: 120, y: 160 });

    const raw = localStorage.getItem(STORE_KEY);
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw as string);
    const saved = parsed.windows.find((w: { instanceId: string }) => w.instanceId === id);
    expect(saved.geo.x).toBe(120);
    expect(saved.geo.y).toBe(160);

    // 模拟刷新：清空内存，再从 localStorage 恢复
    useDesktopStore.setState({
      windows: [],
      workspaces: [makeWorkspace(1)],
      activeWorkspaceId: "ws1",
    });
    useDesktopStore.getState().hydrate();
    const after = useDesktopStore.getState().windows.find((w) => w.instanceId === id);
    expect(after).toBeDefined();
    expect(after?.geo.x).toBe(120);
    expect(after?.geo.y).toBe(160);
  });

  it("禁用模块：开窗被忽略（坞上禁用即不可开）", () => {
    const st = useDesktopStore.getState();
    st.registerModule(alpha);
    st.disableModule("mod-alpha");
    st.openWindow("mod-alpha");
    expect(useDesktopStore.getState().windows).toHaveLength(0);
  });
});
