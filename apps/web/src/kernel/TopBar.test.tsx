/**
 * P0 · 去桌面化：顶栏不许再有「窗口管理」按钮。
 *
 * 为什么补这条（此前 TopBar **零测试覆盖**，是整个前端最热的一个组件）：
 *   2026-09-24/25 加进来的 `▁ 最小化全部窗口`、`⊞ 整理桌面` 两个 icon-btn 是
 *   Windows 桌面隐喻的余党（浮窗层叠 / 平铺管理）。路线改成「领土分区（区块模型）」后
 *   它们没有语义，但**没有断言** ⇒ 删了会被人「顺手加回来」，或换个 class 复活。
 *
 * 这条钉子只钉**规则**，不钉实现细节：
 *   1. 浏览态 `.topbar__actions` 里 icon-btn 恰好 2 个（主题 / 登出）；
 *   2. 全顶栏不含 `整理桌面` / `最小化全部窗口` 两个可达名字（含 title 与 aria-label）；
 *   3. 时钟仍在（`topbar__clock`）—— 它是今日摘要面板的唯一入口（U2），不许被一起删掉。
 *
 * 注意：store 动作（`tidyDesktop` / `minimizeAllUnpinned`）与命令面板条目本轮**保留**
 * （比丢 lane，随浮窗模型退役），所以这里**不**断言 store 里没有它们 —— 那是另一件事。
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** 取数层 mock：这条测试只关心「顶栏还剩哪些按钮」，不要真去打 8013。 */
vi.mock("@/shared/api/now", () => ({
  useSightNow: () => ({ status: "missing", data: undefined }),
}));
vi.mock("./shell/summaryToday", async () => {
  const actual = await vi.importActual<typeof import("./shell/summaryToday")>(
    "./shell/summaryToday",
  );
  return { ...actual, useSummaryToday: () => ({ data: undefined }) };
});

import { TopBar } from "./TopBar";
import { DEFAULT_WORKSPACE_ID, useDesktopStore } from "./store";

function renderTopBar(props: Partial<Parameters<typeof TopBar>[0]> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TopBar onOpenSearch={() => {}} onLogout={() => {}} {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // 单测共享 zustand 单例：显式给一个干净的工作区集合，别依赖别的测试遗留。
  useDesktopStore.setState({
    workspaces: [{ id: DEFAULT_WORKSPACE_ID, name: "工作区 1" } as never],
    activeWorkspaceId: DEFAULT_WORKSPACE_ID,
    modules: {},
    windows: [],
  });
});

describe("TopBar · P0 去桌面化", () => {
  it("右上角 icon-btn 恰好 2 个（主题 / 登出），没有窗口管理按钮", () => {
    const { container } = renderTopBar();
    const iconBtns = container.querySelectorAll(".topbar__actions .icon-btn");
    expect(iconBtns.length).toBe(2);

    const labels = Array.from(iconBtns).map(
      (b) => b.getAttribute("aria-label") ?? "",
    );
    expect(labels.some((l) => l.includes("主题") || l.includes("亮色") || l.includes("暗色"))).toBe(true);
    expect(labels).toContain("登出");
  });

  it("「整理桌面」「最小化全部窗口」在顶栏不可达 —— 名字、title、aria-label 都不许出现", () => {
    const { container } = renderTopBar();
    const topbar = container.querySelector(".topbar")!;
    const html = topbar.outerHTML;
    expect(html).not.toContain("整理桌面");
    expect(html).not.toContain("最小化全部窗口");
    // 字形也不行：换 class / 换实现悄悄复活，字形仍是桌面隐喻
    expect(html).not.toContain("⊞");
    expect(html).not.toContain("▁");
    expect(screen.queryByLabelText("整理桌面")).toBeNull();
    expect(screen.queryByLabelText("最小化全部窗口")).toBeNull();
  });

  it("时钟保留（今日摘要面板的唯一入口，删了摘要整个失联）", () => {
    const { container } = renderTopBar();
    expect(container.querySelector(".topbar__clock")).not.toBeNull();
  });

  it("未传 onLogout 时不渲染登出（内核不强制鉴权）—— 只剩主题 1 个", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={qc}>
        <TopBar onOpenSearch={() => {}} />
      </QueryClientProvider>,
    );
    expect(container.querySelectorAll(".topbar__actions .icon-btn").length).toBe(1);
  });
});
