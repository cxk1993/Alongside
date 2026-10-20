/**
 * T22 · 窗口能力（置顶 + 固定几何）内核单测。
 *
 * 覆盖卡里点名的四件事：
 *   ① 两段 Z 序（置顶序稳定、两层不混算）
 *   ② 「当前窗」判定按层（closeTopmost / cycleWindow 的共用规则）
 *   ③ 持久化恢复 + **旧 localStorage 向后兼容**
 *   ④ 置顶 / 固定几何 两个 action 的正确性
 *
 * 固定几何"干净不挂 hook"是组件行为，在 `apps/web/src/apps/web/WebApp.test.tsx`
 * 与真实浏览器验收里覆盖（这里做 store 层）。
 */
import { describe, it, expect, beforeEach } from "vitest";

import type { ModuleManifest } from "./types";
import {
  DEFAULT_WORKSPACE_ID,
  PIN_BASE,
  makeWorkspace,
  normalStack,
  topmostNormalId,
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
  window: { w: 800, h: 600 },
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
    workspaces: [makeWorkspace(1, { topZ: 10 })],
    activeWorkspaceId: DEFAULT_WORKSPACE_ID,
  });
}

beforeEach(() => {
  localStorage.clear();
  resetStore();
});

// ───────────────────────── ① 两段 Z 序 ─────────────────────────
describe("zIndexOf —— 两段 Z 序，两层不混算", () => {
  it("普通窗的渲染 zIndex 就是它的 z", () => {
    expect(zIndexOf(win({ instanceId: "a", z: 37 }))).toBe(37);
  });

  it("置顶窗的 zIndex 落在 [PIN_BASE, …)", () => {
    expect(zIndexOf(win({ instanceId: "a", z: 37, pinned: true, pinZ: 2 }))).toBe(PIN_BASE + 2);
  });

  it("★ 任何置顶窗都盖过任何普通窗（即使普通窗的 z 更大）", () => {
    const normal = win({ instanceId: "n", z: 999 });
    const pinned = win({ instanceId: "p", z: 10, pinned: true, pinZ: 1 });
    expect(zIndexOf(pinned)).toBeGreaterThan(zIndexOf(normal));
  });

  it("置顶窗缺 pinZ 时安全降级（落在置顶层底部，不产生 NaN）", () => {
    expect(zIndexOf(win({ instanceId: "p", pinned: true }))).toBe(PIN_BASE);
  });
});

// ───────────────────────── ④ 两个 action ─────────────────────────
describe("setPinned —— 置顶 / 取消置顶", () => {
  it("置顶：取 topPinZ+1，后钉的在上（相对顺序稳定）", () => {
    resetStore([win({ instanceId: "a", z: 11 }), win({ instanceId: "b", z: 12 })]);
    const s = useDesktopStore.getState();
    s.setPinned("a", true);
    s.setPinned("b", true);

    const st = useDesktopStore.getState();
    const a = st.windows.find((w) => w.instanceId === "a")!;
    const b = st.windows.find((w) => w.instanceId === "b")!;
    expect(a.pinZ).toBe(1);
    expect(b.pinZ).toBe(2);
    expect(st.workspaces[0].topPinZ).toBe(2);
    // ★ 验收 #5：置顶层的相对顺序可预期
    expect(zIndexOf(b)).toBeGreaterThan(zIndexOf(a));
  });

  it("★ 聚焦普通窗不会打乱置顶层的顺序（置顶窗仍盖着它）", () => {
    resetStore([win({ instanceId: "a", z: 11 }), win({ instanceId: "n", z: 12 })]);
    useDesktopStore.getState().setPinned("a", true);
    useDesktopStore.getState().focusWindow("n"); // 普通窗被提到最前

    const st = useDesktopStore.getState();
    const a = st.windows.find((w) => w.instanceId === "a")!;
    const n = st.windows.find((w) => w.instanceId === "n")!;
    expect(zIndexOf(a)).toBeGreaterThan(zIndexOf(n));
  });

  it("取消置顶：回普通层，并把 z 提到最前（不沉到别的窗后面）", () => {
    resetStore([win({ instanceId: "a", z: 11 }), win({ instanceId: "n", z: 20 })]);
    // 与 store 惯例保持一致：topZ 恒 ≥ 任何窗的 z（每次赋值都是 topZ+1）
    useDesktopStore.setState({ workspaces: [makeWorkspace(1, { topZ: 20 })] });
    useDesktopStore.getState().setPinned("a", true);
    useDesktopStore.getState().setPinned("a", false);

    const a = useDesktopStore.getState().windows.find((w) => w.instanceId === "a")!;
    const n = useDesktopStore.getState().windows.find((w) => w.instanceId === "n")!;
    expect(a.pinned).toBe(false);
    expect(a.pinZ).toBeUndefined();
    expect(a.z).toBeGreaterThan(n.z);
  });
});

describe("setFixedGeometry", () => {
  it("开 / 关都只改标记", () => {
    resetStore([win({ instanceId: "a", z: 11 })]);
    useDesktopStore.getState().setFixedGeometry("a", true);
    expect(useDesktopStore.getState().windows[0].fixedGeometry).toBe(true);
    useDesktopStore.getState().setFixedGeometry("a", false);
    expect(useDesktopStore.getState().windows[0].fixedGeometry).toBe(false);
  });
});

