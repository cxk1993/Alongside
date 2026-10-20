/**
 * T23 · 多工作区内核单测。
 *
 * 覆盖卡里点名的四件事：
 *   ① 切换**隔离性**（在 A 开/聚焦/置顶，B 的 z 序不受影响）
 *   ② **旧数据迁移**（v1 单工作区快照 → 整体落「工作区 1」，零丢失）
 *   ③ **置顶层 per-workspace**（两区计数器互不污染）
 *   ④ **iframe 保活**的机制前提：切换工作区**不动 `windows` 数组引用**（→ 组件不卸载）
 *
 * 「切过去的窗还在 DOM 里、只是被隐藏」是组件行为，见 `WindowFrame.workspaces.test.tsx`。
 */
import { describe, it, expect, beforeEach } from "vitest";

import type { ModuleManifest } from "./types";
import {
  DEFAULT_WORKSPACE_ID,
  PIN_BASE,
  activeWorkspace,
  makeWorkspace,
  useDesktopStore,
  zIndexOf,
  type WindowState,
} from "./store";

const STORE_KEY = "alongside.windows.v1";

const MANIFEST: ModuleManifest = {
  id: "m1",
  name: "M1",
  version: "0.1.0",
  kind: "builtin",
  entry: "@/apps/m1",
  window: { w: 800, h: 600, minW: 360, minH: 240 },
};

function win(over: Partial<WindowState> & { instanceId: string }): WindowState {
  return {
    moduleId: "m1",
    z: 10,
    minimized: false,
    maximized: false,
    geo: { x: 0, y: 48, w: 800, h: 600 },
    pinned: false,
    fixedGeometry: false,
    workspaceId: DEFAULT_WORKSPACE_ID,
    ...over,
  };
}

function resetStore(windows: WindowState[] = []) {
  useDesktopStore.setState({
    modules: { m1: { manifest: MANIFEST, enabled: true } },
    windows,
    workspaces: [makeWorkspace(1)],
    activeWorkspaceId: DEFAULT_WORKSPACE_ID,
  });
}

beforeEach(() => {
  localStorage.clear();
  resetStore();
});

// ───────────────────────── 基础 ─────────────────────────
describe("工作区生命周期", () => {
  it("默认一个「工作区 1」", () => {
    const s = useDesktopStore.getState();
    expect(s.workspaces).toHaveLength(1);
    expect(s.workspaces[0].name).toBe("工作区 1");
    expect(s.activeWorkspaceId).toBe(DEFAULT_WORKSPACE_ID);
  });

  it("新建工作区 → 自动切过去，名字是「工作区 2」（内核零业务）", () => {
    const id = useDesktopStore.getState().createWorkspace();
    const s = useDesktopStore.getState();
    expect(s.workspaces).toHaveLength(2);
    expect(s.activeWorkspaceId).toBe(id);
    expect(s.workspaces[1].name).toBe("工作区 2");
  });

  it("切到不存在的工作区 → 忽略（不崩、不改状态）", () => {
    useDesktopStore.getState().switchWorkspace("ws-does-not-exist");
    expect(useDesktopStore.getState().activeWorkspaceId).toBe(DEFAULT_WORKSPACE_ID);
  });

  it("新开的窗口落在**当前**工作区", () => {
    const wsB = useDesktopStore.getState().createWorkspace();
    useDesktopStore.getState().openWindow("m1");
    const s = useDesktopStore.getState();
    expect(s.windows).toHaveLength(1);
    expect(s.windows[0].workspaceId).toBe(wsB);
  });
});

