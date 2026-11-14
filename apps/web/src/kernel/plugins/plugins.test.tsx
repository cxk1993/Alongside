import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ComponentType } from "react";

import { SlotHost } from "../slots/SlotHost";
import {
  clearContributions,
  contributionsForSlot,
  registerContributions,
  unregisterContributions,
} from "../slots/contributions";
import { PluginProvider } from "./PluginContext";
import { manifestToModule, syncPluginsToStore, __resetPluginSyncTracking } from "./PluginRegistry";
import { requestPluginListRefresh } from "./pluginListSignal";
import { makeWorkspace, useDesktopStore } from "../store";
import type { PluginInfo, SlotName } from "./types";

function resetStore(): void {
  localStorage.clear();
  useDesktopStore.setState({
    modules: {},
    windows: [],
    workspaces: [makeWorkspace(1)],
    activeWorkspaceId: "ws1",
  });
  // ★ 插槽贡献注册表是模块级 Map，必须一起清 —— 否则上一个测试登记的组件会残留
  //   到下一个测试里（实测踩过：报错组件盖住本测试组件，表现为"找不到文本"）。
  clearContributions();
}

function registerDemo(id: string, slots: SlotName[]): void {
  useDesktopStore.getState().registerModule({
    id,
    name: id,
    version: "0.1.0",
    kind: "builtin",
    entry: "",
    window: { w: 400, h: 300 },
    slots,
  });
}

