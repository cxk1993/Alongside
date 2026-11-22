/**
 * P2 样板钉子：底座组件只负责「结构 + 三种状态」，不吞业务。
 * 任何一条破了，31 个 app 的迁移就会各自长回一套。
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppEmpty, AppErrorState, AppLoading, AppShell, AppToolbar } from "./AppShell";

describe("shared/ui/app-shell", () => {
  it("AppShell 保留应用自己的根 class（旧皮肤不被覆盖）", () => {
    render(
      <AppShell className="todo-root" testId="shell">
        <span>内容</span>
      </AppShell>,
    );
    const root = screen.getByTestId("shell");
    expect(root.className).toContain("app-shell");
    expect(root.className).toContain("todo-root");
    expect(screen.getByText("内容")).toBeTruthy();
  });

  it("AppToolbar 左/右分区，且是 toolbar 角色", () => {
    render(<AppToolbar left={<span>左</span>} right={<span>右</span>} />);
    expect(screen.getByRole("toolbar")).toBeTruthy();
    expect(screen.getByText("左")).toBeTruthy();
    expect(screen.getByText("右")).toBeTruthy();
  });

  it("三种状态互不冒充：失败有重试、加载有 aria-live、空态不冒充错误", () => {
    const retry = vi.fn();
    render(<AppErrorState error={new Error("boom")} onRetry={retry} />);
    expect(screen.getByRole("alert").textContent).toContain("boom");
    screen.getByText("重试").click();
    expect(retry).toHaveBeenCalledTimes(1);

    render(<AppLoading />);
    expect(screen.getByRole("status")).toBeTruthy();

    render(<AppEmpty text="还没有待办" hint="按 + 新建" />);
    expect(screen.getByText("还没有待办")).toBeTruthy();
    expect(screen.getByText("按 + 新建")).toBeTruthy();
  });
});