// ───────────────────────── ① 隔离性 ─────────────────────────
describe("★ 验收#3：两区 z 序完全隔离", () => {
  it("在 A 开窗 / 聚焦 / 置顶，B 的 topZ 与窗口 z 一动不动", () => {
    // A 里先放一扇窗
    resetStore([win({ instanceId: "A1", z: 11, workspaceId: DEFAULT_WORKSPACE_ID })]);
    const wsB = useDesktopStore.getState().createWorkspace();
    useDesktopStore.getState().openWindow("m1"); // 开在 B
    const bWin = useDesktopStore.getState().windows.find((w) => w.workspaceId === wsB)!;
    const bBefore = { z: bWin.z, topZ: activeWorkspace(useDesktopStore.getState()).topZ };

    // 切回 A 一顿操作
    useDesktopStore.getState().switchWorkspace(DEFAULT_WORKSPACE_ID);
    useDesktopStore.getState().openWindow("m1");
    useDesktopStore.getState().focusWindow("A1");
    useDesktopStore.getState().setPinned("A1", true);

    const st = useDesktopStore.getState();
    const wsBafter = st.workspaces.find((w) => w.id === wsB)!;
    const bWinAfter = st.windows.find((w) => w.instanceId === bWin.instanceId)!;

    expect(bWinAfter.z).toBe(bBefore.z); // ★ B 的窗口 z 没动
    expect(wsBafter.topZ).toBe(bBefore.topZ); // ★ B 的计数器没动
    expect(wsBafter.topPinZ).toBe(0); // ★ B 的置顶层没被 A 污染
  });

  it("instanceId 跨工作区**全局唯一**（否则 React key 与查找都会撞）", () => {
    useDesktopStore.getState().openWindow("m1");
    useDesktopStore.getState().createWorkspace();
    useDesktopStore.getState().openWindow("m1");
    const ids = useDesktopStore.getState().windows.map((w) => w.instanceId);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("singleton 模块只在**本工作区**去重（两区可各开一扇）", () => {
    useDesktopStore.setState({
      modules: {
        m1: {
          manifest: { ...MANIFEST, window: { ...MANIFEST.window, singleton: true } },
          enabled: true,
        },
      },
    });
    useDesktopStore.getState().openWindow("m1");
    useDesktopStore.getState().createWorkspace();
    useDesktopStore.getState().openWindow("m1");
    expect(useDesktopStore.getState().windows).toHaveLength(2); // 每区各一扇
  });

  it("整理桌面只动当前工作区", () => {
    resetStore([
      win({ instanceId: "A1", z: 11, geo: { x: 500, y: 300, w: 800, h: 600 } }),
      win({ instanceId: "B1", z: 12, workspaceId: "ws2", geo: { x: 500, y: 300, w: 800, h: 600 } }),
    ]);
    useDesktopStore.setState({
      workspaces: [makeWorkspace(1), makeWorkspace(2)],
    });
    const before = useDesktopStore.getState().windows.find((w) => w.instanceId === "B1")!.geo;
    useDesktopStore.getState().tidyDesktop();
    const after = useDesktopStore.getState().windows.find((w) => w.instanceId === "B1")!.geo;
    expect(after).toEqual(before); // ★ B 的几何没被整理动过
  });
});

// ───────────────────────── ③ 置顶层 per-workspace ─────────────────────────
describe("★ 置顶层 per-workspace", () => {
  it("A 的置顶不改变 B 的 topPinZ，且两区各自的置顶序独立", () => {
    resetStore([win({ instanceId: "A1", z: 11 })]);
    const wsB = useDesktopStore.getState().createWorkspace();
    useDesktopStore.getState().openWindow("m1");

    useDesktopStore.getState().setPinned(useDesktopStore.getState().windows[1].instanceId, true);
    useDesktopStore.getState().switchWorkspace(DEFAULT_WORKSPACE_ID);
    useDesktopStore.getState().setPinned("A1", true);
    useDesktopStore.getState().setPinned("A1", false);

    const st = useDesktopStore.getState();
    const wsA = st.workspaces.find((w) => w.id === DEFAULT_WORKSPACE_ID)!;
    const bAfter = st.workspaces.find((w) => w.id === wsB)!;
    // A 置了又取消 → A 的 topPinZ 留在 1；B 从头到尾是 1
    expect(wsA.topPinZ).toBe(1);
    expect(bAfter.topPinZ).toBe(1);
    // B 里那扇仍是置顶且 pinZ=1（A 的操作没碰它）
    const bWin = st.windows.find((w) => w.workspaceId === wsB)!;
    expect(bWin.pinned).toBe(true);
    expect(bWin.pinZ).toBe(1);
    expect(zIndexOf(bWin)).toBe(PIN_BASE + 1);
  });
});

// ───────────────────────── ⑤ 移动窗口 ─────────────────────────
describe("★ 验收#1/#2：移到工作区 B", () => {
  it("几何与置顶状态**原样带走**；切回 A 时它不在 A", () => {
    resetStore([
      win({ instanceId: "A1", z: 11, geo: { x: 120, y: 200, w: 900, h: 500 } }),
      win({ instanceId: "A2", z: 12, geo: { x: 40, y: 60, w: 400, h: 300 } }),
    ]);
    useDesktopStore.getState().setPinned("A1", true);
    const wsB = useDesktopStore.getState().createWorkspace();

    const before = useDesktopStore.getState().windows.find((w) => w.instanceId === "A1")!;
    useDesktopStore.getState().moveWindowToWorkspace("A1", wsB);
    const after = useDesktopStore.getState().windows.find((w) => w.instanceId === "A1")!;

    expect(after.workspaceId).toBe(wsB);
    expect(after.geo).toEqual(before.geo); // ★ 几何原样
    expect(after.pinned).toBe(true); // ★ 置顶原样
    expect(after.pinZ).toBe(before.pinZ);
    expect(after.z).toBe(before.z);

    // ★ 验收 #2：切回 A，它不在 A；A 里另一扇布局不变
    useDesktopStore.getState().switchWorkspace(DEFAULT_WORKSPACE_ID);
    const inA = useDesktopStore
      .getState()
      .windows.filter((w) => w.workspaceId === DEFAULT_WORKSPACE_ID);
    expect(inA.map((w) => w.instanceId)).toEqual(["A2"]);
    expect(inA[0].geo).toEqual({ x: 40, y: 60, w: 400, h: 300 });
  });

  it("移到不存在的工作区 / 移到原地 → 不动", () => {
    resetStore([win({ instanceId: "A1", z: 11 })]);
    useDesktopStore.getState().moveWindowToWorkspace("A1", "ws-nope");
    expect(useDesktopStore.getState().windows[0].workspaceId).toBe(DEFAULT_WORKSPACE_ID);
    useDesktopStore.getState().moveWindowToWorkspace("A1", DEFAULT_WORKSPACE_ID);
    expect(useDesktopStore.getState().windows).toHaveLength(1);
  });
});

// ───────────────────────── ② 迁移 ─────────────────────────
describe("★ 验收#5：v1 旧快照迁移", () => {
  it("旧单工作区快照 → 全部窗口落「工作区 1」，零丢失，计数器接续", () => {
    localStorage.setItem(
      STORE_KEY,
      JSON.stringify({
        // v1 形状：顶层有计数器、窗口里没有 workspaceId
        windows: [
          {
            instanceId: "legacy#1",
            moduleId: "m1",
            z: 11,
            minimized: false,
            maximized: false,
            geo: { x: 0, y: 48, w: 800, h: 600 },
          },
          {
            instanceId: "legacy#2",
            moduleId: "m1",
            z: 12,
            minimized: false,
            maximized: false,
            geo: { x: 40, y: 80, w: 800, h: 600 },
            pinned: true,
            pinZ: 3,
          },
        ],
        topZ: 12,
        topPinZ: 3,
        seq: 2,
        enabled: { m1: true },
      }),
    );
    resetStore();
    useDesktopStore.getState().hydrate();

    const st = useDesktopStore.getState();
    expect(st.windows).toHaveLength(2); // ★ 一个窗口都没丢
    expect(st.windows.every((w) => w.workspaceId === DEFAULT_WORKSPACE_ID)).toBe(true);
    expect(st.workspaces).toHaveLength(1);
    expect(st.workspaces[0].name).toBe("工作区 1");
    expect(st.workspaces[0].topZ).toBe(12); // 原样接续
    expect(st.workspaces[0].topPinZ).toBe(3);
    expect(st.workspaces[0].seq).toBe(2);
    expect(st.activeWorkspaceId).toBe(DEFAULT_WORKSPACE_ID);
    // 置顶状态也带过来了
    expect(st.windows.find((w) => w.instanceId === "legacy#2")!.pinned).toBe(true);
  });

  it("v2 快照往返：工作区列表 / 当前区 / 计数器全部恢复", () => {
    useDesktopStore.getState().createWorkspace();
    const wsB = useDesktopStore.getState().activeWorkspaceId;
    useDesktopStore.getState().openWindow("m1");
    useDesktopStore.getState().setPinned(useDesktopStore.getState().windows[0].instanceId, true);

    const snapshot = {
      active: useDesktopStore.getState().activeWorkspaceId,
      workspaces: useDesktopStore.getState().workspaces.map((w) => ({ ...w })),
      windows: useDesktopStore.getState().windows.map((w) => ({ ...w })),
    };

    resetStore(); // 模拟刷新
    useDesktopStore.getState().hydrate();

    const st = useDesktopStore.getState();
    expect(st.workspaces).toHaveLength(2);
    expect(st.activeWorkspaceId).toBe(snapshot.active);
    expect(st.activeWorkspaceId).toBe(wsB);
    expect(st.windows).toEqual(snapshot.windows);
    expect(st.workspaces).toEqual(snapshot.workspaces);
  });

  it("窗口指向一个**不存在**的工作区（快照被手改过）→ 退回「工作区 1」，不悬空", () => {
    localStorage.setItem(
      STORE_KEY,
      JSON.stringify({
        windows: [win({ instanceId: "x#1", z: 11, workspaceId: "ws-ghost" })],
        workspaces: [makeWorkspace(1)],
        activeWorkspaceId: "ws-ghost",
        enabled: { m1: true },
      }),
    );
    resetStore();
    useDesktopStore.getState().hydrate();
    const st = useDesktopStore.getState();
    expect(st.windows[0].workspaceId).toBe(DEFAULT_WORKSPACE_ID); // ★ 不悬空
    expect(st.activeWorkspaceId).toBe(DEFAULT_WORKSPACE_ID); // ★ 当前区也回退到存在的那个
  });

  it("空 workspaces 数组 → 视为 v1，照样迁进「工作区 1」", () => {
    localStorage.setItem(
      STORE_KEY,
      JSON.stringify({
        windows: [win({ instanceId: "x#1", z: 11 })],
        workspaces: [],
        enabled: { m1: true },
      }),
    );
    resetStore();
    useDesktopStore.getState().hydrate();
    const st = useDesktopStore.getState();
    expect(st.workspaces).toHaveLength(1);
    expect(st.windows).toHaveLength(1);
  });
});

// ───────────────────────── ④ iframe 保活的机制前提 ─────────────────────────
describe("★ 验收#6：切换工作区必须**不动 `windows` 数组**（组件不卸载的前提）", () => {
  it("switchWorkspace 前后，`windows` 数组与元素**引用完全不变**", () => {
    resetStore([win({ instanceId: "A1", z: 11 })]);
    const wsB = useDesktopStore.getState().createWorkspace();

    const arrBefore = useDesktopStore.getState().windows;
    const elBefore = arrBefore[0];

    useDesktopStore.getState().switchWorkspace(DEFAULT_WORKSPACE_ID);
    const arrMid = useDesktopStore.getState().windows;
    expect(arrMid).toBe(arrBefore); // ★ 同一个数组引用
    expect(arrMid[0]).toBe(elBefore); // ★ 同一个元素引用

    useDesktopStore.getState().switchWorkspace(wsB);
    const arrAfter = useDesktopStore.getState().windows;
    expect(arrAfter).toBe(arrBefore); // ★ 切来切去还是它
    expect(arrAfter[0]).toBe(elBefore);
  });

  it("切换工作区**不产生任何窗口增删**（只改 activeWorkspaceId）", () => {
    resetStore([win({ instanceId: "A1", z: 11 })]);
    const wsB = useDesktopStore.getState().createWorkspace();
    const len = useDesktopStore.getState().windows.length;
    useDesktopStore.getState().switchWorkspace(DEFAULT_WORKSPACE_ID);
    useDesktopStore.getState().switchWorkspace(wsB);
    expect(useDesktopStore.getState().windows).toHaveLength(len);
  });
});