describe("前端插件框架（T14）", () => {
  beforeEach(() => {
    resetStore();
  });
  afterEach(() => {
    cleanup();
    unregisterContributions("demo");
    unregisterContributions("rogue");
    unregisterContributions("boom");
  });

  it("SlotHost 渲染某扩展点的贡献组件（UI 实际显示卡片）", () => {
    registerDemo("demo", ["dashboard.card"]);
    registerContributions("demo", [
      { slot: "dashboard.card", pluginId: "demo", component: () => <div>你好，卡片</div> },
    ]);
    render(<SlotHost slot="dashboard.card" />);
    expect(screen.getByText("你好，卡片")).toBeTruthy();
  });

  it("插件禁用后其扩展点贡献不再渲染（启用态随 store 实时变化）", () => {
    registerDemo("demo", ["dashboard.card"]);
    registerContributions("demo", [
      { slot: "dashboard.card", pluginId: "demo", component: () => <div>你好，卡片</div> },
    ]);
    expect(contributionsForSlot("dashboard.card")).toHaveLength(1);
    render(<SlotHost slot="dashboard.card" />);
    expect(screen.getByText("你好，卡片")).toBeTruthy();

    cleanup();
    useDesktopStore.getState().disableModule("demo");
    expect(contributionsForSlot("dashboard.card")).toHaveLength(0);
    render(<SlotHost slot="dashboard.card" />);
    expect(screen.queryByText("你好，卡片")).toBeNull();
  });

  it("贡献声明了未出现在 manifest.slots 的扩展点时被忽略（越权不挂载）", () => {
    registerDemo("rogue", ["desktop.widget"]);
    registerContributions("rogue", [
      { slot: "dashboard.card", pluginId: "rogue", component: () => <div>越权卡片</div> },
    ]);
    expect(contributionsForSlot("dashboard.card")).toHaveLength(0);
  });

  it("插件组件抛错被 PluginBoundary 兜住，桌面不白屏", () => {
    registerDemo("boom", ["dashboard.card"]);
    registerContributions("boom", [
      {
        slot: "dashboard.card",
        pluginId: "boom",
        component: () => {
          throw new Error("炸了");
        },
      },
    ]);
    render(<SlotHost slot="dashboard.card" />);
    expect(screen.getByText(/渲染失败/)).toBeTruthy();
  });

  it("syncPluginsToStore 把后端插件同步进桌面 store（Dock 可读到、禁用即置灰）", () => {
    const plugins: PluginInfo[] = [
      {
        id: "a",
        name: "甲",
        version: "0.1.0",
        kind: "builtin",
        enabled: true,
        source: "builtin",
        manifest: {
          id: "a",
          name: "甲",
          version: "0.1.0",
          kind: "builtin",
          entry: "",
          window: { w: 480, h: 320 },
          slots: [],
        },
      },
      {
        id: "b",
        name: "乙",
        version: "0.1.0",
        kind: "third-party",
        enabled: false,
        source: "third-party",
        manifest: {
          id: "b",
          name: "乙",
          version: "0.1.0",
          kind: "third-party",
          entry: "",
          window: { w: 480, h: 320 },
          slots: [],
        },
      },
    ];
    syncPluginsToStore(plugins);
    expect(useDesktopStore.getState().modules["a"]?.enabled).toBe(true);
    expect(useDesktopStore.getState().modules["b"]?.enabled).toBe(false);
  });

  it("PluginProvider 拉取插件并挂载插槽贡献（集成：UI 实际显示 + 注册进 store）", async () => {
    const fakeClient = {
      get: vi.fn(async () => ({
        plugins: [
          {
            id: "demo",
            name: "演示",
            version: "0.1.0",
            kind: "builtin",
            enabled: true,
            source: "builtin",
            manifest: {
              id: "demo",
              name: "演示",
              version: "0.1.0",
              kind: "builtin",
              entry: "@/apps/demo",
              window: { w: 480, h: 320 },
              slots: ["dashboard.card"],
            },
          },
        ],
      })),
      post: vi.fn(async () => ({ ok: true })),
    };
    const fakeLoad = async () =>
      ({ default: { slots: { "dashboard.card": () => <div>Provider 卡片</div> } } }) as unknown as {
        default: { slots?: Partial<Record<SlotName, ComponentType>> };
      };

    render(
      <PluginProvider client={fakeClient as never} loadEntry={fakeLoad}>
        <SlotHost slot="dashboard.card" />
      </PluginProvider>,
    );

    await waitFor(() => expect(screen.getByText("Provider 卡片")).toBeTruthy());
    // 贡献确实进了插槽注册表（不是靠恰好在别的路径上渲染出来的）
    expect(contributionsForSlot("dashboard.card")).toHaveLength(1);
    // 插件已注册进桌面 store（Dock 即可显示/置灰）
    expect(useDesktopStore.getState().modules["demo"]).toBeTruthy();
    expect(fakeClient.get).toHaveBeenCalledWith("/api/v1/plugins");
  });

  // ── ISSUE-012 防御──
  // 后端 list_plugins() 曾漏掉 manifest 字段：前端必须「缺 manifest 不崩」，
  // 且不能把贡献注册整体打断（历史上会让 E5 sidecar / D1 卡片全部挂不上）。

  it("manifestToModule 遇到缺 manifest 的项给安全默认值（不抛 TypeError）", () => {
    const ghost = {
      id: "ghost",
      name: "幽灵",
      version: "0.1.0",
      kind: "builtin" as const,
      enabled: true,
      source: "builtin" as const,
    } as unknown as PluginInfo;
    expect(() => manifestToModule(ghost)).not.toThrow();
    const mod = manifestToModule(ghost);
    expect(mod.entry).toBe("");
    expect(mod.window).toEqual({ w: 640, h: 420 });
    expect(mod.slots).toEqual([]);
  });

  it("syncPluginsToStore 中间夹缺 manifest 项不崩（两翼正常注册、幽灵项被跳过）", () => {
    const withManifest = (id: string, enabled: boolean): PluginInfo => ({
      id,
      name: id,
      version: "0.1.0",
      kind: "builtin",
      enabled,
      source: "builtin",
      manifest: {
        id,
        name: id,
        version: "0.1.0",
        kind: "builtin",
        entry: "",
        window: { w: 480, h: 320 },
        slots: [],
      },
    });
    const ghost = { id: "ghost", name: "幽灵" } as unknown as PluginInfo;
    expect(() =>
      syncPluginsToStore([withManifest("a", true), ghost, withManifest("c", false)]),
    ).not.toThrow();
    expect(useDesktopStore.getState().modules["a"]?.enabled).toBe(true);
    expect(useDesktopStore.getState().modules["c"]?.enabled).toBe(false);
    // 幽灵项没进 store（Dock 不出现无入口模块）
    expect(useDesktopStore.getState().modules["ghost"]).toBeUndefined();
  });

  it("PluginProvider 清单含缺 manifest 项 → 不崩、不置 error、有 manifest 的贡献照常挂载", async () => {
    const fakeClient = {
      get: vi.fn(async () => ({
        plugins: [
          {
            id: "demo",
            name: "演示",
            version: "0.1.0",
            kind: "builtin",
            enabled: true,
            source: "builtin",
            manifest: {
              id: "demo",
              name: "演示",
              version: "0.1.0",
              kind: "builtin",
              entry: "@/apps/demo",
              window: { w: 480, h: 320 },
              slots: ["dashboard.card"],
            },
          },
          // ★ ISSUE-012 场景：后端曾漏 manifest —— 前端不得因此整体崩
          {
            id: "ghost",
            name: "幽灵",
            version: "0.1.0",
            kind: "builtin",
            enabled: true,
            source: "builtin",
          },
        ],
      })),
      post: vi.fn(async () => ({ ok: true })),
    };
    const fakeLoad = async () =>
      ({ default: { slots: { "dashboard.card": () => <div>防御后仍挂载</div> } } }) as unknown as {
        default: { slots?: Partial<Record<SlotName, ComponentType>> };
      };

    render(
      <PluginProvider client={fakeClient as never} loadEntry={fakeLoad}>
        <SlotHost slot="dashboard.card" />
      </PluginProvider>,
    );

    await waitFor(() => expect(screen.getByText("防御后仍挂载")).toBeTruthy());
    expect(contributionsForSlot("dashboard.card")).toHaveLength(1);
    expect(useDesktopStore.getState().modules["demo"]).toBeTruthy();
    // 幽灵项被跳过、不进 store
    expect(useDesktopStore.getState().modules["ghost"]).toBeUndefined();
  });

  // ── r5：「安装/卸载后不刷新页面可见」的触发通道 ──────────────────────────
  // 后端当前不发插件生命周期事件（emits=[]），故走内核刷新信号：管理面变更后 poke，
  // PluginProvider 订阅到就重取清单 → store 同步 → Dock 出现/置灰，全程不刷新页面。

  it("★ r5：内核刷新信号驱动 PluginProvider 重取清单（不刷新页面）", async () => {
    const fakeClient = {
      get: vi.fn(async () => ({ plugins: [] })),
      post: vi.fn(async () => ({ ok: true })),
    };
    render(
      <PluginProvider client={fakeClient as never} loadEntry={async () => ({ default: {} })}>
        <div />
      </PluginProvider>,
    );
    await waitFor(() => expect(fakeClient.get).toHaveBeenCalledTimes(1));

    // 模拟管理面（plugins/packs 页）在装/卸/启停成功后请求重取
    await act(async () => {
      requestPluginListRefresh();
    });
    await waitFor(() => expect(fakeClient.get).toHaveBeenCalledTimes(2));
    expect(fakeClient.get).toHaveBeenCalledWith("/api/v1/plugins");
  });

  it("刷新信号无订阅者时静默 no-op（管理页可独立渲染，不依赖 provider）", () => {
    expect(() => requestPluginListRefresh()).not.toThrow();
  });

  it("★ r5：卸载（清单里消失）⇒ 从 store 注销并关掉它的窗口（Dock 图标消失）", () => {
    __resetPluginSyncTracking();
    const mk = (id: string): PluginInfo => ({
      id,
      name: id,
      version: "0.1.0",
      kind: "third-party",
      enabled: true,
      source: "third-party",
      manifest: {
        id,
        name: id,
        version: "0.1.0",
        kind: "third-party",
        entry: `/plugin-assets/${id}/index.js`,
        window: { w: 400, h: 300 },
        slots: [],
      },
    });
    syncPluginsToStore([mk("keep"), mk("gone")]);
    expect(useDesktopStore.getState().modules["gone"]).toBeTruthy();
    useDesktopStore.getState().openWindow("gone");
    expect(useDesktopStore.getState().windows.some((w) => w.moduleId === "gone")).toBe(true);

    // 卸载：后端清单里不再有 gone（仍在清单里的 keep 不受影响）
    syncPluginsToStore([mk("keep")]);
    expect(useDesktopStore.getState().modules["gone"]).toBeUndefined();
    expect(useDesktopStore.getState().windows.some((w) => w.moduleId === "gone")).toBe(false);
    expect(useDesktopStore.getState().modules["keep"]).toBeTruthy();
    __resetPluginSyncTracking();
  });
});
