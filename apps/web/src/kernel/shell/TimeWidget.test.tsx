/**
 * U2 · 聚合小日历视图侧测试（判据 J1-J12）。
 * 裁决令61 丙案：前端只调 BFF /api/v1/summary/today（单请求四分区共享）；
 * provider.status 三态可区分、逐分区独立降级；day-dots/day-peek 走 dashboard。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ApiError } from "@/shared/api/client";
import { TimeWidget } from "./TimeWidget";

vi.mock("@/shared/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/shared/api/client")>("@/shared/api/client");
  return { ...actual, api: { get: vi.fn() } };
});

import { api } from "@/shared/api/client";
import { useDesktopStore } from "../store";

function wrapper(): { el: ReactNode } {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  });
  return {
    el: (
      <QueryClientProvider client={qc}>
        <TimeWidget />
      </QueryClientProvider>
    ),
  };
}

/** 与真 BFF 一致：label = manifest 名（_fetch_one 有值才放）。 */
const LABELS: Record<string, string> = { calendar: "日程", todo: "待办", diary: "日记", review: "复盘" };
const okProv = (id: string, data?: unknown) => ({
  id,
  label: LABELS[id] ?? id,
  status: "ok" as const,
  data: data ?? { title: "", items: [] },
});
const summary = (providers: unknown[]) => ({ providers });

