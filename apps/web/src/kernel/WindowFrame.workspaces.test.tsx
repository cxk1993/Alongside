/**
 * T23 · 多工作区的 **DOM 层**验证（真渲染 jsdom，不是"读代码觉得对"）。
 *
 * 为什么这层不能省：
 *   卡的最硬一条是「**切换工作区不许重载 iframe**」（会丢登录态）。
 *   store 层只能证"没动 `windows` 数组"（机制前提）；
 *   而"**React 没有重挂载这个窗口**"必须在 DOM 上证 —— 见下 §1 的**节点引用不变**断言。
 *   iframe 是否重载直接取决于节点有没有被销毁重建，所以节点 identity 就是最贴近的证据。
 */
import { describe, it, expect, beforeEach } from "vitest";
import { act, render, fireEvent } from "@testing-library/react";

import { WindowFrame } from "./WindowFrame";
import { DEFAULT_WORKSPACE_ID, makeWorkspace, useDesktopStore, type WindowState } from "./store";
import type { ModuleManifest } from "./types";

const MANIFEST: ModuleManifest = {
  id: "m1",
  name: "测试窗",
  version: "0.1.0",
  kind: "builtin",
  entry: "@/apps/__t23_none__",
  window: { w: 800, h: 600, minW: 360, minH: 240 },
};

function seedOne(ok = 1): void {
  const base: WindowState = {
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
    modules: { m1: { manifest: MANIFEST, enabled: true } },
    windows: [base],
    workspaces: [makeWorkspace(1), makeWorkspace(2)],
    activeWorkspaceId: ok === 1 ? DEFAULT_WORKSPACE_ID : "ws2",
  });
}

const node = () => document.querySelector(".win") as HTMLElement | null;

beforeEach(() => {
  localStorage.clear();
  seedOne(1);
});

describe("WindowFrame · T23 工作区", () => {
  it("★ 验收#6：非当前工作区的窗**仍在 DOM 里**，只是被隐藏（不是被摘掉）", () => {
    seedOne(2); // 激活 ws2，而窗口在 ws1
    render(<WindowFrame instanceId="m1#1" />);
    const el = node();
    expect(el).not.toBeNull(); // ★ 节点还在 —— 卸载的话这里会是 null
    expect(el?.className).toContain("win--hidden");
  });

  it("★ 验收#6 最强证据：**切换工作区前后是同一个 DOM 节点**（React 没重挂载 → iframe 不会重载）", async () => {
    render(<WindowFrame instanceId="m1#1" />);
    const before = node();
    expect(before?.className).not.toContain("win--hidden");

    // ★ 外部 store 的更新要包 act()，否则 React 不会同步刷新（不是实现问题，是测试写法）
    await act(async () => {
      useDesktopStore.getState().switchWorkspace("ws2");
    });
    const afterHide = node();
    expect(afterHide).toBe(before); // ★ 同一个节点引用
    expect(afterHide?.className).toContain("win--hidden");

    await act(async () => {
      useDesktopStore.getState().switchWorkspace(DEFAULT_WORKSPACE_ID);
    });
    const afterBack = node();
    expect(afterBack).toBe(before); // ★ 还是同一个
    expect(afterBack?.className).not.toContain("win--hidden");
  });

  it("当前工作区的窗不带隐藏类", () => {
    render(<WindowFrame instanceId="m1#1" />);
    expect(node()?.className).not.toContain("win--hidden");
  });

  it("★ 标题栏右键 → 出现「移到工作区…」菜单，只列**其它**工作区", () => {
    render(<WindowFrame instanceId="m1#1" />);
    expect(document.querySelector(".win__menu")).toBeNull(); // 默认不显示

    fireEvent.contextMenu(document.querySelector(".win__bar")!);
    const menu = document.querySelector(".win__menu");
    expect(menu).not.toBeNull();
    expect(menu?.textContent).toContain("移到工作区…");
    // 窗口在 ws1 → 只应列出副屏「AI 工作面」（默认第二张桌子的名字）
    expect(menu?.textContent).toContain("AI 工作面");
    expect(menu?.textContent).not.toContain("工作区 1");
  });

  it("★ 点菜单项 → 真把窗口移过去（workspaceId 变了）", () => {
    render(<WindowFrame instanceId="m1#1" />);
    fireEvent.contextMenu(document.querySelector(".win__bar")!);
    fireEvent.click(document.querySelector(".win__menu-item")!);
    expect(useDesktopStore.getState().windows[0].workspaceId).toBe("ws2");
    expect(document.querySelector(".win__menu")).toBeNull(); // 选完关闭
  });

  it("没有其它工作区时给出提示，不是空菜单", () => {
    useDesktopStore.setState({ workspaces: [makeWorkspace(1)] });
    render(<WindowFrame instanceId="m1#1" />);
    fireEvent.contextMenu(document.querySelector(".win__bar")!);
    expect(document.querySelector(".win__menu-empty")?.textContent).toContain("还没有别的工作区");
  });

  it("点遮罩关闭菜单", () => {
    render(<WindowFrame instanceId="m1#1" />);
    fireEvent.contextMenu(document.querySelector(".win__bar")!);
    expect(document.querySelector(".win__menu")).not.toBeNull();
    fireEvent.pointerDown(document.querySelector(".win__menu-veil")!);
    expect(document.querySelector(".win__menu")).toBeNull();
  });
});