// ───────────────────────── ② 「当前窗」判定 ─────────────────────────
describe("topmostNormalId / normalStack —— 置顶窗不参与", () => {
  const windows = [
    win({ instanceId: "n1", z: 11 }),
    win({ instanceId: "p1", z: 99, pinned: true, pinZ: 1 }),
    win({ instanceId: "n2", z: 12 }),
  ];

  it("★ 取普通层里 z 最高的，跳过置顶窗（否则「关当前窗」会关错人）", () => {
    expect(topmostNormalId(windows)).toBe("n2");
  });

  it("normalStack 只含普通层且按 z 升序", () => {
    expect(normalStack(windows).map((w) => w.instanceId)).toEqual(["n1", "n2"]);
  });

  it("全是置顶窗时返回 null（没有「当前窗」可言，快捷键应静默）", () => {
    expect(topmostNormalId([win({ instanceId: "p", pinned: true, pinZ: 1 })])).toBeNull();
  });
});

// ───────────────────────── ③ 持久化与向后兼容 ─────────────────────────
describe("hydrate —— 持久化恢复与旧数据兼容", () => {
  it("往返：置顶 / 固定几何 / 置顶顺序全部恢复", () => {
    resetStore([win({ instanceId: "a", z: 11 }), win({ instanceId: "b", z: 12 })]);
    useDesktopStore.getState().setPinned("a", true);
    useDesktopStore.getState().setFixedGeometry("b", true);

    // 模拟刷新：清空内存态后重新 hydrate
    resetStore();
    useDesktopStore.getState().hydrate();

    const st = useDesktopStore.getState();
    const a = st.windows.find((w) => w.instanceId === "a")!;
    const b = st.windows.find((w) => w.instanceId === "b")!;
    expect(a.pinned).toBe(true);
    expect(a.pinZ).toBe(1);
    expect(b.fixedGeometry).toBe(true);
    expect(st.workspaces[0].topPinZ).toBe(1);
  });

  it("★ 旧版本快照（没有 pinned / fixedGeometry / topPinZ）→ 不丢窗、不报错、字段降级为 false", () => {
    // 旧版写入的形状：窗口里没有 T22 的三个字段，顶层没有 topPinZ
    localStorage.setItem(
      STORE_KEY,
      JSON.stringify({
        windows: [
          {
            instanceId: "legacy#1",
            moduleId: "m1",
            z: 11,
            minimized: false,
            maximized: false,
            geo: { x: 0, y: 48, w: 800, h: 600 },
          },
        ],
        topZ: 11,
        seq: 1,
        enabled: { m1: true },
      }),
    );
    resetStore();
    useDesktopStore.getState().hydrate();

    const st = useDesktopStore.getState();
    expect(st.windows.length).toBe(1); // ★ 窗口没丢
    expect(st.windows[0].instanceId).toBe("legacy#1");
    expect(st.windows[0].pinned).toBe(false);
    expect(st.windows[0].fixedGeometry).toBe(false);
    expect(st.windows[0].pinZ).toBeUndefined();
    expect(st.workspaces[0].topPinZ).toBe(0); // 缺字段 → 0
  });

  it("快照损坏（windows 不是数组）→ 当作没有，不崩", () => {
    localStorage.setItem(STORE_KEY, JSON.stringify({ windows: "oops", topZ: 3 }));
    resetStore();
    expect(() => useDesktopStore.getState().hydrate()).not.toThrow();
  });
});

// ─────────────────── ★ U1-2 收放跨会话往返（hermes 立单修复） ───────────────────
// 真 bug 回放：toggle 调了 savePersisted，但快照白名单没带折叠字段 →
// localStorage 永远没写进去，reload 弹回展开态（生产 Playwright 实测）。
// 本组测试走「toggle → localStorage 序列化 → 清态 → hydrate」全链，防再发。
describe("U1-2 顶栏/底栏收放跨会话持久化", () => {
  it("★ toggleTopbar → 快照 JSON 里真的有 topbarCollapsed（序列化白名单回归钉）", () => {
    useDesktopStore.setState({ topbarCollapsed: false, bottombarCollapsed: false });
    useDesktopStore.getState().toggleTopbar();
    const raw = localStorage.getItem(STORE_KEY);
    expect(raw).toBeTruthy();
    const snap = JSON.parse(raw!);
    expect(snap.topbarCollapsed).toBe(true); // ★ 断裂点：曾经这里永远是 undefined
  });

  it("toggle → hydrate（重建态）→ 折叠保持（reload 语义）", () => {
    useDesktopStore.setState({ topbarCollapsed: false, bottombarCollapsed: false });
    useDesktopStore.getState().toggleTopbar();
    useDesktopStore.getState().toggleBottombar();
    // 模拟 reload：状态清空再从 localStorage 恢复
    useDesktopStore.setState({ topbarCollapsed: false, bottombarCollapsed: false });
    useDesktopStore.getState().hydrate();
    const st = useDesktopStore.getState();
    expect(st.topbarCollapsed).toBe(true);
    expect(st.bottombarCollapsed).toBe(true);
  });

  it("旧快照（无折叠字段）→ hydrate 不误折叠（向后兼容）", () => {
    localStorage.setItem(
      STORE_KEY,
      JSON.stringify({ windows: [], workspaces: [makeWorkspace(1)], activeWorkspaceId: DEFAULT_WORKSPACE_ID, enabled: {} }),
    );
    useDesktopStore.setState({ topbarCollapsed: false, bottombarCollapsed: false });
    useDesktopStore.getState().hydrate();
    const st = useDesktopStore.getState();
    expect(st.topbarCollapsed).toBe(false);
    expect(st.bottombarCollapsed).toBe(false);
  });
});