describe("U2 · TimeWidget（聚合小日历视图侧）", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
  });
  afterEach(() => {
    cleanup();
  });

  it("J1 · 点击时钟展开面板，Esc 关闭", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) return {};
      return summary(["calendar", "todo", "diary", "review"].map((id) => okProv(id)));
    });
    const { el } = wrapper();
    const { container } = render(el);
    expect(container.querySelector("[data-testid='today-summary-panel']")).toBeNull();
    fireEvent.click(screen.getByLabelText(/当前时间/));
    expect(container.querySelector("[data-testid='today-summary-panel']")).toBeTruthy();
    // 四分区常驻
    await waitFor(() => expect(screen.getByTestId("today-sum-calendar")).toBeTruthy());
    expect(screen.getByTestId("today-sum-todo")).toBeTruthy();
    expect(screen.getByTestId("today-sum-diary")).toBeTruthy();
    expect(screen.getByTestId("today-sum-review")).toBeTruthy();
    // 月历常驻（U2 v2）
    expect(screen.getByTestId("month-grid")).toBeTruthy();
    // 单请求 BFF（裁决令61 丙案）
    expect(vi.mocked(api.get).mock.calls.map((c) => c[0])).toContain("/api/v1/summary/today");
    // Esc 关闭
    fireEvent.keyDown(document, { key: "Escape" });
    expect(container.querySelector("[data-testid='today-summary-panel']")).toBeNull();
  });

  it("J3 · 源未实现（not-implemented）→ 显示「未安装 · 去安装」占位（不消失）", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) return {};
      return summary(
        ["calendar", "todo", "diary", "review"].map((id) => ({
          id,
          label: LABELS[id] ?? id,
          status: "not-implemented" as const,
        })),
      );
    });
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getAllByText(/未安装 · 去安装/).length).toBe(4));
  });

  it("J4 · 源已装但空数据 → 空态「今日暂无X」", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) return {};
      return summary(["calendar", "todo", "diary", "review"].map((id) => okProv(id)));
    });
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getByText("今日暂无待办")).toBeTruthy());
    expect(screen.getByText("今日暂无日记")).toBeTruthy();
  });

  it("J5 · 有数据 → 渲染列表（text + 数量 + 单请求 BFF）", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) return {};
      return summary([
        okProv("calendar", { title: "今日日程", items: [{ text: "开周会 10:00", state: "info" }] }),
        okProv("todo", {
          title: "待办",
          items: [
            { text: "交房租", state: "due" },
            { text: "写周报", state: "done" },
          ],
        }),
        okProv("diary"),
        okProv("review"),
      ]);
    });
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getByText("开周会 10:00")).toBeTruthy());
    expect(screen.getByText("交房租")).toBeTruthy();
    expect(screen.getByText("写周报")).toBeTruthy();
    // 前端只调 BFF 一个端点（裁决令61 丙案：杜绝双份）
    const urls = vi.mocked(api.get).mock.calls.map((c) => c[0]);
    expect(urls).toContain("/api/v1/summary/today");
    expect(urls.some((u) => u.includes("/api/v1/todo/today-summary"))).toBe(false);
    expect(urls.some((u) => u.includes("/api/v1/calendar/today-summary"))).toBe(false);
  });

  it("J6 · 单源 unavailable → 该分区「暂时不可用」，其余分区照常（独立降级）", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) return {};
      return summary([
        okProv("calendar", { items: [{ text: "正常条目" }] }),
        okProv("todo", { items: [{ text: "正常条目" }] }),
        { id: "diary", status: "unavailable" },
        okProv("review", { items: [{ text: "正常条目" }] }),
      ]);
    });
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getAllByText("正常条目").length).toBe(3));
    await waitFor(() => expect(screen.getByTestId("today-sum-diary-err")).toBeTruthy());
    expect(screen.getAllByText("暂时不可用").length).toBe(1);
  });

  it("J6b · 分区头点击 → 打开对应插件窗口", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) return {};
      return summary(["calendar", "todo", "diary", "review"].map((id) => okProv(id)));
    });
    const openWindow = vi
      .spyOn(useDesktopStore.getState(), "openWindow")
      .mockImplementation(() => {});
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getByText("待办")).toBeTruthy());
    fireEvent.click(screen.getByText("待办"));
    expect(openWindow).toHaveBeenCalledWith("todo");
    openWindow.mockRestore();
  });

  it("J7 · 月历渲染 + day-dots 打点（今日有内容显示圆点）", async () => {
    const d = new Date();
    const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) {
        return { [today]: ["calendar", "todo"] };
      }
      return summary(["calendar", "todo", "diary", "review"].map((id) => okProv(id)));
    });
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getByTestId("month-grid")).toBeTruthy());
    await waitFor(() =>
      expect(
        screen.getByTestId(`cal-day-${today}`).querySelector(".today-sum__cal-dot--calendar.is-on"),
      ).toBeTruthy(),
    );
    const cell = screen.getByTestId(`cal-day-${today}`);
    expect(cell.querySelector(".today-sum__cal-dot--todo.is-on")).toBeTruthy();
    expect(cell.querySelector(".today-sum__cal-dot--diary.is-on")).toBeNull();
    const urls = vi.mocked(api.get).mock.calls.map((c) => c[0]);
    expect(urls.some((u) => u.startsWith("/api/v1/dashboard/day-dots?"))).toBe(true);
  });

  it("J8 · 点非今日 → 显示选中日聚合（day-peek 四源）", async () => {
    const d = new Date();
    const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const tomorrow = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate() + 1).padStart(2, "0")}`;
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) {
        return { [today]: [], [tomorrow]: ["calendar", "todo"] };
      }
      if (url.startsWith("/api/v1/dashboard/day-peek")) {
        return {
          date: tomorrow,
          marks: { calendar: 1, diary: 0, review: 0, todo_open: 1 },
          dots: { has_event: true, has_diary: false, has_review: false, has_todo: true },
          list: {
            calendar: [{ id: "c1", title: "明天开会", start: `${tomorrow}T10:00:00` }],
            todo: [{ id: "t1", title: "交房租", done: false }],
            diary: [],
            review: [],
          },
        };
      }
      return summary(["calendar", "todo", "diary", "review"].map((id) => okProv(id)));
    });
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getByTestId("month-grid")).toBeTruthy());
    fireEvent.click(screen.getByTestId(`cal-day-${tomorrow}`));
    await waitFor(() => expect(screen.getByTestId("day-peek-panel")).toBeTruthy());
    expect(screen.getByText("明天开会 · 10:00")).toBeTruthy();
    expect(screen.getByText("交房租")).toBeTruthy();
    expect(screen.getByText("当日暂无日记")).toBeTruthy();
    expect(vi.mocked(api.get).mock.calls.map((c) => c[0])).toContain(
      `/api/v1/dashboard/day-peek?date=${tomorrow}`,
    );
  });

  it("J9 · 月历翻月（‹/› 与标题联动）", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) return {};
      return summary(["calendar", "todo", "diary", "review"].map((id) => okProv(id)));
    });
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getByTestId("month-grid")).toBeTruthy());
    const d = new Date();
    expect(screen.getByText(`${d.getFullYear()}年${d.getMonth() + 1}月`)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("下个月"));
    const next = new Date(d.getFullYear(), d.getMonth() + 1, 1);
    expect(screen.getByText(`${next.getFullYear()}年${next.getMonth() + 1}月`)).toBeTruthy();
  });

  it("J10 · day-peek 失败 → 「暂时不可用」（独立于今日摘要）", async () => {
    const d = new Date();
    const tomorrow = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate() + 1).padStart(2, "0")}`;
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) return {};
      if (url.startsWith("/api/v1/dashboard/day-peek")) {
        throw new ApiError({ type: "about:blank", title: "500", status: 500, detail: "boom" });
      }
      return summary(["calendar", "todo", "diary", "review"].map((id) => okProv(id)));
    });
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getByTestId("month-grid")).toBeTruthy());
    fireEvent.click(screen.getByTestId(`cal-day-${tomorrow}`));
    await waitFor(() => expect(screen.getByTestId("day-peek-err")).toBeTruthy());
  });

  it("J11 · 形状归一：源 data.items 为 string[]（diary 现状）→ 渲染为条目", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) return {};
      return summary([
        okProv("calendar"),
        okProv("todo"),
        okProv("diary", { title: "今日日记", items: ["记了日程"] }),
        okProv("review"),
      ]);
    });
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getByText("记了日程")).toBeTruthy());
    expect(screen.getByText("今日日记")).toBeTruthy();
  });

  it("J12 · 形状归一：源 data.items 为 object[]（title 键，非规范 text）→ 渲染为条目", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) return {};
      return summary([
        okProv("calendar"),
        okProv("todo"),
        okProv("diary"),
        okProv("review", { title: "今日复盘", items: [{ title: "复盘了本周" }] }),
      ]);
    });
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getByText("复盘了本周")).toBeTruthy());
  });

  it("J13 · BFF 401 → 面板级「未登录 · 请重新登录」（V4-01：不再混为暂时不可用）", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) return {};
      throw new ApiError({ type: "about:blank", title: "401", status: 401, detail: "unauthorized" });
    });
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getByTestId("today-summary-auth")).toBeTruthy());
    // r6 起 401 上移到面板级：一条提示，不逐分区重复
    expect(screen.getAllByText("未登录 · 请重新登录").length).toBe(1);
    expect(screen.queryByText("暂时不可用")).toBeNull();
  });

  it("J14 · 新插件声明 today-summary ⇒ 分区自动出现（r6 数据驱动验收）", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/dashboard/day-dots")) return {};
      return summary([
        ...["calendar", "todo", "diary", "review"].map((id) => okProv(id)),
        {
          id: "mood",
          label: "心情",
          status: "ok",
          data: { title: "", items: [{ text: "今天很开心" }] },
        },
      ]);
    });
    const { el } = wrapper();
    render(el);
    fireEvent.click(screen.getByLabelText(/当前时间/));
    await waitFor(() => expect(screen.getByTestId("today-sum-mood")).toBeTruthy());
    expect(screen.getByText("心情")).toBeTruthy();
    expect(screen.getByText("今天很开心")).toBeTruthy();
  });
});
