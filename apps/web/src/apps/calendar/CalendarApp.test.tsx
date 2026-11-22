/**
 * 渲染冒烟测试：验证日程表主组件、Inspector、QuickCard 在无真实网络时
 * 能正常挂载渲染（数据为空 → 走空态分支，不应抛错）。
 *
 * 注意：涉及真实 SSE / 拖拽 fps / 与后端联调的验收项，环境里没有真服务，
 * 一律标记为「需人工验证」，不在此用 mock 冒充。
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CalendarApp } from "./CalendarApp";
import { Inspector } from "./inspector/Inspector";
import { QuickCard } from "./inspector/QuickCard";
import type { CalendarEvent } from "./api";

// useCalendarEvents 内部会建 QueryClient + 挂 SSE；这里用最简桩避免真实网络。
vi.mock("./hooks/useCalendarEvents", () => ({
  useCalendarEvents: () => ({
    events: [],
    isLoading: false,
    error: null,
    updateEvent: { mutate: () => {} },
    createEvent: { mutate: () => {} },
    deleteEvent: { mutate: () => {} },
    addChild: { mutate: () => {} },
    updateChild: { mutate: () => {} },
    deleteChild: { mutate: () => {} },
  }),
}));

const ev: CalendarEvent = {
  id: "e1",
  title: "测试事项",
  color: "var(--accent)",
  start_at: "2025-01-06T09:00:00.000+08:00", // 周一
  end_at: "2025-01-06T10:30:00.000+08:00",
  all_day: false,
  span_days: 1,
  source: "manual",
  parent_id: null,
  sort: 0,
  location: null,
  note: null,
  children: [],
};

describe("CalendarApp 冒烟", () => {
  it("挂载不报错，且显示工具栏视图切换", () => {
    render(<CalendarApp />);
    expect(screen.getByText("日")).toBeTruthy();
    expect(screen.getByText("周")).toBeTruthy();
    expect(screen.getByText("月")).toBeTruthy();
  });

  it("工具栏显示日期标题（默认周视图 → 含年月日）", () => {
    render(<CalendarApp />);
    const title = screen.getByTestId("cal-title");
    expect(title.textContent).toMatch(/\d{4}年\d{1,2}月/);
  });
});

describe("Inspector 空态", () => {
  it("无选中时显示提示", () => {
    render(
      <Inspector
        selected={null}
        parentOfSelected={null}
        onPatch={() => {}}
        onPatchChild={() => {}}
        onAddChild={() => {}}
        onDelete={() => {}}
        onDeleteChild={() => {}}
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText(/选中一个事项/)).toBeTruthy();
  });
});

describe("QuickCard 交互", () => {
  it("改名回调触发", () => {
    const onRename = vi.fn();
    render(
      <QuickCard
        event={ev}
        onClose={() => {}}
        onRename={onRename}
        onColor={() => {}}
        onFocus={() => {}}
        onAddChild={() => {}}
        onDelete={() => {}}
      />,
    );
    const btn = screen.getByText("改名");
    fireEvent.click(btn);
    expect(onRename).toBeCalledWith("测试事项");
  });
});
