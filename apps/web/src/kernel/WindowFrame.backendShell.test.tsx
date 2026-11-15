/**
 * r8 · N2 「纯 API 插件的入口可达性」—— 真渲染验证（jsdom，不是读代码觉得对）。
 *
 * 背景（PR#5 校园五插件验收发现）：
 *   yuketang / welearn 是纯 API 插件（entry=""、api.base 存在）。它们在
 *   Dock / DesktopSideNav 里以正常按钮出现，点击 openWindow —— 用户点任何
 *   图标必须「要么打开真东西，要么看不到它，不许打开一个内部错误」。
 *   WindowFrame 对 entry="" 的处理落在 ModulePlaceholder 的 backend 薄壳，
 *   r8 起薄壳升级为「诚实说明 + 打开插件管理」的可行动出口。
 *
 * 判据：
 *   ① entry="" + api.base → 薄壳为「已接入（后端服务）」，不是「未找到插件入口」；
 *   ② 薄壳文案说明「纯 API 插件：无桌面窗口」并指路（AI 对话 / 插件管理）；
 *   ③ 有模块提供 plugin.manage 能力时，薄壳出现「打开插件管理」按钮，
 *      点击 openWindow 到那个模块（能力发现，内核不硬编码插件 id）；
 *   ④ 无提供者 / 提供者被停用 → 按钮不渲染（降级为纯文案，不炸）；
 *   ⑤ entry="" 且**无** api.base → 落「清单缺少 entry 字段」诚实报因。
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";

import { WindowFrame } from "./WindowFrame";
import { DEFAULT_WORKSPACE_ID, makeWorkspace, useDesktopStore, type WindowState } from "./store";
import type { ModuleManifest } from "./types";

const API_ONLY: ModuleManifest = {
  id: "api-only",
  name: "纯API插件",
  version: "0.1.0",
  kind: "third-party",
  entry: "",
  window: { w: 720, h: 560 },
  api: { base: "/api/v1/api-only" },
} as unknown as ModuleManifest;

const NO_API: ModuleManifest = {
  id: "no-api",
  name: "无API",
  version: "0.1.0",
  kind: "third-party",
  entry: "",
  window: { w: 400, h: 300 },
} as unknown as ModuleManifest;

const MANAGER: ModuleManifest = {
  id: "mgr",
  name: "插件管理",
  version: "0.1.0",
  kind: "core",
  entry: "@/apps/none-mgr",
  provides: ["plugin.manage"],
  window: { w: 400, h: 300 },
} as unknown as ModuleManifest;

function seed(modules: Record<string, { manifest: ModuleManifest; enabled: boolean }>): void {
  const w: WindowState = {
    instanceId: "api-only#1",
    moduleId: "api-only",
    z: 11,
    minimized: false,
    maximized: false,
    geo: { x: 0, y: 48, w: 720, h: 560 },
    pinned: false,
    fixedGeometry: false,
    workspaceId: DEFAULT_WORKSPACE_ID,
  };
  useDesktopStore.setState({
    modules,
    windows: [w],
    workspaces: [makeWorkspace(1, { topZ: 11, seq: 1 })],
    activeWorkspaceId: DEFAULT_WORKSPACE_ID,
  });
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("WindowFrame · 纯 API 插件的诚实薄壳（r8 N2）", () => {
  it("① entry='' + api.base → 薄壳「已接入（后端服务）」，绝不出现「未找到插件入口」", async () => {
    seed({ "api-only": { manifest: API_ONLY, enabled: true } });
    render(<WindowFrame instanceId="api-only#1" />);
    const el = await waitFor(() => {
      const e = document.querySelector(".win__placeholder");
      if (!e) throw new Error("placeholder not ready");
      return e;
    });
    expect(el.textContent).toContain("已接入（后端服务）");
    expect(el.textContent).not.toContain("未找到插件入口");
    expect(el.textContent).not.toContain("尚未接入");
  });

  it("② 薄壳说明「纯 API 插件：无桌面窗口」并指路 AI 对话 / 插件管理", async () => {
    seed({ "api-only": { manifest: API_ONLY, enabled: true } });
    render(<WindowFrame instanceId="api-only#1" />);
    const el = await waitFor(() => {
      const e = document.querySelector(".win__placeholder");
      if (!e) throw new Error("placeholder not ready");
      return e;
    });
    expect(el.textContent).toContain("纯 API 插件");
    expect(el.textContent).toContain("没有桌面窗口界面");
    expect(el.textContent).toContain("AI 对话");
    expect(el.textContent).toContain("插件管理");
    expect(el.textContent).toContain("/api/v1/api-only");
  });

  it("③ 有 plugin.manage 提供者 → 「打开插件管理」按钮出现，点击 openWindow 到它", async () => {
    seed({
      "api-only": { manifest: API_ONLY, enabled: true },
      mgr: { manifest: MANAGER, enabled: true },
    });
    const spy = vi
      .spyOn(useDesktopStore.getState(), "openWindow")
      .mockImplementation(() => {});
    render(<WindowFrame instanceId="api-only#1" />);
    const btn = await waitFor(() => {
      const e = document.querySelector(
        "[data-testid='placeholder-open-plugin-manager']",
      ) as HTMLButtonElement | null;
      if (!e) throw new Error("button not ready");
      return e;
    });
    fireEvent.click(btn);
    expect(spy).toHaveBeenCalledWith("mgr");
    spy.mockRestore();
  });

  it("④ 提供者存在但被停用 → 按钮降级不渲染（薄壳仍诚实说明）", async () => {
    seed({
      "api-only": { manifest: API_ONLY, enabled: true },
      mgr: { manifest: MANAGER, enabled: false },
    });
    render(<WindowFrame instanceId="api-only#1" />);
    await waitFor(() => {
      if (!document.querySelector(".win__placeholder")) throw new Error("not ready");
    });
    expect(document.querySelector("[data-testid='placeholder-open-plugin-manager']")).toBeNull();
    expect(document.querySelector(".win__placeholder")?.textContent).toContain("已接入（后端服务）");
  });

  it("⑤ entry='' 且无 api.base → 诚实落「清单缺少 entry 字段」（不当薄壳粉饰）", async () => {
    const w: WindowState = {
      instanceId: "no-api#1",
      moduleId: "no-api",
      z: 11,
      minimized: false,
      maximized: false,
      geo: { x: 0, y: 48, w: 400, h: 300 },
      pinned: false,
      fixedGeometry: false,
      workspaceId: DEFAULT_WORKSPACE_ID,
    };
    seed({ "no-api": { manifest: NO_API, enabled: true } });
    useDesktopStore.setState({ windows: [w] });
    render(<WindowFrame instanceId="no-api#1" />);
    const el = await waitFor(() => {
      const e = document.querySelector(".win__placeholder");
      if (!e) throw new Error("not ready");
      return e;
    });
    expect(el.textContent).toContain("清单缺少 entry 字段");
  });
});
